"""Ubisoft Store (Ubisoft Connect).

Ubisoft gives a normally-paid game away now and then - "free to keep", it
stays in the Ubisoft library - and that, exclusively, is what this adapter
claims. NOT free-to-play (Brawlhalla, Trackmania Starter Access...), NOT free
weekends, demos or trials: those are playtime, not a library add, and the
store labels every one of them differently from a giveaway (below).

**Discovery is the store's own search index, sessionless.** No third-party
feed. Measured against the live store (23 Nov 2026), all with Trove's own user
agent, which the store accepts:

* `GET https://store.ubisoft.com/us/free-games` -> 200 HTML, "Free Offers |
  Ubisoft Store", a Salesforce Commerce Cloud (Demandware) category page
  behind Cloudflare. On the day it listed nine tiles, all free-to-play
  (`data-tc100` edition "Free to play" / "Free Access" / "Free Starter
  Access"), each `div.product-tile[data-itemid=<24-hex id>]` with a
  `.product-availability-label` carrying `data-freeplay="true"`,
  `data-freeofferstartdate` / `data-freeofferenddate` in Java's
  `Date.toString()` shape (`"Fri May 24 13:00:00 GMT 2030"`) and a "$0.00"
  price. So `freeplay` and a $0 price do **not** say "giveaway" - every
  free-to-play title has both, with windows running to 2030 or 2300.
* The same page carries `window.SitePreferences` with the store's public
  Algolia search credentials (`ALGOLIA_API_ID` "XELY3U4LOD",
  `ALGOLIA_API_KEY`, and `ALGOLIA_INDEXES_V2` naming
  `production__us_ubisoft__products__en_US__best_sellers`). That index is
  what the store front itself searches, and it is the part that matters:
* `POST https://XELY3U4LOD-dsn.algolia.net/1/indexes/<index>/query` with the
  page's key -> 200 JSON `{hits, nbHits, facets, ...}`. Each hit is one
  product: `id` / `objectID` (the SFCC product id), `MasterID`, `title`,
  `link`, `image_link`, `product_type` ("Games" | "DLCs"), `Platform`,
  `platforms_availability` (a JSON-encoded list), `comingSoon` ("Yes"/"No"),
  `preorder` ("true"/"false"), `price` ({"USD": n}), and **`Free_offer`**.
  Measured facet counts for `Free_offer`: `demo: 22, freeplay: 9`, nothing
  else - no giveaway was running. The store's own tile renderer (its
  `dist/app.js`, read) badges a tile "giveaway" exactly when
  `Free_offer.toLowerCase() === "giveaway"` (and "Free with any purchase"
  for `freeWithPurchase`), so `Free_offer == giveaway` is the store's own
  statement and the one test this adapter uses. Facet filters are
  case-insensitive (measured: `Free_offer:FREEPLAY` returned the 9).
* `price.USD == 0` is **not** a usable test: it also matched coming-soon
  products with no price yet (Morbid Metal, Heroes of Might and Magic: Olden
  Era) and a PS5-only edition of Avatar - 16 hits, none of them a giveaway.
* The regional index (`production__ie_ubisoft__products__en_NO__...`, which
  the `/ie/` store names for a visitor from Norway) carries **no `Free_offer`
  facet at all** (measured: no values, `Free_offer:freeplay` -> 0 hits where
  the US index had 9), so discovery always asks the US index. Giveaways have
  historically been global; a region-locked one is caught at claim time.

**Where a giveaway is claimed has moved, and the honest answer may be "not in
a browser".** Every store.ubisoft.com product page tried (23 Nov 2026) 301s to
the new ubisoft.com game page (`www.ubisoft.com/<locale>/games/<slug>`, a
Next.js app). `https://store.ubisoft.com/<id>.html` - the site-less product
URL ubisoft.com itself links to - geo-routes to the visitor's regional store
and on to that game page (measured: fr -> ie -> www.ubisoft.com/en-gb/...).
That page's product data carries `offerType` (`GIVEAWAY`, `FREE_TO_PLAY`,
`FREE_WEEKEND`, `FREE_TRIAL`, `DEMO`, `FREE_WITH_PURCHASE`) and `offerEnds`,
and its JavaScript (read) gives a giveaway the button "Get it now" whose
action **opens `https://connect.ubisoft.com/n/game-page/v1?space-id=..&
app-id=..&free-product-id=<offerId>`** in a new tab. That page (fetched)
is a launcher: "Open Ubisoft Connect PC" / "Download Ubisoft Connect PC", and
its own data is a `uplay://navigate/v1/game-page/v1?...&free-product-id=...`
deep link. So on the current game page the claim happens **in the Ubisoft
Connect PC desktop app**, which Trove cannot and should not drive. On that
page this adapter clicks nothing and records `not_eligible` with a sentence
saying where to claim it. (Below 768 px the same button goes to
`free.ubisoft.com` instead, which redirects to the editorial
`www.ubisoft.com/en-gb/games/free` - not a claim flow either.)

The older store page had a browser claim - the store's JS still routes a click
on `#add-to-cart.giveaway` away from the add-to-cart handler, its resources
still say "Get it for free" (`GIVEAWAY_TITLE`) and "ADD TO MY GAMES", and
`window.Urls` still names `Product-ActivateFreeOfferGame` - so if a giveaway
lands on a store.ubisoft.com page instead, the claim clicks that one button,
once, and only after proving it is the giveaway button (`giveaway` class,
no price or cart wording on it). That branch is written from the store's code,
never seen live.

**The session check is the store's own `window.User`.** Every store page
serves `window.User = {"isLoggedIn": false, "ubisoftId": null, ...,
"ownership": null, ...}` signed out (measured). Signed in, `isLoggedIn` should
be true and `ownership` probably lists owned products - both unverified, and
read defensively: an unexpected shape is "could not tell", never "signed out"
and never "does not own it".

**Sign-in** is Ubisoft's WebAuth at connect.ubisoft.com (measured: 200 "Ubisoft
WebAuth", a client-rendered app with no form in the HTML). Its bundle (read)
has `AuthEmail` / `AuthPassword` fields, a "Remember me" option, reCAPTCHA and
DataDome - so the first sign-in is by hand, as everywhere. The form's shape
was not seen rendered, so no assisted sequence is declared.

**Terms.** Automating a store login can breach the store's terms, and Ubisoft's
Terms of Use forbid automated access to its services; this is the person's own
account on their own machine, the same footing as Epic. The Algolia key is
the public, search-only key the store front ships to every visitor; Trove
reads it off the store's own page each run rather than baking it in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
from playwright.async_api import Page, Response, TimeoutError as PlaywrightTimeout

from ..browser import (
    CheckoutBlocked,
    NeedsAttention,
    StoreChallenged,
    StoreUnavailable,
    is_challenge_title,
    refusal_reason,
    screenshot,
    screenshot_name,
)
from .base import BaseAdapter, ChallengeWaiter, ClaimResult, FreeOffer, Requirement

logger = logging.getLogger(__name__)

UA = "Trove/0.1 (self-hosted free game claimer)"

STORE_ROOT = "https://store.ubisoft.com"
# The site-less store root geo-redirects to the visitor's regional home
# (measured: / -> /ie/ -> /ie/home?lang=en-ZW from Norway), so the session
# check lands on the store the account actually shops in.
HOME_URL = f"{STORE_ROOT}/"
# The US store's free-offers category: public, 200 to Trove's user agent, and
# the page that carries the search credentials in `window.SitePreferences`.
FREE_PAGE = f"{STORE_ROOT}/us/free-games"
SEARCH_LOCALE = "en_US"

# What the free page said on 23 Nov 2026. Used only if the page stops carrying
# them, with a warning - the page is the source, these are a fallback.
FALLBACK_ALGOLIA_APP = "XELY3U4LOD"
FALLBACK_ALGOLIA_KEY = "5638539fd9edb8f2c6b024b49ec375bd"
FALLBACK_ALGOLIA_INDEX = "production__us_ubisoft__products__en_US__best_sellers"

# The store app's id on Ubisoft's auth (the store page's `data-appid`, and
# `wa_data.appId`), so the sign-in returns to the store.
STORE_APP_ID = "82b650c0-6cb3-40c0-9f41-25a53b62b206"
SIGNIN_URL = (
    "https://connect.ubisoft.com/login?appId="
    f"{STORE_APP_ID}&lang=en-US&nextUrl={quote(HOME_URL, safe='')}"
)

# The one `Free_offer` value that means free to keep. The others measured or
# named by the store's code - freeplay, demo, freeWithPurchase - are not.
GIVEAWAY = "giveaway"

# Reason codes for `not_eligible`, as words for the interface.
REASON_DESKTOP_CLIENT = "desktop_client"
REASON_REGION = "region_locked"
REASON_NO_CONTROL = "no_claim_control"


# ── The selectors ───────────────────────────────────────────────────────────

# A challenge only a person can answer. The store and ubisoft.com sit behind
# Cloudflare (measured: `server: cloudflare`); the login bundle names reCAPTCHA
# and DataDome (read). None has been seen in front of a store page here.
CHALLENGE = [
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="captcha-delivery.com"]',  # DataDome
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="arkoselabs"]',
    '#challenge-form',
    'text=/verify you are human/i',
]

# The old store page's giveaway button. Measured from the store's own JS, not
# a rendered page: the click handler skips add-to-cart when the button has the
# class `giveaway`, and the sticky copy (`.pdp-sticky-cta.giveaway`) just
# clicks `#add-to-cart.giveaway`. Plain `#add-to-cart` is the PAID button and
# must never match, which is why every entry requires the class.
GIVEAWAY_BUTTON = [
    "#add-to-cart.giveaway",
    "button.giveaway#add-to-cart",
    ".pdp-sticky-cta.giveaway",
]

# The geo-restriction popup, measured verbatim (ProductDetails-GetFreeOfferPopup
# for a US product, from Norway): "This offer is not accessible in your
# location".
REGION_LOCKED = ['text=/not accessible in your location/i']

# After the click. UNVERIFIED wording - nobody has seen a giveaway claimed
# here - so ownership (window.User) stays the ground truth.
CONFIRMED = [
    'text=/added to your (games|library)/i',
    'text=/is now in your library/i',
    'text=/successfully (added|activated)/i',
]
OWNED = [
    'text=/already (own|in your library|have this)/i',
    'text=/you own this/i',
]
# Ubisoft's sign-in, framed or navigated to, after a click.
LOGIN = [
    'iframe[src*="connect.ubisoft.com"]',
    'input#AuthEmail',
    'input[name="AuthEmail"]',
]

# Words a free button must never carry: a purchase verb, or a price that is
# not zero. (A zero price is allowed: the store's resources carry
# `GIVEAWAY_PRICE = 0`, so "$0.00" on the button would be the giveaway's own.)
_MONEY_WORDS = re.compile(r"add to cart|buy|pre-?order|subscribe|checkout", re.I)
_AMOUNT = re.compile(r"\d+(?:[.,]\d+)?")

# `offerType` on the game page, in the RSC payload (escaped quotes in the
# script text, plain ones once parsed). Values from the page's own JS.
_OFFER_TYPE_RE = re.compile(r'\\?"offerType\\?"\s*:\s*\\?"([A-Z_]+)')
_OFFER_ENDS_RE = re.compile(r'\\?"offerEnds\\?"\s*:\s*\\?"([^"\\]+)')


async def _goto(page: Page, url: str) -> Response | None:
    """Navigate, tolerating a navigation the page replaced under us.

    The same tolerance as Epic's: the store redirects a lot (geo, then to
    ubisoft.com, then to a slug), and Playwright reports a superseded
    navigation as `net::ERR_ABORTED` on a page that in fact loaded. A timeout
    still raises.
    """
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
    """The first visible element matching any of these selectors, or None.

    Epic's polling version. `>> visible=true` rather than `.first`: a
    locator's first match is not necessarily a visible one (itch.io's hidden
    duplicate sale widget), so ask Playwright for the first *visible* match.
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


def _product_id(offer: FreeOffer) -> str | None:
    pid = offer.extra.get("product_id") or offer.external_id
    return str(pid) if pid else None


def product_url(pid: str) -> str:
    """The site-less product URL: geo-routes to the account's own store."""
    return f"{STORE_ROOT}/{pid}.html"


# ── Discovery parsing (pure, so the sim can drive it) ─────────────────────

def parse_site_preferences(page_html: str) -> tuple[str, str, str] | None:
    """(app id, search key, index) from the store page's `window.SitePreferences`.

    Measured: one line, `window.SitePreferences = {...};`, JSON, with
    `ALGOLIA_API_ID`, `ALGOLIA_API_KEY` and `ALGOLIA_INDEXES_V2` - itself a
    JSON string keyed by locale, each with a `defaultIndex`.
    """
    match = re.search(r"window\.SitePreferences\s*=\s*(\{.*?\});?\s*$", page_html, re.M)
    if not match:
        return None
    try:
        prefs = json.loads(match.group(1))
        indexes = json.loads(prefs.get("ALGOLIA_INDEXES_V2") or "{}")
        index = (indexes.get(SEARCH_LOCALE) or indexes.get("default") or {}).get(
            "defaultIndex"
        )
        app, key = prefs.get("ALGOLIA_API_ID"), prefs.get("ALGOLIA_API_KEY")
    except (ValueError, AttributeError) as exc:
        logger.warning("Ubisoft: SitePreferences did not parse: %s", exc)
        return None
    if not (app and key and index):
        return None
    return str(app), str(key), str(index)


_TILE_SPLIT = re.compile(r'<div[^>]*class="product-tile[\s"]')


def parse_tiles(page_html: str) -> dict[str, dict]:
    """The free page's tiles, keyed by product id: end date and giveaway mark.

    Measured: `div.product-tile` with `data-itemid` (the master id - the
    search hit's `MasterID`), and a `.product-availability-label` carrying
    `data-freeofferenddate="Fri May 24 13:00:00 GMT 2030"`. The giveaway mark
    is the store's own badge class (`card-label giveaway`, from its renderer);
    no giveaway tile has been seen, so it is used only to cross-check.
    """
    tiles: dict[str, dict] = {}
    chunks = _TILE_SPLIT.split(page_html)
    for chunk in chunks[1:]:
        chunk = chunk[:15000]
        item = re.search(r'data-itemid="([0-9a-f]{24})"', chunk)
        if not item:
            continue
        end = re.search(r'data-freeofferenddate="([^"]*)"', chunk)
        tiles[item.group(1)] = {
            "ends_at": parse_java_date(end.group(1)) if end else None,
            "giveaway": bool(
                re.search(r'class="[^"]*\bcard-label\b[^"]*\bgiveaway\b', chunk)
            ),
        }
    return tiles


def parse_java_date(value: str | None) -> datetime | None:
    """`Fri May 24 13:00:00 GMT 2030` (Java's Date.toString, measured) -> UTC."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%a %b %d %H:%M:%S GMT %Y").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        logger.debug("Ubisoft: could not read the date %r.", value)
        return None


def parse_hit(hit: dict, tiles: dict[str, dict] | None = None) -> FreeOffer | None:
    """One search hit as a free-to-keep offer, or None with a log line.

    The test is the store's own: `Free_offer` is "giveaway". Everything else
    the index says free - `freeplay` (free-to-play, and the free weekends
    that share the flag), `demo`, `freeWithPurchase`, or a bare $0 price - is
    refused. A coming-soon or pre-order product is refused too: nothing can be
    claimed before it exists, and the $0 coming-soon rows prove the index
    carries them.
    """
    tiles = tiles or {}
    pid = str(hit.get("id") or hit.get("objectID") or "")
    title = hit.get("title") or hit.get("name")
    free_offer = str(hit.get("Free_offer") or "").strip().lower()
    if free_offer != GIVEAWAY:
        return None
    if not re.fullmatch(r"[0-9a-f]{24}", pid) or not title:
        logger.error(
            "Ubisoft: a giveaway hit had no usable id/title; keys were %r.",
            sorted(hit.keys()),
        )
        return None
    if str(hit.get("comingSoon") or "").lower() == "yes" or str(
        hit.get("preorder") or ""
    ).lower() == "true":
        logger.info("Ubisoft: skipping %r - giveaway flag on an unreleased product.", title)
        return None
    platforms = f"{hit.get('Platform') or ''} {hit.get('platforms_availability') or ''}"
    if platforms.strip() and "pc" not in platforms.lower():
        logger.info("Ubisoft: skipping %r - not a PC product (%s).", title, platforms)
        return None

    master = str(hit.get("MasterID") or "")
    tile = tiles.get(pid) or tiles.get(master) or {}
    link = hit.get("link") or (f"{STORE_ROOT}{hit['url']}" if hit.get("url") else None)
    return FreeOffer(
        external_id=pid,
        title=title,
        url=link or product_url(pid),
        image_url=hit.get("image_link"),
        kind="dlc" if str(hit.get("product_type") or "").lower().startswith("dlc") else "game",
        ends_at=tile.get("ends_at"),
        source="store",
        extra={"product_id": pid, "master_id": master or None},
    )


def ownership_has(ownership, ids: set[str]) -> bool:
    """Whether `window.User.ownership` names any of these product ids.

    Its signed-in shape is unmeasured (it is `null` signed out), so this only
    ever answers "yes, found it": a list, a dict or a string that contains the
    id. Not finding it is not "does not own it".
    """
    if not ownership or not ids:
        return False
    try:
        blob = json.dumps(ownership)
    except (TypeError, ValueError):
        blob = str(ownership)
    return any(i and i in blob for i in ids)


def is_free_button(classes: str, text: str) -> bool:
    """The money guard: the giveaway class, and nothing that reads like a price.

    The class is the store's own routing (its click handler sends a
    `giveaway` button away from add-to-cart). The text check is the second,
    independent half: a button whose label carries a price or a purchase verb
    is refused even if somebody put the class on it.
    """
    class_list = (classes or "").split()
    if "giveaway" not in class_list or _MONEY_WORDS.search(text or ""):
        return False
    amounts = _AMOUNT.findall(text or "")
    return all(float(a.replace(",", ".")) == 0 for a in amounts)


class UbisoftAdapter(BaseAdapter):
    store = "ubisoft"
    display_name = "Ubisoft"
    login_url = HOME_URL
    # Ubisoft's WebAuth with the store's own app id, returning to the store.
    # Measured to load (200, "Ubisoft WebAuth"); whether the store picks the
    # session up from it without a further click is not (Epic's 0.1.39 lesson
    # says check that first if the session check reads signed out after a
    # sign-in).
    signin_url = SIGNIN_URL
    # No sign_in_forms: the form is rendered client-side and was not seen.
    sign_in_note = (
        "Tick “Remember me” before you sign in, or the session will not survive "
        "a restart. Ubisoft may ask for a code from your authenticator or by "
        "email after the password."
    )
    tagline = "Giveaway alerts"
    blurb = (
        "Watches the Ubisoft Store for its occasional giveaways - a paid game "
        "free to keep for a limited time. Free-to-play games, free weekends and "
        "demos are left alone. Heads up: Ubisoft's current store hands a "
        "giveaway to the Ubisoft Connect PC app to claim, which Trove cannot "
        "drive, so Trove may only be able to tell you one is on and point you "
        "at it. Automating a Ubisoft sign-in can breach Ubisoft's terms; this "
        "is for your own account on your own machine."
    )
    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". Ubisoft's login "
                "has a reCAPTCHA and bot detection, so this is the step Trove "
                "cannot do for you."
            ),
        ),
        Requirement(
            name="Ubisoft Connect PC, possibly",
            description=(
                "On Ubisoft's current store a giveaway's \"Get it now\" opens "
                "the Ubisoft Connect PC app. If so, Trove records the giveaway "
                "and says so, and you claim it in the app."
            ),
            required=False,
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """The store's own search, filtered to `Free_offer: giveaway`.

        Two requests, no session: the US free-offers page (for the search
        credentials and the tiles' end dates) and one search query. A
        429/5xx/unreachable store raises out of here as an httpx error, which
        the runner turns into a refusal rather than a parked account.
        """
        async with httpx.AsyncClient(
            timeout=25.0, headers={"User-Agent": UA, "Accept-Language": "en-US"}
        ) as client:
            page = await client.get(FREE_PAGE)
            page.raise_for_status()
            html = page.text

            creds = parse_site_preferences(html)
            if creds is None:
                logger.warning(
                    "Ubisoft: the free page no longer carries the search "
                    "credentials; using the ones measured on 23 Nov 2026."
                )
                creds = (FALLBACK_ALGOLIA_APP, FALLBACK_ALGOLIA_KEY, FALLBACK_ALGOLIA_INDEX)
            app, key, index = creds
            tiles = parse_tiles(html)

            await asyncio.sleep(1.0)
            response = await client.post(
                f"https://{app}-dsn.algolia.net/1/indexes/{index}/query",
                headers={
                    "X-Algolia-Application-Id": app,
                    "X-Algolia-API-Key": key,
                    "Content-Type": "application/json",
                },
                json={
                    "query": "",
                    "hitsPerPage": 50,
                    "facetFilters": [[f"Free_offer:{GIVEAWAY}"]],
                },
            )
            response.raise_for_status()
            payload = response.json()

        hits = payload.get("hits")
        if not isinstance(hits, list):
            logger.error("Ubisoft's search answer had no hit list; keys %r.", sorted(payload))
            return []
        offers = [o for o in (parse_hit(h, tiles) for h in hits) if o is not None]
        if hits and not offers:
            logger.error(
                "Ubisoft: the search reported %d giveaway hit(s) and none parsed; "
                "the record shape has probably changed.", len(hits),
            )
        found = {o.external_id for o in offers} | {
            o.extra.get("master_id") for o in offers if o.extra.get("master_id")
        }
        marked = {pid for pid, tile in tiles.items() if tile.get("giveaway")}
        if marked - found:
            # The page badges a giveaway the index does not report: silence
            # here would hide a live giveaway, so say it loudly.
            logger.error(
                "Ubisoft: the free page badges %s as a giveaway but the search "
                "did not return it; discovery needs a look.", sorted(marked - found),
            )
        for offer in offers:
            logger.info(
                "Ubisoft giveaway: %r (product %s), ends %s.",
                offer.title, offer.external_id, offer.ends_at,
            )
        return offers

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        """Signed in or not, read off the store's own `window.User`."""
        response: Response | None = None
        try:
            response = await _goto(page, HOME_URL)
        except PlaywrightTimeout:
            return False, "The Ubisoft Store did not load in time."

        if await self._challenged(page):
            raise StoreChallenged(
                "The Ubisoft Store is showing a challenge that needs a person."
                " This is the store judging Trove's own browser, not your"
                " account, so the session behind it may be fine - Trove has"
                " left it alone and will try again on the next run."
            )
        user = await _read_user_settled(page)
        if isinstance(user, dict) and isinstance(user.get("isLoggedIn"), bool):
            if user["isLoggedIn"]:
                return True, "Signed in."
            return False, "This account is signed out. Sign in again with \"Sign in here\"."
        refused = refusal_reason("Ubisoft", response.status if response else None)
        if refused:
            raise StoreUnavailable(refused)
        return False, "Could not tell whether this account is signed in."

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        """True only when the store's own `window.User.ownership` names it.

        Anything else is None: the signed-in shape of `ownership` is not
        measured, so not finding the id there says nothing.
        """
        try:
            await _goto(page, HOME_URL)
        except PlaywrightTimeout:
            return None
        user = await _read_user_settled(page)
        if not isinstance(user, dict) or user.get("isLoggedIn") is not True:
            return None
        ids = {_product_id(offer) or "", str(offer.extra.get("master_id") or "")}
        if ownership_has(user.get("ownership"), ids - {""}):
            return True
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    def checkout_url(self, offer: FreeOffer) -> str | None:
        """The product page in the person's own store, for the un-driven window."""
        pid = _product_id(offer)
        return product_url(pid) if pid else None

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        pid = _product_id(offer)
        if not pid:
            return ClaimResult(outcome="failed", detail="This offer has no Ubisoft product id.")
        try:
            await _goto(page, product_url(pid))
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The Ubisoft product page did not load.",
                await self._shot(page, offer, "page-timeout"),
            ) from None
        await self._guard(page, offer)

        host = (urlparse(page.url or "").hostname or "").lower()
        logger.info("Ubisoft: %r landed on %s.", offer.title, page.url)
        if host == "www.ubisoft.com" or host == "ubisoft.com":
            return await self._on_game_page(page, offer)
        if host == "store.ubisoft.com":
            return await self._on_store_page(page, offer, pid)
        raise NeedsAttention(
            f"The product page sent Trove somewhere it does not recognise ({host}). "
            "Run and watch to see where.",
            await self._shot(page, offer, "unknown-page"),
        )

    async def _on_game_page(self, page: Page, offer: FreeOffer) -> ClaimResult:
        """The new ubisoft.com game page: read the offer, click nothing.

        Its giveaway button opens a `uplay://` deep link to Ubisoft Connect PC
        (read from the page's code and the launcher page it opens - see the
        module docstring), so there is nothing for a browser to finish. The
        page's own `offerType` says whether the giveaway is still on.
        """
        try:
            html = await page.content()
        except Exception as exc:
            logger.info("Ubisoft: could not read the game page: %s", exc)
            html = ""
        types = set(_OFFER_TYPE_RE.findall(html))
        ends = _OFFER_ENDS_RE.search(html)
        logger.info(
            "Ubisoft: game page offer types %s, offerEnds %s.",
            sorted(types), ends.group(1) if ends else None,
        )
        if "GIVEAWAY" not in types:
            return ClaimResult(
                outcome="failed",
                detail=(
                    "Ubisoft's page for this game no longer shows a giveaway - "
                    "it has probably ended."
                ),
            )
        return ClaimResult(
            outcome="not_eligible",
            reason_code=REASON_DESKTOP_CLIENT,
            detail=(
                "Ubisoft hands this giveaway to the Ubisoft Connect PC app: "
                "\"Get it now\" opens Ubisoft Connect rather than claiming in "
                "the browser, and Trove does not drive desktop apps. Open the "
                "game's page and press \"Get it now\" with Ubisoft Connect PC "
                "installed; Trove will see it is owned afterwards."
            ),
        )

    async def _on_store_page(self, page: Page, offer: FreeOffer, pid: str) -> ClaimResult:
        """The older store.ubisoft.com product page: one giveaway click, proven free."""
        user = await _read_user_settled(page)
        if isinstance(user, dict) and user.get("isLoggedIn") is False:
            raise NeedsAttention(
                "This account is signed out of the Ubisoft Store. Sign in again "
                "with \"Sign in here\".",
                await self._shot(page, offer, "signed-out"),
            )
        ids = {pid, str(offer.extra.get("master_id") or "")} - {""}
        if isinstance(user, dict) and ownership_has(user.get("ownership"), ids):
            return ClaimResult(outcome="already_owned", detail="Already in your Ubisoft library.")
        if await _first_visible(page, OWNED, timeout_ms=1500):
            return ClaimResult(outcome="already_owned", detail="Already in your Ubisoft library.")
        if await _first_visible(page, REGION_LOCKED, timeout_ms=800):
            return self._region_locked()

        button = await _first_visible(page, GIVEAWAY_BUTTON, timeout_ms=6000)
        if button is None:
            return ClaimResult(
                outcome="not_eligible",
                reason_code=REASON_NO_CONTROL,
                detail=(
                    "The Ubisoft product page shows no giveaway button, so "
                    "nothing was clicked. The giveaway may have ended, or "
                    "Ubisoft changed the page."
                ),
            )
        classes = (await button.get_attribute("class")) or ""
        text = (await button.inner_text()) or ""
        if not is_free_button(classes, text):
            raise NeedsAttention(
                f"Found a giveaway-shaped button that reads {text.strip()!r} "
                "and could not prove it is free, so nothing was clicked.",
                await self._shot(page, offer, "price-unproven"),
            )

        logger.info("Ubisoft: claiming %r (button %r).", offer.title, text.strip())
        try:
            await button.click(timeout=5000)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The giveaway button could not be clicked. Run and watch to see why.",
                await self._shot(page, offer, "click-blocked"),
            ) from None

        # One click; from here on, only reading.
        for _ in range(12):
            await asyncio.sleep(1.0)
            await self._guard(page, offer)
            if await _first_visible(page, REGION_LOCKED, timeout_ms=300):
                return self._region_locked()
            if await _first_visible(page, LOGIN, timeout_ms=300):
                raise NeedsAttention(
                    "Ubisoft asked for a sign-in at the giveaway. Sign in again "
                    "with \"Sign in here\".",
                    await self._shot(page, offer, "signed-out"),
                )
            if await _first_visible(page, CONFIRMED, timeout_ms=300):
                return ClaimResult(outcome="claimed", detail="Added to your Ubisoft library.")
        if await self.is_owned(page, offer):
            return ClaimResult(outcome="claimed", detail="Added to your Ubisoft library.")
        raise NeedsAttention(
            "The giveaway button was pressed but Ubisoft did not confirm the "
            "claim. Check the account before Trove tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    # ── Shared ───────────────────────────────────────────────────────────

    def _region_locked(self) -> ClaimResult:
        return ClaimResult(
            outcome="not_eligible",
            reason_code=REASON_REGION,
            detail="Ubisoft says this giveaway is not available in your location.",
        )

    async def _challenged(self, page: Page) -> bool:
        try:
            if is_challenge_title(await page.title()):
                return True
        except Exception:
            pass
        return bool(await _first_visible(page, CHALLENGE, timeout_ms=700))

    async def _guard(self, page: Page, offer: FreeOffer) -> None:
        """Stop on a challenge at the claim. Trove never solves one.

        Routed to the un-driven window, Epic's proven remedy: `CheckoutBlocked`
        carries the offer so "Finish the claim here" opens its product page in
        the plain Chrome with nothing attached.
        """
        if await self._challenged(page):
            raise CheckoutBlocked(
                "Ubisoft put up a challenge at the giveaway. Finish this one "
                "claim by hand in the sign-in window - press “Finish the "
                "claim here”.",
                await self._shot(page, offer, "challenge"),
                offer_id=offer.external_id,
            )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))


# How long to let the store pick a session up before believing "signed out".
# Measured on the live install, 23 Nov 2026: a signed-in profile, opened fresh,
# is served `window.User.isLoggedIn: false` first; the page then checks the
# Ubisoft Connect session itself, reloads, and reads `true` with the avatar in
# the header - seconds later. Reading once at DOMContentLoaded reported a
# signed-in account as signed out (Epic's 0.1.39 lesson in another shape).
SESSION_SETTLE_S = 15.0


async def _read_user_settled(page: Page, seconds: float = SESSION_SETTLE_S):
    """`window.User`, waiting out the store's own session pickup.

    Returns as soon as it reads signed in; otherwise keeps looking until the
    time is up and returns the last thing it read (None if never readable).
    Reads only - the page does the pickup on its own.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    last = None
    while True:
        user = await _read_user(page)
        if isinstance(user, dict):
            if user.get("isLoggedIn") is True:
                return user
            last = user
        if loop.time() >= deadline:
            if isinstance(last, dict) and last.get("isLoggedIn") is False:
                logger.info("Ubisoft: still signed out after %.0fs on %s.", seconds, page.url)
            return last
        try:
            await page.wait_for_timeout(1000)
        except Exception:
            return last


async def _read_user(page: Page):
    """The store's `window.User`, or None where there is none (ubisoft.com)."""
    try:
        return await page.evaluate(
            "() => (typeof window.User === 'object' && window.User) ? "
            "JSON.parse(JSON.stringify(window.User)) : null"
        )
    except Exception as exc:
        logger.debug("Ubisoft: could not read window.User: %s", exc)
        return None
