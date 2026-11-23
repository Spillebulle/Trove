"""Humble Bundle: the Humble Store's free giveaways, and Humble Choice.

Two things live behind one Humble login, and this adapter takes both:

  (a) **Free store giveaways** - a normally-paid product on the Humble Store
      temporarily at zero, usually a Steam key. Rare.
  (b) **Humble Choice, for a subscriber** - this month's games, which must be
      redeemed in the library ("Reveal") before their keys exist. Keys expire
      (the site's own words: "Keys expire. Please reveal your key before
      %expirationDate."), so a month nobody looks at is a month that can be
      lost.

**Why one adapter, with `discovery_needs_session`.** Both halves are the same
Humble account: one login, one profile, one session. Two adapters would mean
two Trove accounts holding the same store login in two browser profiles - two
sessions for Humble to reconcile, two places to sign in, and the same
credentials stored twice, which is the "no credential sharing between
accounts" corollary in CLAUDE.md from the other side. Whether the account
subscribes at all can only be answered by a signed-in session, so the run
opens the session (Prime's contract extension) and `discover_in_session`
returns the store giveaways *and* this month's Choice games. The cost is that
every Humble run opens a browser even when nothing is free - one page load
every few hours, the same price Prime pays. `list_free_offers()` still answers
the store half sessionlessly, so the offers page and `/api/offers/refresh`
see Humble's giveaways without an account; the in-session list repeats them
from a 15-minute cache so a run does not ask twice.

**Measured, 23 Nov 2026, plain httpx from this machine, signed out.** The site
was rebuilt on Next.js (React Server Components): pages carry their data as
`self.__next_f.push([1, "..."])` flight chunks, not as a JSON API.

  * `GET /store/api/search?sort=bestselling&filter=all&request=1&page=0`
    -> **403, Cloudflare "Attention Required!" block page** - with Trove's own
    user agent *and* with a Chrome one. The old store search API is not
    usable. `robots.txt` also disallows `/search` and `/api/`, and the new
    search page is client-side Algolia, so the store's full search is not a
    route this adapter takes.
  * `GET /store`, `GET /on-sale`, `GET /` -> 200 HTML (Trove's own user agent
    accepted), ~3.9 MB / 1.0 MB / 0.56 MB. Their flight data holds the
    curated product feeds as
    `{"relationTo":"algolia-hit","value":{"id":"<uuid>","productKey":"<slug>",
    "url":"/store/<slug>","title":...,"thumbnail":...,"deliveryMethods":
    ["steam"],"displayPrice":{"currentPrice":{"type":"centPrecision",
    "centAmount":499,"currencyCode":"EUR","fractionDigits":2},"basePrice":
    {...},"isSale":false,"validUntil":"2026-12-03T18:00:00.000Z" | "$undefined",
    "sku":...},"productType":"storefront-game-product"}}`. 353 + 100 + 43
    unique products on the day, **none at zero**. `centAmount` can be a float
    (`1998.9999999999998` seen), so zero is tested as < 0.5. The price is
    geolocated (EUR here, `"country":"US"` beside it) - zero is zero anyway.
    The `id` UUID is the same on the product's own page (`storePage.id`), so
    it is the offer's identity; the slug is not.
  * `GET /store/<slug>` -> 200. Flight carries
    `"displayPrice":{"sku":...,"currentPrice":{"centAmount":499,...},
    "basePrice":"$a:..." (a reference),"isSale":false}`, `"userId":null`
    signed out, and the page's one buy control is a `<button>` whose label is
    a `<div>` reading **"Add to Cart"** next to "EUR4.99 incl. tax". So a
    purchase goes through the cart and `/checkout` (a route in the site's own
    route table) - and a cart is shared state (Steam's lesson): it may hold
    things the person put there. Hence the money rule below.
  * `GET /membership` -> 200 (Trove UA). Its "Choice Carousel" block lists
    this month's games: `{"id":"dysmantle_choice","productType":
    "humble-choice","title":"DYSMANTLE","deliveryMethods":["steam"],
    "endDate":"2027-06-01T00:00:00+00:00", ...}`. Ten on the day (November
    2026), including `"theouterworlds2_choice"` delivered `["blizzard"]`
    (Battle.net, needs a linked account), `"ignplus_choice_2026_11"`
    delivered `["external_key"]` (an IGN Plus subscription code, not a game -
    skipped), and a Steam playtest. The month is in the meta description:
    "Get November 2026 Humble Choice and more...".
  * `GET /membership/home` signed out -> **307 to /membership**.
    `GET /home/library` signed out -> 200 "Your Library" shell (the nav
    markup, including "Logout", is in the HTML either way - so page markers
    cannot tell signed-in from signed-out). Every page's flight carries
    `"user":null` and `"hasActiveSubscription":false` signed out.
  * `GET /api/auth/session-status` -> 200 `{"status":"anonymous"}` signed out.
    Humble's own client polls this and treats **any status other than
    "anonymous"** as signed in (read in its bundle: `if("anonymous"!==t)
    return;s.refresh()`); `health` uses the same rule.
  * Login is Ory Kratos at `auth.humblebundle.com`:
    `GET /self-service/login/browser` -> 303 to `/login?flow=<uuid>`, whose
    server-rendered form is **identifier-first**: one `identifier` field
    ("Enter your E-Mail"), a "Continue" button (`value="identifier_first"`),
    and Google/social buttons; no captcha script in the markup. The password
    step was not seen and focus was not measured, so no `sign_in_forms`.
  * The site's own strings (i18n in the flight, read verbatim) name the
    library flow: buttons "Reveal" / "Claim" / "Link", "Redeeming...",
    "Redeemed on {provider}", "Content redeemed successfully", a permanent
    "Select a Platform" dialog ("Platform selection is permanent"), error
    bodies ("Connect your {provider} account to redeem this.", "This game
    isn't available to redeem in your region.", ...), and the subscription's
    money traps: **"Pay to Unlock" / "Early Access" / "Pay Now"** ("Your games
    for the month are ready. If you continue, you will not be able to skip.").
    Account routes: `/home/library`, `/home/purchases`, `/membership/home`.

**Written and NOT verified** (no Humble account here): everything after a
sign-in. The subscriber signal (`"hasActiveSubscription":true` in the signed-in
flight; `/membership/home` not redirecting), the library card markup (found
by the title's text and its nearest ancestor holding a button), where a
revealed key renders, the post-click states, the checkout page's "Total" and
its order button's label, and the order confirmation (`/order/confirmation`
is in the route table). All of it fails safe: unknown ownership is `None`,
an unrecognised page is `NeedsAttention` with a screenshot, a missing control
is `not_eligible` with a sentence, and every step logs what it saw so the
first real run is the measurement.

**Money and subscription state.** Nothing here may charge or change a plan:

  * A free store product is ordered only after the checkout's own total has
    been read as zero, twice, immediately before the one click; anything
    unreadable or non-zero stops with nothing pressed. A checkout that
    mentions a recurring charge stops outright.
  * "Pay to Unlock", "Early Access", "Pay Now", skip, pause, cancel and plan
    changes are never touched. A month that is not unlocked yet is simply not
    claimable, and says so.
  * A newsletter or marketing opt-in is **never ticked**. A giveaway that
    requires one is `not_eligible` (`newsletter_optin`) with a sentence.
  * The permanent platform choice is the person's: if Humble asks, Trove
    presses Cancel and records `platform_choice`.
  * A linked-account delivery (Battle.net, Epic, ...) is never linked by
    Trove: `link_required`.

**Revealing a key binds it to the account.** On Humble, "Reveal" marks the key
as redeemed by this account; it cannot be gifted afterwards. That is exactly
what the person would do by hand, and it is what stops a Choice key expiring
unrevealed, so Trove does it - and records the key in the ledger, encrypted,
the way Prime's keys are. Trove never redeems a key on Steam or anywhere else.

**Terms.** Humble's terms prohibit "using any automated system, including
without limitation "robots," "spiders," "offline readers," etc., to access the
Service in a manner that sends more request messages to the Company servers
than a human can reasonably produce in the same period of time by using a
conventional on-line web browser", and its robots.txt goes further ("Use of
any robot, crawler, or other tool to scrape, harvest, extract, or retrieve any
content on this website using automated means is prohibited without written
permission"). Trove's cadence is a handful of page loads every few hours,
spaced `POLITE_DELAY_S` apart, on the person's own account - but automated
access is automated access, and the add-account page says so plainly.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import time
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
from .base import (
    BaseAdapter,
    ChallengeWaiter,
    ClaimResult,
    FreeOffer,
    Requirement,
)

logger = logging.getLogger(__name__)

ROOT = "https://www.humblebundle.com"
# The pages whose server-rendered flight carries curated product feeds. Not
# the search (robots-disallowed, client-side Algolia) and not the old search
# API (Cloudflare 403, measured). A giveaway is promoted, so it is expected on
# one of these; a free product featured on none of them would be missed, and
# that limit is stated rather than worked around.
STORE_PAGES = [f"{ROOT}/store", f"{ROOT}/on-sale", f"{ROOT}/"]
MEMBERSHIP_URL = f"{ROOT}/membership"
MEMBERSHIP_HOME_URL = f"{ROOT}/membership/home"
LIBRARY_URL = f"{ROOT}/home/library"
CHECKOUT_URL = f"{ROOT}/checkout"
SESSION_STATUS_URL = f"{ROOT}/api/auth/session-status"
# Ory's browser login: 303 to auth.humblebundle.com/login?flow=... (measured).
SIGNIN_URL = "https://auth.humblebundle.com/self-service/login/browser"

USER_AGENT = "Trove/0.1 (self-hosted free game claimer)"
POLITE_DELAY_S = 3.0
PUBLIC_CACHE_S = 15 * 60

CHOICE_PREFIX = "choice:"

# reason_code words (see ClaimResult). `newsletter_optin` and
# `no_claim_control` match the words other adapters already use.
REASON_NEWSLETTER = "newsletter_optin"
REASON_NO_CONTROL = "no_claim_control"
REASON_LINK = "link_required"
REASON_PLATFORM = "platform_choice"
REASON_REGION = "region_blocked"
REASON_STOCK = "out_of_stock"
REASON_EXPIRED = "expired"
REASON_NOT_UNLOCKED = "not_unlocked"
REASON_NOT_IN_LIBRARY = "not_in_library"
REASON_NOT_FREE = "not_free"
REASON_BASE_GAME = "base_game_unknown"

# Humble's delivery words (measured: "steam", "blizzard", "external_key") to
# the ledger's `key_store` words.
DELIVERY_STORE = {
    "steam": "steam",
    "blizzard": "battlenet",
    "battlenet": "battlenet",
    "epic": "epic",
    "gog": "gog",
    "ubisoft": "ubisoft",
    "origin": "ea",
    "ea": "ea",
}
# Deliveries that are not a game for the library: an IGN Plus subscription
# code was in November 2026's Choice as `external_key`. Skipped at discovery.
SKIP_DELIVERIES = {"external_key"}

MONTHS = (
    "january february march april may june july august september october "
    "november december"
).split()


# ── The selectors ───────────────────────────────────────────────────────────
#
# Text is the site's own (its i18n strings, read out of the flight data); the
# structure around it is UNVERIFIED - nobody has seen a signed-in page. Each
# list is tried in order through `_first_visible`.

CHALLENGE = [
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="turnstile"]',
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="arkoselabs"]',
    'iframe[title*="challenge" i]',
    'text=/verify you are human/i',
    'text=/performing security verification/i',
    'text=/attention required/i',
]

# A page asking for a login: the library's guest wording (measured string
# `redeemContentGuest`) and the Ory form's own field.
LOGIN_PROMPT = [
    'input[name="identifier"]',
    'button:has-text("Log In To Redeem")',
    'text=/sign in to redeem/i',
]

# The subscription's pay-early controls. Seen = this month is not unlocked;
# never clicked. Button-scoped for "Early Access" because a game can carry an
# "Early Access" tag too.
PAY_EARLY = [
    'button:has-text("Pay to Unlock")',
    'button:has-text("Pay Now")',
    'button:has-text("Early Access")',
    'text=/you will be charged today/i',
]

# The library's own name filter (measured placeholder "Search by name"). Not
# the header's store search, which navigates away on Enter.
LIBRARY_SEARCH = [
    'input[placeholder="Search by name"]',
    'input[aria-label="Search by name"]',
]

# Inside one library card.
CARD_REVEAL = [
    'button:has-text("Reveal")',
    'button:has-text("Claim")',
]
CARD_LINK = [
    'button:has-text("Link Account")',
    'button:has-text("Link")',
]

# Page-level, after the click.
PLATFORM_DIALOG = [
    'text=/platform selection is permanent/i',
    'text="Select a Platform"',
]
DIALOG_CANCEL = [
    '[role="dialog"] button:has-text("Cancel")',
    '[role="alertdialog"] button:has-text("Cancel")',
]
DIALOG = ['[role="alertdialog"]', '[role="dialog"]']
REDEEMED_TOAST = ['text=/content redeemed successfully/i']

# The product page.
ADD_TO_CART = [
    'button:has-text("Add to Cart")',
]
# A $0 product might offer a direct control instead of the cart. Unseen.
GET_FREE = [
    'button:has-text("Get for Free")',
    'button:has-text("Get it Free")',
    'button:has-text("Claim for Free")',
    'button:has-text("Get Free")',
]
# A marketing opt-in in the claim path. Never ticked.
NEWSLETTER_OPTIN = [
    'label:has-text("newsletter") input[type="checkbox"]',
    'label:has-text("marketing") input[type="checkbox"]',
    'label:has-text("email me") input[type="checkbox"]',
    '[role="checkbox"]:near(:text-matches("newsletter|marketing|promotional", "i"))',
    'text=/subscribe to (our|the|humble.s) newsletter/i',
    'text=/sign up for (our|the|humble.s) newsletter/i',
]

# The checkout's order button, only ever pressed after the total reads zero
# twice. Unseen labels, longest first.
PLACE_ORDER = [
    'button:has-text("Complete Purchase")',
    'button:has-text("Complete Order")',
    'button:has-text("Place Order")',
    'button:has-text("Get for Free")',
    'button:has-text("Claim for Free")',
]

# Card-text states, matched against the card's rendered text. The words are
# the site's own strings.
_KEY_RE = re.compile(r"\b[A-Z0-9]{4,6}(?:-[A-Z0-9]{4,6}){2,5}\b")
# "Redeemed on {provider}" is a done state, but the measured link alert reads
# "Key will be redeemed on {provider}." - which is the opposite. The
# lookbehind keeps the second from reading as the first (the sim pins it).
_REDEEMED_RE = re.compile(r"(?<!be )\bredeemed (on|to)\b", re.I)
_EXPIRED_RE = re.compile(r"\bkey expired on\b|\bexpired on\b|can no longer be redeemed", re.I)
_LINK_RE = re.compile(
    r"linked .{1,30} account is required|connect your .{1,30} account", re.I
)
_STOCK_RE = re.compile(
    r"temporarily unavailable in your region|out of stock|sold out|"
    r"aren.t available in your region",
    re.I,
)
_PROCESSING_RE = re.compile(r"redeeming\.\.\.|delivery in progress", re.I)

# The library's redemption error dialog bodies (verbatim i18n), most specific
# first, and what each one means. `None` code with outcome "attention" stops
# the run for a person.
_REDEEM_ERRORS: list[tuple[re.Pattern, str, str | None, str]] = [
    (re.compile(r"already in your .{1,30} library|already owns this on|has already been redeemed", re.I),
     "already_owned", None, "Already redeemed on this account."),
    (re.compile(r"already chosen a platform|selection already made", re.I),
     "already_owned", None, "A platform was already chosen for this game."),
    (re.compile(r"isn.t available to redeem in your region|not available in region", re.I),
     "not_eligible", REASON_REGION, "Humble says this game cannot be redeemed in your region."),
    (re.compile(r"out of stock|sold out", re.I),
     "not_eligible", REASON_STOCK, "Humble is out of keys for this game for now; Trove will look again."),
    (re.compile(r"connect your .{1,30} account to redeem", re.I),
     "not_eligible", REASON_LINK, "This game is delivered to a linked account that is not connected."),
    (re.compile(r"must already own the base game", re.I),
     "not_eligible", REASON_BASE_GAME, "Humble says the linked account must own the base game first."),
    (re.compile(r"does not meet the age requirement", re.I),
     "not_eligible", None, "The delivery account does not meet the game's age requirement."),
    (re.compile(r"expired and can no longer be redeemed", re.I),
     "not_eligible", REASON_EXPIRED, "This key has expired and can no longer be redeemed."),
    (re.compile(r"please sign in and try again", re.I),
     "signed_out", None, "Humble says this account is signed out."),
    (re.compile(r"unable to redeem|something went wrong|refresh the page", re.I),
     "attention", None, "Humble answered the reveal with an error."),
]


# ── Page helpers (module-level so the sim can point them at a fake) ────────


async def _goto(page: Page, url: str) -> Response | None:
    """Navigate, tolerating a navigation the page replaced (Epic's lesson)."""
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


async def _first_visible(page, selectors: list[str], timeout_ms: int = 1500):
    """The first *visible* match of any selector, in list order, or None.

    Works on a page or on a locator (a library card). `>> visible=true`, not
    `.first`: a locator's first match is not necessarily a visible one (the
    itch.io lesson in CLAUDE.md).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while True:
        for selector in selectors:
            try:
                locator = page.locator(f"{selector} >> visible=true").first
                if await locator.is_visible():
                    return locator
            except Exception as exc:  # a malformed selector is a bug, not a state
                logger.debug("Selector %r did not resolve: %s", selector, exc)
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(0.1)


async def _page_html(page: Page) -> str:
    try:
        return await page.content()
    except Exception as exc:
        logger.debug("Could not read the page's HTML: %s", exc)
        return ""


async def _body_text(page: Page) -> str:
    try:
        return await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return ""


async def _find_card(page: Page, title: str):
    """The library card for `title`: the nearest ancestor of the visible title
    text that holds a button. Unverified markup, so it asks the page for text
    rather than for class names."""
    try:
        text = page.get_by_text(title, exact=True)
        count = await text.count()
        for i in range(min(count, 6)):
            node = text.nth(i)
            if await node.is_visible():
                return node.locator("xpath=ancestor::*[.//button][1]")
    except Exception as exc:
        logger.debug("Looking for the library card of %r failed: %s", title, exc)
    return None


async def _card_text(card) -> str:
    """The card's rendered text plus any input values (a key may sit in one)."""
    parts: list[str] = []
    try:
        parts.append(await card.inner_text() or "")
    except Exception:
        pass
    try:
        values = await card.locator("input").evaluate_all(
            "els => els.map(e => e.value || '')"
        )
        parts.extend(v for v in values if v)
    except Exception:
        pass
    return "\n".join(parts)


# ── Parsing (pure, and pinned by tools/humble_sim.py) ──────────────────────


_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


def _flight(html: str) -> str:
    """The page's React Server Components payload, decoded to one string."""
    out: list[str] = []
    for chunk in _PUSH_RE.findall(html or ""):
        try:
            out.append(json.loads(f'"{chunk}"'))
        except ValueError:
            continue
    return "".join(out)


def _objects_after(text: str, marker: str, back_to_brace: bool = False) -> list[dict]:
    """Every JSON object found at `marker` in `text`.

    With `back_to_brace`, the object starts at the `{` that opens the marker's
    enclosing object (the marker is a key inside it); otherwise the object
    starts right after the marker.
    """
    decoder = json.JSONDecoder()
    found: list[dict] = []
    start = 0
    while True:
        i = text.find(marker, start)
        if i < 0:
            return found
        start = i + len(marker)
        at = text.rfind("{", 0, i + 1) if back_to_brace else start
        try:
            value, _end = decoder.raw_decode(text, at)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)


def _cents(price) -> float | None:
    if isinstance(price, dict):
        amount = price.get("centAmount")
        if isinstance(amount, (int, float)):
            return float(amount)
    return None


def _date(value) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("$"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_store_hits(html: str) -> list[dict]:
    """The product feed entries in a store page's flight data (measured shape)."""
    return [
        o for o in _objects_after(_flight(html), '{"relationTo":"algolia-hit","value":')
        if o.get("id")
    ]


def free_offers_from_hits(hits: list[dict]) -> list[FreeOffer]:
    """The temporarily-free store products among `hits`.

    Both halves: the current price is zero AND the product normally costs
    something (a positive base price, or the feed marks it a sale). A product
    that is zero with no sale and no base price is permanently free, which is
    not a giveaway. Bundles and anything not a `storefront-*` product are
    skipped: a bundle's price is pay-what-you-want, never a giveaway.
    """
    offers: list[FreeOffer] = []
    seen: set[str] = set()
    for hit in hits:
        pid = str(hit.get("id"))
        if pid in seen:
            continue
        seen.add(pid)
        if not str(hit.get("productType") or "").startswith("storefront"):
            continue
        price = hit.get("displayPrice") or {}
        current = _cents(price.get("currentPrice"))
        base = _cents(price.get("basePrice"))
        if current is None or current >= 0.5:
            continue
        if not ((base is not None and base >= 0.5) or price.get("isSale") is True):
            logger.info(
                "Humble: %r is zero but not on sale - permanently free, skipped.",
                hit.get("title"),
            )
            continue
        deliveries = [str(d).lower() for d in hit.get("deliveryMethods") or []]
        url = hit.get("url") or (f"/store/{hit['slug']}" if hit.get("slug") else None)
        if url and url.startswith("/"):
            url = ROOT + url
        offers.append(
            FreeOffer(
                external_id=pid,
                title=str(hit.get("title") or hit.get("productKey") or pid),
                url=url,
                image_url=hit.get("thumbnail") or hit.get("titleImageUrl"),
                kind="game",
                ends_at=_date(price.get("validUntil")) or _date(hit.get("endDate")),
                source="store",
                extra={
                    "humble": "store",
                    "product_key": hit.get("productKey"),
                    "sku": price.get("sku"),
                    "delivery": deliveries,
                },
            )
        )
    return offers


def parse_product_price(html: str) -> float | None:
    """The product page's own current price in cents (measured shape), or None."""
    # `"displayPrice":{"sku"...}` - decode from the brace after the key. The
    # feed entries' displayPrice starts with "currentPrice", so this key order
    # picks the page's own product.
    flight = _flight(html)
    i = flight.find('"displayPrice":{"sku"')
    if i < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(flight, i + len('"displayPrice":'))
    except ValueError:
        return None
    return _cents(value.get("currentPrice")) if isinstance(value, dict) else None


def parse_choice_items(html: str) -> list[dict]:
    """This month's Choice products in a membership page's flight data."""
    items: list[dict] = []
    seen: set[str] = set()
    for obj in _objects_after(_flight(html), '"productType":"humble-choice"', back_to_brace=True):
        cid = obj.get("id")
        if not cid or cid in seen or obj.get("productType") != "humble-choice":
            continue
        seen.add(cid)
        items.append(obj)
    return items


def parse_choice_month(html: str) -> str | None:
    """"2026-11" from "Get November 2026 Humble Choice" (measured meta text)."""
    found = re.search(
        r"\b(" + "|".join(MONTHS) + r")\s+(\d{4})\s+Humble Choice", html or "", re.I
    )
    if not found:
        return None
    return f"{found.group(2)}-{MONTHS.index(found.group(1).lower()) + 1:02d}"


def parse_subscriber(html: str) -> bool | None:
    """`hasActiveSubscription` from the page's flight (measured false signed
    out; true for a subscriber is the reasoning), or None if absent."""
    found = re.search(r'"hasActiveSubscription":(true|false)', _flight(html) or "")
    if not found:
        return None
    return found.group(1) == "true"


def choice_offers_from_items(items: list[dict], month: str) -> list[FreeOffer]:
    offers: list[FreeOffer] = []
    for item in items:
        deliveries = [str(d).lower() for d in item.get("deliveryMethods") or []]
        if deliveries and all(d in SKIP_DELIVERIES for d in deliveries):
            logger.info(
                "Humble Choice: %r is delivered as %s, not a game; skipped.",
                item.get("title"), deliveries,
            )
            continue
        cid = str(item["id"])
        offers.append(
            FreeOffer(
                external_id=f"{CHOICE_PREFIX}{month}:{cid}",
                title=str(item.get("title") or cid),
                url=LIBRARY_URL,
                image_url=item.get("thumbnail"),
                kind="game",
                ends_at=_date(item.get("endDate")),
                source="subscription",
                extra={"humble": "choice", "choice_id": cid, "month": month,
                       "delivery": deliveries},
            )
        )
    return offers


def total_is_zero(text: str) -> bool | None:
    """Whether a checkout's order total reads zero, from its rendered text.

    The last "Total" that is not "Subtotal", followed by an amount. True only
    for an amount that is exactly zero; False for anything else; None when no
    total can be read at all (which stops the claim - never a guess).
    """
    matches = re.findall(
        r"(?<![A-Za-z])Total\b[^0-9\n]{0,24}?(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)",
        text or "",
    )
    if not matches:
        if re.search(r"(?<![A-Za-z])Total\b[^\n]{0,24}\bFree\b", text or "", re.I):
            return True
        return None
    digits = re.sub(r"[^0-9]", "", matches[-1])
    return bool(digits) and int(digits) == 0


def _key_store(offer: FreeOffer) -> str | None:
    for delivery in offer.extra.get("delivery") or []:
        if delivery in DELIVERY_STORE:
            return DELIVERY_STORE[delivery]
    return None


def _is_choice(offer: FreeOffer) -> bool:
    return offer.extra.get("humble") == "choice" or (offer.external_id or "").startswith(CHOICE_PREFIX)


# A short-lived copy of the last public answer, so a run's in-session
# discovery does not repeat the three page loads its own sessionless
# discovery made a moment earlier.
_public_cache: tuple[float, list[FreeOffer]] | None = None


def _copies(offers: list[FreeOffer]) -> list[FreeOffer]:
    # The runner writes `account_id` into `extra`; never share the dict.
    return [dataclasses.replace(o, extra=dict(o.extra)) for o in offers]


class HumbleAdapter(BaseAdapter):
    store = "humble"
    display_name = "Humble Bundle"
    login_url = LIBRARY_URL
    signin_url = SIGNIN_URL
    # No `sign_in_forms`: the login's first step is measured (identifier-first,
    # one "Enter your E-Mail" field and Continue, like Epic's) but the password
    # step and where focus lands were not seen. An Enter typed into the wrong
    # step is a failed login attempt. The one-at-a-time buttons still work.
    blurb = (
        "Claims the Humble Store's occasional free games and, if you subscribe "
        "to Humble Choice, redeems this month's games and keeps their keys in "
        "the ledger. Revealing a key on Humble binds it to your account, as it "
        "would by hand. Trove never pays early, skips, pauses or changes your "
        "plan, never ticks a newsletter box, and never chooses a platform or "
        "links an account for you. Humble's terms restrict automated access; "
        "this is for your own account, at a human pace, and that risk is yours."
    )
    discovery_needs_session = True

    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". Humble's login "
                "asks for your email first and the password on the next step."
            ),
        ),
        Requirement(
            name="Automated access",
            description=(
                "Humble's terms forbid automated systems that send more "
                "requests than a person would in a browser. Trove visits a few "
                "pages every few hours, but it is automation all the same."
            ),
        ),
        Requirement(
            name="A Humble Choice subscription",
            description=(
                "Only for the monthly games. Without one, Trove claims the "
                "store's free games and leaves Choice alone."
            ),
            required=False,
        ),
        Requirement(
            name="Linked accounts",
            description=(
                "Some Choice games are delivered to Battle.net or another linked "
                "account. Trove never links one; link it on Humble yourself."
            ),
            required=False,
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """The store's free giveaways, read off its curated pages. No session.

        Three page loads at most, `POLITE_DELAY_S` apart. A 429/5xx raises
        (the runner turns it into `StoreUnavailable`); a 403 is Cloudflare
        refusing a plain client, logged and skipped page by page.
        """
        global _public_cache
        hits: list[dict] = []
        refused: list[str] = []
        async with httpx.AsyncClient(
            timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for n, url in enumerate(STORE_PAGES):
                if n:
                    await asyncio.sleep(POLITE_DELAY_S)
                response = await client.get(url)
                if response.status_code == 403:
                    refused.append(url)
                    logger.error("Humble: %s answered 403 (Cloudflare); skipped.", url)
                    continue
                response.raise_for_status()
                page_hits = parse_store_hits(response.text)
                if not page_hits:
                    # The itch.io lesson: markup in, nothing parsed out, is a
                    # parser that no longer matches - say it loudly.
                    logger.error(
                        "Humble: %s returned %d bytes but no product feed parsed.",
                        url, len(response.content),
                    )
                hits.extend(page_hits)
        offers = free_offers_from_hits(hits)
        logger.info(
            "Humble discovery: %d products read across %d pages (%d refused), %d free.",
            len({h.get("id") for h in hits}), len(STORE_PAGES), len(refused), len(offers),
        )
        _public_cache = (time.monotonic(), _copies(offers))
        return offers

    async def _public_offers(self) -> list[FreeOffer]:
        if _public_cache and time.monotonic() - _public_cache[0] < PUBLIC_CACHE_S:
            return _copies(_public_cache[1])
        try:
            return await self.list_free_offers()
        except Exception as exc:
            # Choice must not be lost to a store page that failed.
            logger.warning("Humble: store discovery failed inside the session: %s", exc)
            return []

    async def discover_in_session(self, page: Page) -> list[FreeOffer]:
        """The store's free games, plus this month's Choice games for a subscriber."""
        offers = await self._public_offers()
        offers.extend(await self._choice_offers(page))
        return offers

    async def _choice_offers(self, page: Page) -> list[FreeOffer]:
        try:
            await _goto(page, MEMBERSHIP_HOME_URL)
        except PlaywrightTimeout:
            logger.warning("Humble Choice: the membership page did not load; nothing from Choice.")
            return []
        landed = (page.url or "").split("?", 1)[0].rstrip("/")
        html = await _page_html(page)
        subscriber = parse_subscriber(html)
        if landed.endswith("/membership") or subscriber is False:
            logger.info(
                "Humble Choice: this account is not a subscriber (landed on %s, "
                "hasActiveSubscription=%s); nothing to claim from Choice.",
                page.url, subscriber,
            )
            return []
        if subscriber is None:
            logger.info(
                "Humble Choice: could not tell whether this account subscribes "
                "(no hasActiveSubscription on %s); claiming nothing from Choice.",
                page.url,
            )
            return []
        if await _first_visible(page, PAY_EARLY, timeout_ms=1500):
            logger.info(
                "Humble Choice: this month is not unlocked yet (a pay-early "
                "control is showing). Trove never pays early; nothing to claim."
            )
            return []

        items = parse_choice_items(html)
        month = parse_choice_month(html)
        if not items:
            # The subscriber home's shape is unverified; the public page's is
            # measured.
            try:
                await _goto(page, MEMBERSHIP_URL)
                public = await _page_html(page)
            except PlaywrightTimeout:
                public = ""
            items = parse_choice_items(public)
            month = month or parse_choice_month(public)
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        offers = choice_offers_from_items(items, month)
        logger.info(
            "Humble Choice: subscriber; %s has %d items, %d to claim.",
            month, len(items), len(offers),
        )
        return offers

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        """Humble's own session poll: `{"status":"anonymous"}` signed out
        (measured); anything else is signed in, by the site's own rule."""
        response: Response | None = None
        try:
            response = await _goto(page, SESSION_STATUS_URL)
        except PlaywrightTimeout:
            return False, "Humble did not load in time."
        text = await _body_text(page)
        status = None
        try:
            data = json.loads(text or "")
            if isinstance(data, dict) and isinstance(data.get("status"), str):
                status = data["status"]
        except ValueError:
            pass
        if status is None and await _first_visible(page, CHALLENGE):
            raise StoreChallenged(
                "Humble is showing a challenge that needs a person."
                " This is the store judging Trove's own browser, not your"
                " account, so the session behind it may be fine - Trove has"
                " left it alone and will try again on the next run."
            )
        if status == "anonymous":
            return False, "This account is signed out of Humble. Sign in again with \"Sign in here\"."
        if status:
            return True, "Signed in."
        refused = refusal_reason("Humble", response.status if response else None)
        if refused:
            raise StoreUnavailable(refused)
        logger.info("Humble health: session-status said %r on %s.", (text or "")[:120], page.url)
        return False, "Could not tell whether this account is signed in."

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        """Read off the library card. True only on evidence; never False.

        A Choice game counts as owned once it is revealed or redeemed; a store
        product counts as owned once it is in the library at all.
        """
        try:
            await _goto(page, LIBRARY_URL)
        except PlaywrightTimeout:
            return None
        await self._narrow_library(page, offer.title)
        card = await _find_card(page, offer.title)
        if card is None:
            return None
        text = await _card_text(card)
        if _KEY_RE.search(text) or _REDEEMED_RE.search(text):
            return True
        if not _is_choice(offer):
            return True
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    def checkout_url(self, offer: FreeOffer) -> str | None:
        if _is_choice(offer):
            return LIBRARY_URL
        return offer.url or None

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        if _is_choice(offer):
            return await self._claim_choice(page, offer)
        return await self._claim_store(page, offer)

    async def _claim_choice(self, page: Page, offer: FreeOffer) -> ClaimResult:
        """Reveal one Choice game in the library: at most one click."""
        try:
            await _goto(page, LIBRARY_URL)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "Humble's library did not load.", await self._shot(page, offer, "library-timeout")
            ) from None
        await self._guard(page, offer)
        if await _first_visible(page, PAY_EARLY, timeout_ms=800):
            return ClaimResult(
                outcome="not_eligible",
                reason_code=REASON_NOT_UNLOCKED,
                detail=(
                    "This month's Choice is not unlocked yet. Trove never pays "
                    "early or changes your plan; it will look again after your "
                    "billing date."
                ),
            )
        return await self._redeem_card(page, offer)

    async def _redeem_card(self, page: Page, offer: FreeOffer) -> ClaimResult:
        await self._narrow_library(page, offer.title)
        card = await _find_card(page, offer.title)
        if card is None:
            logger.info("Humble: no library card titled %r on %s.", offer.title, page.url)
            return ClaimResult(
                outcome="not_eligible",
                reason_code=REASON_NOT_IN_LIBRARY,
                detail=(
                    "Not in your Humble library yet. If this month has not been "
                    "billed, it appears after your billing date; Trove never "
                    "pays early."
                ),
            )
        text = await _card_text(card)
        key_store = _key_store(offer)
        key = _KEY_RE.search(text)
        if key:
            return ClaimResult(
                outcome="already_owned", detail="Already revealed; the key is in the ledger.",
                key_code=key.group(0), key_store=key_store,
            )
        if _REDEEMED_RE.search(text):
            return ClaimResult(outcome="already_owned", detail="Already redeemed on this account.")
        if _EXPIRED_RE.search(text):
            return ClaimResult(
                outcome="not_eligible", reason_code=REASON_EXPIRED,
                detail="Humble says this key has expired.",
            )
        if _STOCK_RE.search(text):
            return ClaimResult(
                outcome="not_eligible", reason_code=REASON_STOCK,
                detail="Humble has no keys for this game in your region right now; Trove will look again.",
            )
        if _LINK_RE.search(text) or await _first_visible(card, CARD_LINK, timeout_ms=400):
            return ClaimResult(
                outcome="not_eligible", reason_code=REASON_LINK,
                detail=(
                    "This game is delivered to a linked account (such as "
                    "Battle.net). Trove never links accounts; link it on Humble "
                    "and Trove will reveal it on a later run."
                ),
            )
        button = await _first_visible(card, CARD_REVEAL, timeout_ms=2000)
        if button is None:
            logger.info("Humble: card %r had no Reveal/Claim. Text: %r", offer.title, text[:200])
            return ClaimResult(
                outcome="not_eligible", reason_code=REASON_NO_CONTROL,
                detail="The library shows this game with nothing to reveal or claim.",
            )

        try:
            label = " ".join((await button.inner_text() or "").split())[:30]
        except Exception:
            label = "?"
        logger.info("Humble: pressing %r on %r (this binds the key to the account).", label, offer.title)
        try:
            await button.click(timeout=5000)
        except PlaywrightTimeout:
            await self._guard(page, offer)
            raise NeedsAttention(
                f"The {label} button could not be clicked. Run and watch to see why.",
                await self._shot(page, offer, "click-blocked"),
            ) from None

        # Clicked once. From here only read - and Cancel, which only cancels.
        for _ in range(15):
            await self._guard(page, offer)
            if await _first_visible(page, PLATFORM_DIALOG, timeout_ms=400):
                cancel = await _first_visible(page, DIALOG_CANCEL, timeout_ms=800)
                if cancel is not None:
                    try:
                        await cancel.click(timeout=3000)
                    except Exception:
                        pass
                return ClaimResult(
                    outcome="not_eligible", reason_code=REASON_PLATFORM,
                    detail=(
                        "Humble asks which platform to redeem this on, and the "
                        "choice is permanent - that is yours to make. Trove "
                        "pressed Cancel; choose it in your library."
                    ),
                )
            dialog = await _first_visible(page, DIALOG, timeout_ms=300)
            if dialog is not None:
                result = await self._read_error_dialog(page, offer, dialog)
                if result is not None:
                    return result
            text = await _card_text(card)
            key = _KEY_RE.search(text)
            if key:
                logger.info("Humble: %r revealed a key for %s.", offer.title, key_store or "an unstated store")
                return ClaimResult(
                    outcome="claimed",
                    detail="Revealed" + (f" a {key_store} key" if key_store else " a key") + "; it is in the ledger.",
                    key_code=key.group(0), key_store=key_store,
                )
            if _REDEEMED_RE.search(text) or await _first_visible(page, REDEEMED_TOAST, timeout_ms=200):
                return ClaimResult(outcome="claimed", detail="Redeemed in your Humble library.")
            await page.wait_for_timeout(1000)

        raise NeedsAttention(
            "The reveal was pressed but Humble never showed a key or a result "
            "Trove recognises. Check the library before Trove tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    async def _read_error_dialog(self, page, offer, dialog) -> ClaimResult | None:
        try:
            text = await dialog.inner_text() or ""
        except Exception:
            return None
        for pattern, outcome, code, sentence in _REDEEM_ERRORS:
            if not pattern.search(text):
                continue
            logger.info("Humble: %r answered %r.", offer.title, text[:160])
            if outcome == "signed_out":
                raise NeedsAttention(
                    "This account is signed out of Humble. Sign in again with \"Sign in here\".",
                    await self._shot(page, offer, "signed-out"),
                )
            if outcome == "attention":
                raise NeedsAttention(
                    f"{sentence} One attempt only; check the library.",
                    await self._shot(page, offer, "redeem-error"),
                )
            return ClaimResult(outcome=outcome, reason_code=code, detail=sentence)
        return None

    async def _claim_store(self, page: Page, offer: FreeOffer) -> ClaimResult:
        """Order one free store product, proving a zero total first."""
        if not offer.url:
            return ClaimResult(outcome="failed", detail="This offer has no product page to open.")
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The product page did not load.", await self._shot(page, offer, "product-timeout")
            ) from None
        await self._guard(page, offer)

        if await _first_visible(page, NEWSLETTER_OPTIN, timeout_ms=800):
            return self._newsletter_result()

        price = parse_product_price(await _page_html(page))
        if price is None or price >= 0.5:
            logger.info("Humble: %r reads price %r on its own page.", offer.title, price)
            return ClaimResult(
                outcome="not_eligible", reason_code=REASON_NOT_FREE,
                detail=(
                    "The product page does not show this as free any more, so "
                    "nothing was clicked." if price is not None else
                    "Could not read the product page's price, so nothing was clicked."
                ),
            )

        direct = await _first_visible(page, GET_FREE, timeout_ms=800)
        button = direct or await _first_visible(page, ADD_TO_CART, timeout_ms=4000)
        if button is None:
            raise NeedsAttention(
                "Could not find the product's button. Humble has probably "
                "changed the page; run and watch to read its label.",
                await self._shot(page, offer, "no-buy-button"),
            )
        logger.info("Humble: pressing %s on free %r.", "a free control" if direct else "Add to Cart", offer.title)
        await button.click(timeout=5000)
        await page.wait_for_timeout(1500)
        await self._guard(page, offer)
        if await _first_visible(page, NEWSLETTER_OPTIN, timeout_ms=600):
            return self._newsletter_result()

        if not direct:
            try:
                await _goto(page, CHECKOUT_URL)
            except PlaywrightTimeout:
                raise NeedsAttention(
                    "The checkout did not load.", await self._shot(page, offer, "checkout-timeout")
                ) from None
            await self._guard(page, offer)
            result = await self._place_free_order(page, offer)
            if result is not None:
                return result

        # Ordered (or claimed directly). Ownership is the ground truth; the key
        # is revealed in the library the same way a Choice game's is.
        for _ in range(10):
            if "/order/confirmation" in (page.url or ""):
                break
            await page.wait_for_timeout(1000)
        try:
            await _goto(page, LIBRARY_URL)
        except PlaywrightTimeout:
            return ClaimResult(outcome="claimed", detail="Ordered at no cost; the key is waiting in your Humble library.")
        revealed = await self._redeem_card(page, offer)
        if revealed.outcome in ("claimed", "already_owned"):
            return ClaimResult(
                outcome="claimed",
                detail="Ordered at no cost" + ("; the key is in the ledger." if revealed.key_code else "."),
                key_code=revealed.key_code, key_store=revealed.key_store,
            )
        if revealed.reason_code == REASON_NOT_IN_LIBRARY:
            raise NeedsAttention(
                "The free order was placed but the product is not in your "
                "Humble library. Check the account before Trove tries again.",
                await self._shot(page, offer, "order-unconfirmed"),
            )
        return ClaimResult(
            outcome="claimed",
            detail=f"Ordered at no cost; the key was not revealed: {revealed.detail}",
        )

    async def _place_free_order(self, page: Page, offer: FreeOffer) -> ClaimResult | None:
        """Press the order button only if the total reads zero, twice."""
        body = await _body_text(page)
        if re.search(r"recurring charge|automatically renew", body, re.I):
            raise NeedsAttention(
                "The checkout mentions a recurring charge, so something other "
                "than the free game is in the cart. Trove pressed nothing.",
                await self._shot(page, offer, "checkout-recurring"),
            )
        zero = total_is_zero(body)
        if zero is not True:
            logger.info("Humble checkout for %r: total zero=%r.", offer.title, zero)
            return ClaimResult(
                outcome="not_eligible", reason_code=REASON_NOT_FREE,
                detail=(
                    "The checkout total is not zero - the cart may hold other "
                    "things you put there - so Trove pressed nothing. The free "
                    "game is in your cart; finish it yourself if you want it."
                    if zero is False else
                    "Could not read the checkout total, so Trove pressed nothing."
                ),
            )
        required = await self._required_optin(page)
        if required:
            return self._newsletter_result()
        button = await _first_visible(page, PLACE_ORDER, timeout_ms=4000)
        if button is None:
            raise NeedsAttention(
                "The checkout total is zero but Trove could not find the order "
                "button. Run and watch to read its label.",
                await self._shot(page, offer, "no-order-button"),
            )
        if total_is_zero(await _body_text(page)) is not True:
            return ClaimResult(
                outcome="not_eligible", reason_code=REASON_NOT_FREE,
                detail="The checkout total changed before the order, so Trove pressed nothing.",
            )
        logger.info("Humble: placing a zero-total order for %r.", offer.title)
        await button.click(timeout=5000)
        await page.wait_for_timeout(2000)
        await self._guard(page, offer)
        return None

    async def _required_optin(self, page: Page) -> bool:
        """An opt-in on the checkout that the order cannot go without."""
        box = await _first_visible(page, NEWSLETTER_OPTIN, timeout_ms=500)
        if box is None:
            return False
        try:
            required = await box.evaluate(
                "e => !!(e.required || e.getAttribute('aria-required') === 'true')"
            )
        except Exception:
            required = True  # unreadable: assume the worst, tick nothing
        logger.info("Humble checkout: a marketing opt-in is showing (required=%s); left unticked.", required)
        return bool(required)

    def _newsletter_result(self) -> ClaimResult:
        return ClaimResult(
            outcome="not_eligible", reason_code=REASON_NEWSLETTER,
            detail=(
                "This giveaway asks you to sign up for Humble's newsletter. "
                "Trove never ticks a marketing box for you; claim it yourself "
                "if you want it."
            ),
        )

    # ── Shared ───────────────────────────────────────────────────────────

    async def _narrow_library(self, page: Page, title: str) -> None:
        """Type the title into the library's own name filter, if it is there."""
        box = await _first_visible(page, LIBRARY_SEARCH, timeout_ms=2500)
        if box is None:
            return
        try:
            await box.fill(title)
            await page.wait_for_timeout(1200)
        except Exception as exc:
            logger.debug("Humble: the library filter did not take %r: %s", title, exc)

    async def _guard(self, page: Page, offer: FreeOffer) -> None:
        """Stop on a challenge (to the un-driven window) or a login prompt."""
        if await _first_visible(page, CHALLENGE, timeout_ms=600):
            raise CheckoutBlocked(
                "Humble put up a challenge. Trove never answers those; finish "
                "this one by hand - press “Finish the claim here”.",
                await self._shot(page, offer, "challenge"),
                offer_id=offer.external_id,
            )
        if "auth.humblebundle.com" in (page.url or "") or await _first_visible(
            page, LOGIN_PROMPT, timeout_ms=400
        ):
            raise NeedsAttention(
                "This account is signed out of Humble. Sign in again with \"Sign in here\".",
                await self._shot(page, offer, "signed-out"),
            )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))
