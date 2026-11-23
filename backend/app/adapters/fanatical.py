"""Fanatical.

Fanatical (formerly Bundle Stars) is a key shop, and now and then it gives a
Steam key away: a product whose price is zero for the length of a promotion,
claimed by putting it in the cart and checking out a zero-total order. The key
then sits in the account's "Order History & Keys". Most of what Fanatical calls
"free" is *not* that - "free with every purchase over $15", a spin-to-win with
a minimum spend, a coupon that comes with a bundle - and none of it is claimed
here, because all of it costs money.

**Discovery is sessionless, and was measured (23 Nov 2026, plain httpx,
Trove's own user agent, requests 3 s apart):**

    GET https://www.fanatical.com/api/algolia/key
      -> 200 {"key": "<base64 secured key>", "validUntil": <ms epoch>}

The key is a secured Algolia search key minted per visitor. Decoded, it
carries its own restrictions: `disabled = 0`, the availability window
(`available_valid_from <= now`, `available_valid_until = 0 OR > now`), a
`facetFilters=["included_regions:<country of the caller's IP>"]` (NO from
here) and a list of the `fan*` indices it may read. So the region question
answers itself: what the key can see is what this address may buy.

    POST https://w2m9492ddv-dsn.algolia.net/1/indexes/fan/query
      X-Algolia-Application-Id: W2M9492DDV, X-Algolia-API-Key: <key>
      {"params": "query=&hitsPerPage=50&filters=giveaway = 1"}
      -> 200 {"hits": [...], "nbHits": N, ...}

Application id `W2M9492DDV` and index `fan` are the store's own (the front
end's bundle, `"algolia":{"indexName":"fan"}`). A hit carries `product_id`
(the stable catalogue id - the offer's identity), `objectID`, `name`, `slug`,
`type` and `display_type` (game / dlc / bundle / competition / ...), `price`
and `fullPrice` as `{"GBP": .., "EUR": .., "USD": .., ...}` in currency units,
`discount_percent`, `drm` (`["steam"]`), `cover`, `free_to_play`, `presale`,
`mystery`, `pay_what_you_want`, `available_valid_from/until` (unix seconds,
0 = open) and **`giveaway`** - a boolean on the hit, and numerically
filterable: measured, `giveaway = 0` matched all 16585 records and
`giveaway = 1` matched **none**, so the filter works and no giveaway was live
that day. (`giveaway:true` / `giveaway:false` both match nothing - it is not a
facet; use the numeric form.)

**A price of zero is not a giveaway.** `price.USD = 0` matched 72 records that
day, and they were unreleased games with no price yet (Warhammer 40,000:
Mechanicus II, Internet Cafe Simulator 3, ...), free-to-play listings (World
of Tanks, `free_to_play: true`) and a prize draw ("Black Friday JSAUX
Accessories Giveaway", `display_type: "competition"`, `giveaway: false`).
Hence the conditions in `_parse_hit`: `giveaway` true AND every listed price
zero AND not free-to-play, mystery, pay-what-you-want or a competition.

**"Free with purchase" is a different list, and is excluded twice.**

    GET https://www.fanatical.com/api/all-promotions/en
      -> 200 {"displaySales", "saleRecords", "deliveries", "freeProducts",
              "bundleFest", "vouchers"}

`freeProducts` is the "free gift with your order" tiers: each has
`min_spend` per currency (Black Friday Spin to Win: 1500 = $15.00) and/or
`required_products` (a coupon that comes with a named bundle), and
`products[]` whose own `price` can read 0. Measured: four tiers, every one
with a minimum spend or a required product. They are not `giveaway = 1`
products, but a giveaway hit whose slug also appears in a tier is dropped
anyway, and so is any name that says "with any/every purchase". That request
is only made when there is a candidate to check.

`/api/products-group/<slug>/en` (what a product page loads) answers **403
"Request blocked"** to Trove's user agent; discovery does not need it. The
claim reads it from inside the signed-in page, where it is the page's own
request.

**The checkout, read from the store's own bundle (not run - no account
here).** The cart page (`/en/cart`) lists items under `.checkout-items`, each
with a `.name-container`, and a summary whose `Total:` is
`.checkout-total .summary-number` (plus "You will be charged" when the
currency is converted). With exactly one giveaway game in the cart the button
is a "Proceed to checkout" link to `/cart?upsell=true` - an upsell page of
paid games with a "Skip and Proceed To Checkout" button (`#api-button`);
otherwise `#api-button` reads "Proceed To Checkout". The store's
`createPayment` then checks, in order: signed in, an Epic link for Epic
products, **the newsletter** for a `giveaway_newsletter` product, **Steam**
for `giveaway_steam` / `giveaway_wishlist_on_steam` / `giveaway_steam_sync`,
then creates the order; when every item is a giveaway and the payment total
is 0 it calls `POST /api/user/payment/giveaway-order {orderId}` and goes to
`/receipt?authResult=...`. The receipt says "THANK YOU FOR YOUR ORDER" and
links "View Order and Keys" to the order, where "Reveal Key" shows the key in
a read-only `input[aria-label="reveal-key"]`. Error text, verbatim: "There was
a problem claiming this promotion - please try again or contact support";
"The giveaway in your cart is only available to newsletter subscribers...".

**Two rules this adapter keeps that the store's own page would not:**

1. **The money guard.** Before anything goes in, the cart must be empty (or
   hold only this offer, left by an earlier attempt). After adding, it must
   hold exactly this one item and a total that reads as zero, or nothing is
   clicked. Trove never removes the person's items and never checks out a
   cart with anything else in it - a basket is shared state (see Steam). A
   redirect to `/payment` or `/billing` stops the run: a free order never
   goes there.
2. **No marketing consent.** The cart's "Sign me up for gaming deals and
   more!" box is *pre-ticked* outside some regions (the bundle sets it on by
   default unless a regional flag says otherwise). Submitting it ticked would
   be Trove subscribing the person, so a ticked box is unticked before the
   checkout. A giveaway that *requires* the newsletter is `not_eligible`
   with a sentence - Trove never opts in for anybody.

**Everything signed-in is written, not verified**: the product page's add
button, the cart markup in a live browser, the receipt, the order page and
the key reveal. Every step fails safe - unreadable ownership is `None`, an
unrecognised page is `NeedsAttention` with a screenshot, a missing control is
`not_eligible` - and the selector tables below are the one place to fix what
the first real giveaway disproves.

The login form (from the bundle): email and password on one page with a
"remember me" option, reCAPTCHA v3, and possible two-factor or
"suspicious country" steps. Nobody has looked at it here, so there is no
"Sign in for me" sequence.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import httpx
from playwright.async_api import Page, Response, TimeoutError as PlaywrightTimeout

from ..browser import (
    CheckoutBlocked,
    NeedsAttention,
    StoreChallenged,
    StoreUnavailable,
    refusal_reason,
    screenshot,
    screenshot_name,
)
from .base import BaseAdapter, ChallengeWaiter, ClaimResult, FreeOffer, Requirement

logger = logging.getLogger(__name__)

STORE_ROOT = "https://www.fanatical.com"
LOCALE = "en"
USER_AGENT = "Trove/0.1 (self-hosted free game claimer)"

# Sessionless discovery: the store's own search, read with the key its front
# end gets. All measured (module docstring).
ALGOLIA_KEY_URL = f"{STORE_ROOT}/api/algolia/key"
ALGOLIA_APP_ID = "W2M9492DDV"
ALGOLIA_QUERY_URL = f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/fan/query"
GIVEAWAY_FILTER = "giveaway = 1"
PROMOTIONS_URL = f"{STORE_ROOT}/api/all-promotions/{LOCALE}"
IMAGE_ROOT = "https://cdn-ext.fanatical.com/production/product/1280x720"
POLITE_DELAY_S = 3.0

CART_URL = f"{STORE_ROOT}/{LOCALE}/cart"
ORDERS_URL = f"{STORE_ROOT}/{LOCALE}/orders"

# The kinds of product this adapter claims. A giveaway bundle is a tiered
# product whose free part is tier 0; nobody has seen one, so it is skipped
# with a log line rather than guessed at.
CLAIMABLE_TYPES = {"game", "dlc"}

# A hit's `drm` word -> the ledger's `key_store` word.
DRM_STORE = {
    "steam": "steam",
    "epicgames": "epic",
    "gog": "gog",
    "uplay": "ubisoft",
    "origin": "ea",
    "eaplay": "ea",
    "microsoft": "microsoft",
    "xbox": "microsoft",
    "rockstar": "rockstar",
    "bethesda": "bethesda",
}

FREE_WITH_PURCHASE = re.compile(r"with (any|every|your) (purchase|order)|free gift|spend", re.I)


# ── The selectors ───────────────────────────────────────────────────────────
#
# Read from the store's own bundle (class names and default message strings),
# not from a rendered signed-in page. UNVERIFIED until the first real claim.

CHALLENGE = [
    'iframe[src*="recaptcha/api2/bframe"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[title*="challenge" i]',
    'text=/verify you are human/i',
    'text=/security check/i',
]

# The account area: signed in, the orders page renders this heading.
SIGNED_IN = [
    ".orders-and-keys",
    'h1:has-text("Order History & Keys")',
    'h2:has-text("Order History & Keys")',
]
SIGNED_OUT = [
    'input[type="password"]',
    'a[href*="/account/login"]',
]

# The product's own add button, in the page header. The sticky and docked
# copies are the same button; `visible=true` takes whichever is showing.
ADD_TO_CART = [
    '.ProductHeaderButtons button:has-text("Add to cart")',
    '.ProductHeaderButtons [role="button"]:has-text("Add to cart")',
    '.ProductHeaderButtons button.btn-primary',
]
# Already bought on Fanatical. Card label "In Library" in the bundle; whether
# the product page shows it the same way is unverified, so it is only ever
# read as a yes.
IN_LIBRARY = [
    'text=/^\\s*In Library\\s*$/',
    'text=/already own/i',
]
# The cart's checkout control. The link form appears when the cart is one
# giveaway game and leads to the upsell page; `#api-button` is the button on
# both pages ("Proceed To Checkout" / "Skip and Proceed To Checkout").
PROCEED = [
    'a.btn-payment:has-text("Proceed to checkout")',
    '#api-button',
]
# The pre-ticked marketing opt-in in the cart summary.
NEWSLETTER_OPTIN = '#checkoutemailsignup'

RECEIPT_OK = [
    'text=/thank you for your order/i',
    'text=/your order is complete/i',
]
RECEIPT_PROCESSING = [
    'text=/your order is being processed/i',
    'text=/flagged for manual authori[sz]ation/i',
    'text=/taking longer than usual/i',
]
RECEIPT_CANCELLED = [
    'text=/your order has been cancelled/i',
    'text=/your order has been refunded/i',
]
VIEW_KEYS = [
    'a:has-text("View Order and Keys")',
    'a:has-text("View Order & Keys")',
    'a:has-text("Orders History and Keys")',
]
REVEAL_KEY = ['button:has-text("Reveal Key")']
KEY_INPUT = ['input[aria-label="reveal-key"]']
VERIFY_EMAIL = ['text=/verify (your )?email/i']
CLAIM_ERROR = ['text=/problem claiming this promotion/i']

# What a refused giveaway says in the cart or in a modal, mapped to the
# reason code and the sentence the ledger shows. Verbatim fragments of the
# store's own messages (bundle); the first match wins.
REQUIREMENT_TEXT = [
    (
        re.compile(r"only available to newsletter subscribers|newsletter subscription", re.I),
        "newsletter_required",
        "This giveaway is only for Fanatical newsletter subscribers. Trove "
        "never signs anybody up for marketing; claim it yourself if you want "
        "the newsletter.",
    ),
    (
        re.compile(r"connected steam account|link your steam account", re.I),
        "steam_link_required",
        "This giveaway needs a Steam account connected to Fanatical. Connect "
        "it once in your Fanatical account and Trove will try again.",
    ),
    (
        re.compile(r"wishlist on steam|synced their steam wishlist|sync your steam wishlist", re.I),
        "steam_wishlist_required",
        "This giveaway is only for people who wishlisted the game on Steam "
        "(or synced their wishlist). That is yours to do; Trove will not.",
    ),
    (
        re.compile(r"not available for your region", re.I),
        "region_locked",
        "Fanatical says this giveaway is not available in your region.",
    ),
    (
        re.compile(r"link your epic games account", re.I),
        "epic_link_required",
        "This giveaway is delivered to an Epic Games account, and none is "
        "linked to Fanatical. Link it once and Trove will try again.",
    ),
]


async def _goto(page: Page, url: str) -> Response | None:
    """Navigate, tolerating a navigation the SPA replaced under us."""
    try:
        return await page.goto(url, wait_until="domcontentloaded")
    except PlaywrightTimeout:
        raise
    except Exception as exc:
        if "ERR_ABORTED" not in str(exc):
            raise
        logger.debug("Navigation to %s was superseded; reading where we landed.", url)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    return None


async def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 1500):
    """The first *visible* element matching any selector, in list order, or None.

    `>> visible=true` rather than `.first`: a locator's first match need not
    be the visible one (the itch.io lesson), and Fanatical renders the add
    button up to three times (header, sticky, docked).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while True:
        for selector in selectors:
            try:
                locator = page.locator(f"{selector} >> visible=true").first
                if await locator.is_visible():
                    return locator
            except Exception as exc:
                logger.debug("Selector %r did not resolve: %s", selector, exc)
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(0.1)


# ── Pure helpers (exercised by tools/fanatical_sim.py) ─────────────────────


def _prices_all_zero(price: object) -> bool:
    """Every currency the record lists is zero - and there is at least one."""
    if not isinstance(price, dict) or not price:
        return False
    try:
        return all(float(v) == 0 for v in price.values())
    except (TypeError, ValueError):
        return False


def _free_with_purchase_slugs(promotions: dict) -> set[str]:
    """Slugs Fanatical gives away only alongside something that costs money.

    Every `freeProducts` tier, whatever it says: one with a minimum spend or a
    required product is plainly "with purchase", and one with neither still
    rides on an order of something else (there is nothing to check out with
    an empty cart). So all of them are excluded, which is the safe side.
    """
    slugs: set[str] = set()
    for tier in (promotions or {}).get("freeProducts") or []:
        for product in tier.get("products") or []:
            slug = product.get("slug")
            if slug:
                slugs.add(slug)
    return slugs


def _parse_hit(hit: dict, excluded: set[str] | None = None) -> FreeOffer | None:
    """One Algolia record as a claimable giveaway, or None with a log line."""
    name = hit.get("name") or ""
    slug = hit.get("slug") or ""
    why = None
    if not hit.get("giveaway"):
        why = "not a giveaway"
    elif not _prices_all_zero(hit.get("price")):
        why = f"priced {hit.get('price')!r}"
    elif hit.get("free_to_play"):
        why = "free to play, nothing to keep"
    elif hit.get("mystery") or hit.get("pay_what_you_want"):
        why = "mystery or pay-what-you-want"
    elif hit.get("display_type") == "competition" or hit.get("type") == "competition":
        why = "a prize draw"
    elif (hit.get("type") or hit.get("display_type")) not in CLAIMABLE_TYPES:
        why = f"type {hit.get('type')!r} is not claimed here"
    elif excluded and slug in excluded:
        why = "free only with a purchase"
    elif FREE_WITH_PURCHASE.search(name):
        why = "free only with a purchase (by its name)"
    elif not slug or not (hit.get("product_id") or hit.get("objectID")):
        why = "no slug or id to identify it by"
    if why:
        logger.info("Fanatical: skipped %r (%s): %s.", name, slug, why)
        return None

    ptype = hit.get("type") or "game"
    drm = [d for d in (hit.get("drm") or []) if isinstance(d, str)]
    until = hit.get("available_valid_until") or 0
    since = hit.get("available_valid_from") or 0
    cover = hit.get("cover")
    return FreeOffer(
        external_id=str(hit.get("product_id") or hit.get("objectID")),
        title=name,
        url=f"{STORE_ROOT}/{LOCALE}/{ptype}/{slug}",
        image_url=f"{IMAGE_ROOT}/{cover}" if cover else None,
        kind="dlc" if ptype == "dlc" else "game",
        starts_at=_from_unix(since),
        ends_at=_from_unix(until),
        source="store",
        extra={
            "slug": slug,
            "type": ptype,
            "drm": drm,
            "key_store": next((DRM_STORE[d] for d in drm if d in DRM_STORE), None),
        },
    )


def _from_unix(value) -> datetime | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _is_zero_amount(text: str | None) -> bool | None:
    """Does a displayed amount read as zero? None when it is not an amount.

    "$0.00", "£0.00", "0,00 €", "FREE" are zero; anything with a non-zero
    digit is not; text with no digits at all (and no "free") is unreadable,
    which the money guard treats as "not proven zero".
    """
    if text is None:
        return None
    cleaned = " ".join(text.split())
    digits = re.findall(r"\d", cleaned)
    if not digits:
        return True if re.fullmatch(r"(?i)\s*free\s*", cleaned) else None
    return all(d == "0" for d in digits)


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _names_match(cart_name: str, title: str) -> bool:
    a, b = _norm(cart_name), _norm(title)
    return bool(a and b) and (a == b or a in b or b in a)


def _requirement(texts: list[str]) -> tuple[str, str] | None:
    """A giveaway condition only a person can meet, from the page's messages."""
    blob = " ".join(texts or [])
    for pattern, code, sentence in REQUIREMENT_TEXT:
        if pattern.search(blob):
            return code, sentence
    return None


def _cart_verdict(cart: dict | None, title: str, *, before_add: bool) -> tuple[str, str | None]:
    """What the cart allows, as (state, sentence).

    States: "empty" (nothing in it), "ours" (exactly this offer), "proceed"
    (exactly this offer at a zero total - the only state a checkout may
    follow), "mixed" (anything else in it), "priced" (this offer, but the
    total is not provably zero), "unreadable". `before_add` asks only whether
    adding is allowed; after adding, "ours" is upgraded to "proceed" or
    "priced" by the total.
    """
    if not isinstance(cart, dict):
        return "unreadable", "Trove could not read the Fanatical cart."
    items = cart.get("items")
    if not isinstance(items, list):
        if cart.get("empty"):
            return "empty", None
        return "unreadable", "Trove could not read the Fanatical cart."
    if not items:
        return ("empty", None) if (cart.get("empty") or before_add) else (
            "unreadable", "The Fanatical cart looked empty after the giveaway was added."
        )
    others = [i for i in items if not _names_match(str(i), title)]
    if len(items) > 1 or others:
        return "mixed", (
            "Your Fanatical cart has other things in it ("
            + ", ".join(str(i) for i in items[:4])
            + "). Trove never checks out a cart with anything but the free "
            "game in it, and never removes your items - empty the cart, or "
            "claim this one yourself."
        )
    if before_add:
        return "ours", None
    totals = [cart.get("total"), cart.get("charged")]
    readings = [_is_zero_amount(t) for t in totals if t is not None]
    if readings and all(r is True for r in readings):
        return "proceed", None
    return "priced", (
        f"The cart total reads {cart.get('total')!r}, not zero, so Trove did "
        "not check out. Nothing was paid."
    )


# The cart, read in one pass. Returns names rather than elements so the
# verdict above is a pure function of what the page showed.
_READ_CART_JS = """
() => {
  const text = el => el ? (el.innerText || el.textContent || '').trim() : null;
  const list = document.querySelector('.checkout-items');
  const empty = !!document.querySelector('.empty-cart');
  let items = null;
  if (list) {
    let names = [...list.querySelectorAll('.checkout-card-item-container .name-container')];
    if (!names.length) names = [...list.querySelectorAll('.name-container')];
    items = names.map(text).filter(Boolean);
  }
  const total = text(document.querySelector('.checkout-total .summary-number'));
  const charged = [...document.querySelectorAll('.summary-line-container')]
    .filter(el => /will be charged/i.test(el.innerText || ''))
    .map(el => text(el.querySelector('.summary-number')))[0] || null;
  const warnings = [...document.querySelectorAll('.item-message, .notifications, .modal-content')]
    .map(text).filter(Boolean);
  const optin = document.querySelector('#checkoutemailsignup');
  return { items, empty, total, charged, warnings, optin_checked: optin ? !!optin.checked : null };
}
"""

# The product record, fetched by the page itself from its own origin - the
# same request the product page makes. Read for the giveaway's conditions.
_READ_PRODUCT_JS = """
async (slug) => {
  try {
    const r = await fetch(`/api/products-group/${slug}/en`, { credentials: 'include' });
    if (!r.ok) return { status: r.status };
    const d = await r.json();
    const p = (d && d.products && d.products[0]) || d || {};
    const pick = {};
    for (const k of ['name', 'slug', 'type', 'price', 'giveaway', 'giveaway_newsletter',
                     'giveaway_steam', 'giveaway_wishlist_on_steam', 'giveaway_steam_sync',
                     'free_to_play', 'presale', 'sold_out', 'drm']) pick[k] = p[k];
    return { status: r.status, product: pick };
  } catch (e) { return { error: String(e) }; }
}
"""


def _product_conditions(product: dict | None) -> tuple[str, str, str] | None:
    """A refusal read off the product record before anything is added.

    Returns (outcome, reason_code, sentence) or None to carry on. Unknown
    fields carry on: the cart's own messages are the second check.
    """
    if not isinstance(product, dict):
        return None
    if product.get("giveaway") is False or (
        product.get("price") is not None and not _prices_all_zero(product.get("price"))
    ):
        return ("failed", "not_free",
                "Fanatical no longer lists this as a free giveaway - it has "
                "probably ended. Nothing was added to the cart.")
    if product.get("giveaway_newsletter"):
        code, sentence = REQUIREMENT_TEXT[0][1], REQUIREMENT_TEXT[0][2]
        return "not_eligible", code, sentence
    if product.get("giveaway_wishlist_on_steam") or product.get("giveaway_steam_sync"):
        return "not_eligible", REQUIREMENT_TEXT[2][1], REQUIREMENT_TEXT[2][2]
    return None


def _slug(offer: FreeOffer) -> str | None:
    slug = offer.extra.get("slug")
    if slug:
        return str(slug)
    path = urllib.parse.urlparse(offer.url or "").path.rstrip("/")
    return path.rpartition("/")[2] or None


def _key_store(offer: FreeOffer, product: dict | None) -> str | None:
    if offer.extra.get("key_store"):
        return offer.extra["key_store"]
    drm = (product or {}).get("drm")
    if isinstance(drm, dict):
        for word, on in drm.items():
            if on and word in DRM_STORE:
                return DRM_STORE[word]
    if isinstance(drm, list):
        for word in drm:
            if word in DRM_STORE:
                return DRM_STORE[word]
    return None


_KEY_SHAPE = re.compile(r"[A-Z0-9]{4,}(?:-[A-Z0-9]{4,}){1,7}", re.I)


class FanaticalAdapter(BaseAdapter):
    store = "fanatical"
    display_name = "Fanatical"
    login_url = f"{STORE_ROOT}/{LOCALE}/"
    signin_url = f"{STORE_ROOT}/{LOCALE}/account/login"
    # No sign_in_forms: the form is known from the bundle (email + password on
    # one page, reCAPTCHA v3, "remember me"), but nobody has looked at it.
    sign_in_note = (
        "Keep “remember me” on when you sign in, or the session will "
        "not survive a restart."
    )
    blurb = (
        "Claims Fanatical's occasional free Steam-key giveaways: puts the free "
        "game in an otherwise empty cart, checks out only at a zero total, and "
        "keeps the revealed key in the ledger. Never claims \"free with "
        "purchase\" offers, never touches a cart with your own items in it, and "
        "never signs you up for the newsletter - a giveaway that requires it is "
        "left for you."
    )
    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". Fanatical's login "
                "has a reCAPTCHA and may ask for a two-factor code."
            ),
        ),
        Requirement(
            name="An empty cart",
            description=(
                "Trove only checks out a cart holding nothing but the free "
                "game. If you leave things in your Fanatical cart, the claim "
                "stops and asks you instead."
            ),
        ),
        Requirement(
            name="A confirmed email address",
            description=(
                "Fanatical will not show a key until the account's email is "
                "confirmed."
            ),
            required=False,
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """Live giveaways from the store's own search; two requests when none.

        The search key is minted per visitor and region-scoped by address
        (module docstring), so what comes back is what this address may claim.
        """
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            key_response = await client.get(ALGOLIA_KEY_URL)
            key_response.raise_for_status()
            key = (key_response.json() or {}).get("key")
            if not key:
                logger.error("Fanatical's /api/algolia/key had no key in it.")
                return []
            await asyncio.sleep(POLITE_DELAY_S)
            params = urllib.parse.urlencode(
                {"query": "", "hitsPerPage": 50, "filters": GIVEAWAY_FILTER}
            )
            search = await client.post(
                ALGOLIA_QUERY_URL,
                headers={
                    "X-Algolia-Application-Id": ALGOLIA_APP_ID,
                    "X-Algolia-API-Key": key,
                },
                json={"params": params},
            )
            search.raise_for_status()
            body = search.json() or {}
            hits = body.get("hits")
            if not isinstance(hits, list):
                logger.error("Fanatical's search answered without a hits list: %r.", sorted(body))
                return []
            logger.info("Fanatical: %d record(s) flagged giveaway.", len(hits))
            if not hits:
                return []

            excluded: set[str] = set()
            try:
                await asyncio.sleep(POLITE_DELAY_S)
                promos = await client.get(PROMOTIONS_URL)
                promos.raise_for_status()
                excluded = _free_with_purchase_slugs(promos.json())
            except Exception as exc:
                # The giveaway flag is the primary test; this is a second
                # fence. Without it, the name check still stands.
                logger.warning("Fanatical: could not read all-promotions (%s).", exc)

        offers = [o for o in (_parse_hit(h, excluded) for h in hits) if o is not None]
        for offer in offers:
            logger.info("Fanatical giveaway: %r (%s), ends %s.", offer.title,
                        offer.external_id, offer.ends_at)
        return offers

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        response: Response | None = None
        try:
            response = await _goto(page, ORDERS_URL)
        except PlaywrightTimeout:
            return False, "Fanatical did not load in time."
        if await _first_visible(page, CHALLENGE):
            raise StoreChallenged(
                "Fanatical is showing a challenge that needs a person."
                " This is the store judging Trove's own browser, not your"
                " account, so the session behind it may be fine - Trove has"
                " left it alone and will try again on the next run."
            )
        if await _first_visible(page, SIGNED_IN, timeout_ms=6000):
            return True, "Signed in."
        if "/login" in (page.url or "") or await _first_visible(page, SIGNED_OUT):
            return False, "This account is signed out. Sign in again with \"Sign in here\"."
        refused = refusal_reason("Fanatical", response.status if response else None)
        if refused:
            raise StoreUnavailable(refused)
        logger.info("Fanatical health: neither marker on %s.", page.url)
        return False, "Could not tell whether this account is signed in."

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        """Only ever a yes: "In Library" on the product page. Otherwise None."""
        if not offer.url:
            return None
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            return None
        if await _first_visible(page, IN_LIBRARY, timeout_ms=4000):
            return True
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    def checkout_url(self, offer: FreeOffer) -> str | None:
        """The cart, where the free game is waiting when a captcha stopped it."""
        return CART_URL

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        slug = _slug(offer)
        if not offer.url or not slug:
            return ClaimResult(outcome="failed", detail="This offer has no product page to open.")

        # 1. The cart first: nothing goes into a cart that holds the person's
        #    own things.
        cart = await self._open_cart(page, offer)
        state, sentence = _cart_verdict(cart, offer.title, before_add=True)
        if state in ("mixed", "unreadable"):
            raise NeedsAttention(sentence or "", await self._shot(page, offer, f"cart-{state}"))
        already_in_cart = state == "ours"

        # 2. The product page, and the giveaway's own conditions.
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The giveaway's page did not load.", await self._shot(page, offer, "product-timeout")
            ) from None
        await self._guard(page, offer)
        product = await self._read_product(page, slug)
        refusal = _product_conditions(product)
        if refusal and not already_in_cart:
            outcome, code, sentence = refusal
            return ClaimResult(outcome=outcome, detail=sentence, reason_code=code)
        if await _first_visible(page, IN_LIBRARY, timeout_ms=1500):
            return ClaimResult(outcome="already_owned", detail="Already in your Fanatical library.")

        # 3. Add it, once.
        if not already_in_cart:
            button = await _first_visible(page, ADD_TO_CART, timeout_ms=8000)
            if button is None:
                return ClaimResult(
                    outcome="not_eligible",
                    detail=(
                        "The giveaway's page has no \"Add to cart\" button Trove "
                        "recognises (it may have ended, be region-locked, or be "
                        "behind an age check). Nothing was added."
                    ),
                    reason_code="no_claim_control",
                    screenshot=await self._shot(page, offer, "no-add-button"),
                )
            logger.info("Fanatical: adding %r to the cart.", offer.title)
            try:
                await button.click(timeout=5000)
            except PlaywrightTimeout:
                await self._guard(page, offer)
                raise NeedsAttention(
                    "The \"Add to cart\" button could not be clicked.",
                    await self._shot(page, offer, "add-blocked"),
                ) from None
            await page.wait_for_timeout(2500)
            await self._guard(page, offer)

        # 4. The cart again, and the money guard.
        cart = await self._open_cart(page, offer)
        return await self._checkout_from_cart(page, offer, cart, product)

    async def _checkout_from_cart(
        self, page: Page, offer: FreeOffer, cart: dict | None, product: dict | None
    ) -> ClaimResult:
        """Everything from a read cart to a claimed row. The only checkout path."""
        requirement = _requirement((cart or {}).get("warnings") or [])
        state, sentence = _cart_verdict(cart, offer.title, before_add=False)
        if state != "proceed":
            raise NeedsAttention(sentence or "", await self._shot(page, offer, f"cart-{state}"))
        if requirement:
            code, why = requirement
            return ClaimResult(
                outcome="not_eligible",
                detail=why + " The free game was left in your cart.",
                reason_code=code,
            )

        if (cart or {}).get("optin_checked"):
            # Pre-ticked by the store. Leaving it would be Trove subscribing
            # the person to marketing; untick it, and stop if it will not go.
            optin = await _first_visible(page, [NEWSLETTER_OPTIN], timeout_ms=1000)
            try:
                if optin is not None:
                    await optin.uncheck(timeout=3000)
            except Exception:
                pass
            again = await self._read_cart(page)
            if (again or {}).get("optin_checked"):
                raise NeedsAttention(
                    "Fanatical's cart has the newsletter sign-up ticked and "
                    "Trove could not untick it, so it did not check out - "
                    "Trove never signs anybody up.",
                    await self._shot(page, offer, "optin-stuck"),
                )
            state, sentence = _cart_verdict(again, offer.title, before_add=False)
            if state != "proceed":
                raise NeedsAttention(sentence or "", await self._shot(page, offer, f"cart-{state}"))

        proceed = await _first_visible(page, PROCEED, timeout_ms=5000)
        if proceed is None:
            raise NeedsAttention(
                "Could not find Fanatical's \"Proceed to checkout\" control. "
                "Run and watch to read the real label; it goes in PROCEED.",
                await self._shot(page, offer, "no-proceed"),
            )
        logger.info("Fanatical: cart is %r at %r; checking out.", offer.title, cart.get("total"))
        await proceed.click(timeout=5000)
        await page.wait_for_timeout(2000)

        # The single-giveaway cart goes through the upsell page first. Only
        # its skip button is ever pressed - never an "Add To Cart" there.
        if "upsell" in (page.url or ""):
            await self._guard(page, offer)
            skip = await _first_visible(page, ["#api-button"], timeout_ms=6000)
            if skip is None:
                raise NeedsAttention(
                    "Fanatical's upsell page had no \"Skip and Proceed\" button "
                    "Trove recognises.",
                    await self._shot(page, offer, "no-skip"),
                )
            await skip.click(timeout=5000)

        return await self._read_order(page, offer, product)

    async def _read_order(self, page: Page, offer: FreeOffer, product: dict | None) -> ClaimResult:
        """After the checkout click: only read, never click checkout again."""
        for _ in range(20):
            await page.wait_for_timeout(1000)
            url = page.url or ""
            await self._guard(page, offer)
            if "/payment" in url or "/billing" in url:
                raise NeedsAttention(
                    "Fanatical asked for payment or billing details, which a "
                    "free order never needs. Trove stopped there; nothing was "
                    "entered or paid. Check the cart.",
                    await self._shot(page, offer, "payment-page"),
                )
            if "/login" in url:
                raise NeedsAttention(
                    "Fanatical asked to sign in at checkout. Sign in again with "
                    "\"Sign in here\".",
                    await self._shot(page, offer, "signed-out"),
                )
            if "/receipt" in url:
                if await _first_visible(page, RECEIPT_CANCELLED, timeout_ms=800):
                    return ClaimResult(
                        outcome="failed",
                        detail="Fanatical cancelled the order.",
                        screenshot=await self._shot(page, offer, "cancelled"),
                    )
                if await _first_visible(page, RECEIPT_OK + RECEIPT_PROCESSING, timeout_ms=800):
                    return await self._collect_key(page, offer, product)
                continue
            try:
                body = (await page.inner_text("body"))[:6000]
            except Exception:
                body = ""
            requirement = _requirement([body])
            if requirement:
                code, why = requirement
                return ClaimResult(
                    outcome="not_eligible",
                    detail=why + " The free game was left in your cart.",
                    reason_code=code,
                    screenshot=await self._shot(page, offer, code),
                )
            if await _first_visible(page, CLAIM_ERROR, timeout_ms=300):
                raise NeedsAttention(
                    "Fanatical answered \"There was a problem claiming this "
                    "promotion\". One attempt only; check the account.",
                    await self._shot(page, offer, "claim-error"),
                )
        raise NeedsAttention(
            "The checkout was pressed but Fanatical never showed a receipt "
            "Trove recognises. Check Order History & Keys before it tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    async def _collect_key(self, page: Page, offer: FreeOffer, product: dict | None) -> ClaimResult:
        """The order went through; fetch the key if Fanatical will show it."""
        key_store = _key_store(offer, product)
        where = "Fanatical's Order History & Keys"
        fallback = ClaimResult(
            outcome="claimed",
            detail=f"Claimed. The key is in {where}; Trove could not read it there.",
            key_store=key_store,
        )
        link = await _first_visible(page, VIEW_KEYS, timeout_ms=5000)
        target = None
        if link is not None:
            try:
                href = await link.get_attribute("href")
                target = urllib.parse.urljoin(STORE_ROOT, href) if href else None
            except Exception:
                target = None
        try:
            await _goto(page, target or ORDERS_URL)
        except PlaywrightTimeout:
            return fallback
        if target is None:
            # The orders list, not the order: the newest order is the one
            # just made, but opening it is a guess too far. Say where it is.
            return fallback
        if await _first_visible(page, VERIFY_EMAIL, timeout_ms=2000):
            return ClaimResult(
                outcome="claimed",
                detail=(
                    f"Claimed. Fanatical will show the key in {where} once the "
                    "account's email address is confirmed."
                ),
                key_store=key_store,
            )
        reveal = await _first_visible(page, REVEAL_KEY, timeout_ms=6000)
        if reveal is not None:
            try:
                await reveal.click(timeout=5000)
                await page.wait_for_timeout(2000)
            except Exception:
                return fallback
        code = await self._read_key(page)
        if code is None:
            return fallback
        logger.info("Fanatical: %r claimed with a key for %s.", offer.title, key_store or "?")
        return ClaimResult(
            outcome="claimed",
            detail="Claimed as a key" + (f" for {key_store.title()}" if key_store else "")
            + "; the code is in the ledger.",
            key_code=code,
            key_store=key_store,
        )

    async def _read_key(self, page: Page) -> str | None:
        field = await _first_visible(page, KEY_INPUT, timeout_ms=4000)
        if field is None:
            return None
        try:
            value = ((await field.input_value()) or "").strip()
        except Exception:
            return None
        found = _KEY_SHAPE.search(value)
        return found.group(0) if found else None

    # ── Shared ───────────────────────────────────────────────────────────

    async def _open_cart(self, page: Page, offer: FreeOffer) -> dict | None:
        try:
            await _goto(page, CART_URL)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The Fanatical cart did not load.", await self._shot(page, offer, "cart-timeout")
            ) from None
        await self._guard(page, offer)
        await page.wait_for_timeout(2500)  # the SPA fills the cart after load
        return await self._read_cart(page)

    async def _read_cart(self, page: Page) -> dict | None:
        try:
            cart = await page.evaluate(_READ_CART_JS)
        except Exception as exc:
            logger.info("Fanatical: could not read the cart (%s).", exc)
            return None
        logger.info("Fanatical cart: %r", cart)
        return cart

    async def _read_product(self, page: Page, slug: str) -> dict | None:
        try:
            result = await page.evaluate(_READ_PRODUCT_JS, slug)
        except Exception as exc:
            logger.info("Fanatical: could not read the product record (%s).", exc)
            return None
        logger.info("Fanatical product record for %s: %r", slug, result)
        return (result or {}).get("product")

    async def _guard(self, page: Page, offer: FreeOffer) -> None:
        """A captcha goes to the un-driven window. Trove never solves one."""
        if await _first_visible(page, CHALLENGE, timeout_ms=700):
            raise CheckoutBlocked(
                "Fanatical put up a challenge. Finish this one claim by hand in "
                "the sign-in window - press “Finish the claim here”, "
                "which opens your Fanatical cart (add the game there if it is "
                "not in it yet).",
                await self._shot(page, offer, "challenge"),
                offer_id=offer.external_id,
            )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))
