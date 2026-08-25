"""Steam.

**Steam has no weekly giveaway, and this adapter must not pretend it does.**
What exists - and what this adapter claims, exclusively - is the occasional
"free to keep" promotion: a normally-paid game temporarily discounted 100 %
that stays in the library forever once added. NOT free-to-play games (nothing
to claim; installing them is not a promotion), and NOT free weekends (a trial:
the game leaves with the weekend). Claiming a free-to-keep title is one "Add
to Account" click on the store page in a signed-in session - the same free
license grant ArchiSteamFarm's `addlicense` has exercised at scale for years,
so the click itself is a benign, ordinary store action.

**Discovery is Steam's own search, sessionless.** No Reddit, no SteamDB, no
third-party feed - their terms are unchecked and CLAUDE.md forbids leaning on
an unchecked feed. Measured live (24 Aug 2026):

* `search/results/?...&maxprice=free&specials=1&infinite=1` answers JSON
  `{success, results_html, total_count, start}`. `specials=1` means "a
  discount is running" and `maxprice=free` means "the discounted price is
  free", so together they are exactly "discounted to nothing" - a free-to-keep
  promotion. On the day of measurement `total_count` was 0, which is the
  normal state: these promotions are rare and irregular.
* The `json=1` variant of the same search returns only `{name, logo}` per
  item - no appid, no price, no discount - so it cannot positively identify
  anything and is not used.
* `results_html` rows (measured against live specials rows) carry
  `data-ds-appid`, a `<span class="title">`, `data-discount` (the percentage)
  and `data-price-final` (cents). Rows for packages/bundles carry
  `data-ds-packageid` instead of an appid and are skipped with a log line.
* `api/appdetails?appids=N` confirms a candidate: a paid discounted game
  showed `is_free: False` with `price_overview: {discount_percent: 38,
  final: 495}`, a free-to-play game showed `is_free: True` with no
  `price_overview` at all. So `discount_percent == 100 and final == 0` is the
  positive identification of free-to-keep, and it is what tells one apart
  from a free *weekend*: the weekend is playtime, not price, so the weekend
  title's `price_overview` keeps its real (non-zero) price. That 100/0 branch
  is written from the measured field shapes, not from a live promotion -
  none was running - so it is flagged unverified in CLAUDE.md.
* `api/featuredcategories` has a `specials` bucket whose items carry
  `discount_expiration` (a unix timestamp) - the only end-date any of these
  endpoints offers - so it fills `ends_at` best-effort.

**The claim is one click on the app page.** The page-side facts, measured
signed-out (24 Aug 2026): the "Add to Account" control ships as a
`div.btn_addtocart.btn_addtoaccount` containing a `span[role="button"]` whose
`onclick` is a free-license action (`AddFreeBundle(...)` on the measured
pages; `AddFreeLicense` is the same family for single packages), with the
tooltip "This game is available for free to your account." A paid purchase is
`Add to Cart` via `addToCart(...)` / `addBundleToCart(...)` and **must never
be clicked** - the money guard below refuses any control it cannot positively
establish as free. The age gate is a *form*, not a challenge: an age-limited
app redirects to `/agecheck/app/<appid>/` with selects `#ageDay` (values
"1".."31"), `#ageMonth` ("January"...), `#ageYear` ("1900"...) and an
`#view_product_page_btn` "View Page" button - stepping through it is fine and
needed, and is nothing like solving a captcha.

What could NOT be measured without a live promotion or a signed-in session,
all flagged in CLAUDE.md: the exact purchase block a 100 %-off game renders,
the confirmation wording after the click, and the owned marker ("... is
already in your Steam library", community-known). All of them fail safe: an
unrecognised page stops with a screenshot, and ownership - not a banner - is
the ground truth for "claimed", the same pattern as Epic.

Sign-in is by hand, always: Steam Guard (email or mobile confirmation) makes
an unattended login unrealistic, and Trove's whole design is a session made
once by a person and reused forever. That is not a limitation to work around;
it is the design working.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ..browser import NeedsAttention, screenshot, screenshot_name
from .base import (
    BaseAdapter,
    ChallengeWaiter,
    ClaimResult,
    FreeOffer,
    Requirement,
    SignInForm,
    SignInStep,
)

logger = logging.getLogger(__name__)

STORE_ROOT = "https://store.steampowered.com"

# The store's own search, in its `infinite=1` shape: JSON with the result rows
# as HTML inside it. Chosen over the `json=1` shape because that one returns
# only a name and a thumbnail per item (measured), which cannot identify
# anything; the rows here carry the appid, the discount and the price.
SEARCH_URL = f"{STORE_ROOT}/search/results/"
APPDETAILS_URL = f"{STORE_ROOT}/api/appdetails"
FEATURED_URL = f"{STORE_ROOT}/api/featuredcategories"

# The country decides prices and which promotions exist; the language decides
# the wording every text selector below reads. One place for both.
COUNTRY = "US"
LANGUAGE = "english"

UA = "Trove/0.1 (self-hosted free game claimer)"


def _app_url(appid: str) -> str:
    return f"{STORE_ROOT}/app/{appid}/"


# ── Parsing the search rows ─────────────────────────────────────────────────
#
# One `<a class="search_result_row">` per item. Regex rather than an HTML
# parser because the app takes no new dependencies and the four attributes
# read here are flat and stable (measured against live rows, 24 Aug 2026).

ROW_RE = re.compile(
    r'<a[^>]*\bdata-ds-appid="(?P<appid>\d+)"[^>]*>(?P<body>.*?)</a>', re.S
)
TITLE_RE = re.compile(r'<span class="title">(?P<title>[^<]*)</span>')
DISCOUNT_RE = re.compile(r'data-discount="(?P<pct>\d+)"')
PRICE_FINAL_RE = re.compile(r'data-price-final="(?P<cents>\d+)"')
# A row that is a package or bundle rather than an app. Skipped: a package has
# no appdetails to confirm against, so it cannot be positively identified as
# free-to-keep, and the rule is that anything unconfirmed is not claimed.
PACKAGE_ROW_RE = re.compile(r'data-ds-packageid="\d+"|data-ds-bundleid="\d+"')


# ── The selectors ───────────────────────────────────────────────────────────
#
# Every string Steam could change, in one table, same as Epic - and like
# Epic's, tried in order until one is visible. Which are measured and which
# are community knowledge is noted per list; the module docstring says why.

# Signed out. Measured (24 Aug 2026): the global header of a signed-out page
# carries `#global_action_menu` with a "Sign in" link to `/login/`.
SIGNED_OUT = [
    '#global_action_menu a[href*="/login"]',
    'a[href*="store.steampowered.com/login"]:has-text("Sign in")',
]

# Signed in. `#account_pulldown` is the account-name dropdown in the global
# nav; zero occurrences on every signed-out page fetched, which is what makes
# it a clean discriminator. (Its signed-in presence is community knowledge -
# there was no session to measure with.)
SIGNED_IN = [
    '#account_pulldown',
    '#account_dropdown',
    'a[href*="store.steampowered.com/account/"]',
]

# The age gate. Measured on /agecheck/app/1091500/: three selects and a View
# Page button. It is a form and answering it is fine - see the docstring.
AGE_GATE = [
    '#ageYear',
    '.agegate_birthday_selector',
]
AGE_GATE_VIEW = [
    '#view_product_page_btn',
    'a:has-text("View Page")',
]

# The account already owns it. Community-known wording, NOT yet measured (it
# only renders signed-in): a `game_area_already_owned` banner saying the title
# "is already in your Steam library". Kept broad; `is_owned` answers None when
# none of these show, never False.
OWNED = [
    '.game_area_already_owned',
    'div[class*="already_owned"]',
    'text=/already in your steam library/i',
]

# The free "Add to Account" control - the ONE button this adapter may click.
# A deliberately broad list, like epic.PLACE_ORDER and for the same reason:
# Steam renders several purchase-block variants at once and the exact block a
# 100%-off game renders could not be measured without a live promotion, so
# every plausible shape of the measured control is listed and the first
# visible one wins. Scoped to the purchase area so a stray "Add to Account"
# in a review or description can never match. The money guard below still has
# to pass before any of these is clicked.
ADD_TO_ACCOUNT = [
    # The measured shape: a span[role=button] inside the btn_addtoaccount
    # container, onclick AddFreeLicense/AddFreeBundle.
    '.game_area_purchase_game span[onclick*="AddFreeLicense"]',
    '.game_area_purchase_game .btn_addtoaccount span[role="button"]',
    '.game_area_purchase_game a[href*="freelicense"]',
    '.game_area_purchase_game a[onclick*="AddFreeLicense"]',
    # Label-matched fallbacks for a markup change that keeps the wording.
    '.game_area_purchase_game span[role="button"]:has-text("Add to Account")',
    '.game_area_purchase_game a:has-text("Add to Account")',
    '#game_area_purchase a:has-text("Add to Account")',
    '#game_area_purchase span[role="button"]:has-text("Add to Account")',
]

# The order went through. Wording UNVERIFIED (no promotion to run against);
# ownership is the ground truth either way, this only saves the extra load.
CONFIRMED = [
    'text=/added to your account/i',
    'text=/is now in your (steam )?library/i',
    'text=/successfully added/i',
]

# The store will not grant it to this account (region locks exist on Steam
# too). Community wording, kept narrow.
NOT_ELIGIBLE = [
    'text=/not available in your (country|region)/i',
]

# Free-license actions: structurally incapable of charging money. A control
# whose onclick/href names one of these is safe to click by construction.
_FREE_ACTIONS = ("addfreelicense", "addfreebundle", "freelicense")
# Cart actions: these lead to a paid checkout and are never clicked, whatever
# the surrounding page claims about the price.
_CART_ACTIONS = ("addtocart", "addbundletocart")


async def _goto(page: Page, url: str) -> None:
    """Navigate, tolerating a navigation the page replaced under us.

    The same tolerance as Epic's `_goto` and for the same reason: a redirect
    mid-load (Steam's age gate redirects to `/agecheck/...`, a country
    redirect) can surface as `net::ERR_ABORTED` on a page that in fact
    loaded. A timeout still raises; that genuinely means nothing arrived.
    """
    try:
        await page.goto(url, wait_until="domcontentloaded")
        return
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


async def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 1500):
    """The first visible element matching any of these selectors, or None.

    Epic's polling version, kept identical on purpose: priority order is
    honoured (the specific selector beats the label fallback) and an absent
    group costs one timeout, not one per selector.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while True:
        for selector in selectors:
            try:
                # `visible=true` and not `.first`: a locator's first match is
                # not necessarily a visible one. itch.io renders the sale
                # widget twice - a `visibility: hidden` 0x0 copy for the
                # narrow layout, then the real one - and taking `.first`
                # found the hidden copy, answered "not visible" and reported a
                # live 100% sale as ended. Ask Playwright for the first
                # *visible* match, which is what this function is called.
                locator = page.locator(f"{selector} >> visible=true").first
                if await locator.is_visible():
                    return locator
            except Exception as exc:  # a malformed selector is a bug, not a state
                logger.debug("Selector %r did not resolve: %s", selector, exc)
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(0.1)


class SteamAdapter(BaseAdapter):
    store = "steam"
    display_name = "Steam"
    # The login form directly: it is where a new account starts, and a
    # signed-in visit to it just bounces to the store front.
    login_url = f"{STORE_ROOT}/login/"
    # Reported from the real form, 25 Aug 2026: Steam puts the username and
    # the password on **one page**, with a "Remember me" checkbox and a Sign
    # in button under them. So the step between the two fields is a Tab, not
    # an Enter - an Enter there submits the form with an empty password, which
    # is a failed login attempt, and Steam counts those.
    #
    # It is `email` that types into the username field: the action names what
    # Trove has stored, not what the store calls its field. Steam wants the
    # account name rather than an address, so store the account name there.
    sign_in_forms = [
        SignInForm(
            key="full",
            label="Username and password",
            description=(
                "The two fields with “Remember me” under them. Click the "
                "username field first."
            ),
            steps=[
                SignInStep("email", "Typing your username…"),
                SignInStep("tab", "Moving to the password…", wait_ms=300),
                SignInStep("password", "Typing your password…"),
                SignInStep("enter", "Signing in…", wait_ms=2500),
            ],
        ),
    ]
    sign_in_note = (
        "Click the username field first, and tick “Remember me”, before you "
        "press this. Store your Steam *account name* as the email - Steam does "
        "not take an address. Steam Guard will still ask for a code, which the "
        "Code button types."
    )
    blurb = (
        "Claims Steam's occasional “free to keep” promotions - a paid game "
        "temporarily 100% off that stays in your library. They are rare and "
        "irregular; most runs find nothing, which is normal. Free-to-play "
        "games and free weekends are deliberately left alone, and Trove never "
        "idles cards or mass-adds titles. Steam Guard means the one sign-in "
        "is by hand - which is how Trove works everywhere."
    )

    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". Steam Guard will "
                "ask for a code or a mobile confirmation on that first "
                "sign-in, which is exactly why Trove never signs in for you."
            ),
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """Steam's own search for "discounted to free", confirmed per app.

        Two stages, both against store.steampowered.com and nothing else:
        the search narrows 60-odd-thousand free *to play* items down to the
        handful whose running discount makes them free, and `appdetails` then
        positively confirms each one (`discount_percent == 100`, measured)
        before it is offered to a run. Anything that cannot be confirmed is
        skipped with a log line rather than claimed - a free weekend keeps
        its real price in `price_overview` and falls out here.

        Zero results is the normal answer: free-to-keep promotions are rare
        and irregular. One page of 50 is plenty for an event that has never
        listed more than a handful of titles at once.
        """
        params = {
            "query": "",
            "start": 0,
            "count": 50,
            "maxprice": "free",
            "specials": 1,
            "infinite": 1,
            "cc": COUNTRY,
            "l": LANGUAGE,
        }
        async with httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": UA}
        ) as client:
            response = await client.get(SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()

            if not payload.get("success"):
                logger.error("Steam's search did not answer success=1.")
                return []
            total = payload.get("total_count")
            rows = _parse_rows(payload.get("results_html") or "")
            if total not in (None, 0) and not rows:
                # The count says there is something and the parser saw none:
                # the markup changed, and silence here would hide a live
                # giveaway forever.
                logger.error(
                    "Steam lists %s free special(s) but no row parsed; "
                    "the search markup has probably changed.", total,
                )
                return []

            offers: list[FreeOffer] = []
            expirations: dict[int, int] | None = None
            for row in rows:
                # The row must itself say the price is nothing. `maxprice=free`
                # already filtered, but the claim path never trusts one signal.
                if row["discount"] != 100 and row["cents"] != 0:
                    logger.info(
                        "Steam: skipping %r (appid %s) - listed under the free "
                        "filter but shows discount %s%% / price %s.",
                        row["title"], row["appid"], row["discount"], row["cents"],
                    )
                    continue
                confirmed = await self._confirm_free_to_keep(client, row["appid"])
                if confirmed is None:
                    logger.info(
                        "Steam: skipping %r (appid %s) - could not positively "
                        "confirm it is free to keep.", row["title"], row["appid"],
                    )
                    continue
                if expirations is None:
                    expirations = await self._discount_expirations(client)
                ends = expirations.get(int(row["appid"]))
                offers.append(
                    FreeOffer(
                        external_id=row["appid"],
                        title=confirmed.get("name") or row["title"] or row["appid"],
                        url=_app_url(row["appid"]),
                        image_url=confirmed.get("header_image"),
                        kind="dlc" if confirmed.get("type") == "dlc" else "game",
                        ends_at=(
                            datetime.fromtimestamp(ends, tz=timezone.utc)
                            if ends
                            else None
                        ),
                        source="store",
                        extra={"appid": row["appid"]},
                    )
                )
            return offers

    async def _confirm_free_to_keep(
        self, client: httpx.AsyncClient, appid: str
    ) -> dict | None:
        """appdetails' verdict: the app's data if it is free to keep, else None.

        Measured shapes: a paid discounted game answers `is_free: False` with
        a `price_overview` carrying `discount_percent` and `final`; a
        free-to-play game answers `is_free: True` with **no** `price_overview`
        at all.

        **And a live 100 %-off promotion, measured 25 Aug 2026 on Dokimon
        Quest (2019300), answers this** - which is not what this function was
        first written against:

            {"initial": 1499, "final": 1499, "discount_percent": 100,
             "initial_formatted": "$14.99", "final_formatted": "Free"}

        `final` is the **undiscounted** price, not zero. The original rule was
        `discount_percent == 100 and final == 0`, reasoned from a partially
        discounted game (`discount_percent: 38, final: 495`) where `final` is
        indeed what you pay - and it was flagged unverified for exactly this
        reason. Against the real thing it rejected every free-to-keep game
        there was, silently, so Steam reported "nothing is free" through a
        promotion. Now measured, and the rule is the discount alone.

        That still separates it from everything it must be separated from. A
        free weekend is playtime rather than price, so its `price_overview`
        keeps its real price and a `discount_percent` that is not 100. A
        free-to-play game has no `price_overview` to read, so `.get` yields
        `{}` and the test fails. `final_formatted` is checked as well when it
        is there - Steam writes "Free" in it - but it is not required, because
        it is a localised string and the discount is the fact.

        Anything unreadable fails, which is the conservative direction:
        skipped, never claimed.
        """
        try:
            response = await client.get(
                APPDETAILS_URL,
                params={"appids": appid, "cc": COUNTRY, "l": LANGUAGE},
            )
            response.raise_for_status()
            entry = response.json().get(str(appid)) or {}
        except Exception as exc:
            logger.warning("Steam: appdetails for %s failed: %s", appid, exc)
            return None
        if not entry.get("success"):
            return None
        data = entry.get("data") or {}
        price = data.get("price_overview") or {}
        if price.get("discount_percent") != 100:
            return None
        # Belt and braces where Steam offers it, and never a veto on its own:
        # `final == 0` is how some entries write it and `final_formatted` says
        # "Free" in others, so either confirms and neither is required.
        formatted = str(price.get("final_formatted") or "").strip().lower()
        if price.get("final") == 0 or "free" in formatted or not formatted:
            return data
        logger.info(
            "Steam: %s is at -100%% but its price still reads %r; skipped.",
            appid,
            price.get("final_formatted"),
        )
        return None

    async def _discount_expirations(self, client: httpx.AsyncClient) -> dict[int, int]:
        """appid -> discount_expiration (unix), from featuredcategories.

        The one place any of Steam's own endpoints states when a discount
        ends (measured: the `specials` bucket items carry it). Best-effort: a
        missing entry just leaves `ends_at` empty, and the offer still shows.
        """
        try:
            response = await client.get(
                FEATURED_URL, params={"cc": COUNTRY, "l": LANGUAGE}
            )
            response.raise_for_status()
            items = (response.json().get("specials") or {}).get("items") or []
        except Exception as exc:
            logger.debug("Steam: featuredcategories not readable: %s", exc)
            return {}
        return {
            item["id"]: item["discount_expiration"]
            for item in items
            if item.get("id") and item.get("discount_expiration")
        }

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        try:
            await _goto(page, STORE_ROOT)
        except PlaywrightTimeout:
            return False, "The Steam store did not load in time."
        if await _first_visible(page, SIGNED_IN, timeout_ms=4000):
            return True, "Signed in."
        if await _first_visible(page, SIGNED_OUT):
            return False, (
                "This account is signed out. Sign in again with \"Sign in "
                "here\" - Steam Guard will want its code."
            )
        return False, "Could not tell whether this account is signed in."

    # ── Ownership ────────────────────────────────────────────────────────

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        if not offer.url:
            return None
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            return None
        await self._pass_age_gate(page)
        if await _first_visible(page, OWNED, timeout_ms=3000):
            return True
        # No marker is not "not owned": the page may not have rendered the
        # library state, or the marker may have been reworded. None, per the
        # contract - claiming an owned game is harmless, a false "not owned"
        # row is not.
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        """One attempt: the app page, the age form if any, one free click.

        `waiter` is unused: Steam's free-license grant has no known captcha,
        and if a challenge ever does appear the unrecognised page stops the
        run with a screenshot, which is the correct answer until someone has
        seen the real thing.
        """
        if not offer.url:
            return ClaimResult(
                outcome="failed", detail="This offer has no store page to claim from."
            )
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "Steam's store page did not load. It may be under load, or "
                "the session may have expired.",
                await self._shot(page, offer, "page-timeout"),
            ) from None

        await self._pass_age_gate(page)

        if await _first_visible(page, SIGNED_OUT, timeout_ms=800):
            raise NeedsAttention(
                "This account is signed out of Steam. Sign in again with "
                "\"Sign in here\".",
                await self._shot(page, offer, "signed-out"),
            )
        if await _first_visible(page, OWNED, timeout_ms=2000):
            return ClaimResult(
                outcome="already_owned", detail="Already in your Steam library."
            )
        if await _first_visible(page, NOT_ELIGIBLE, timeout_ms=800):
            return ClaimResult(
                outcome="not_eligible",
                detail="Steam will not grant this to your account, usually a region limit.",
            )

        button = await _first_visible(page, ADD_TO_ACCOUNT, timeout_ms=8000)
        if button is None:
            raise NeedsAttention(
                "Could not find the \"Add to Account\" button on the store "
                "page. Either the promotion has ended, or Steam has changed "
                "the page - run this with \"Run and watch\" to read the real "
                "label, then it goes in steam.ADD_TO_ACCOUNT.",
                await self._shot(page, offer, "no-add-button"),
            )

        # The money guard. This click must be provably free before it lands.
        await self._assert_free_click(page, offer, button)

        logger.info("Steam: adding %r to the account.", offer.title)
        try:
            await button.click(timeout=5000)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The \"Add to Account\" button could not be clicked - "
                "something is covering it. Run and watch to see what.",
                await self._shot(page, offer, "click-blocked"),
            ) from None

        # One click, then read the result; never a second click. The
        # confirmation wording is unverified, so ownership is the ground
        # truth, exactly the Epic pattern: reload the app page and look for
        # the owned marker.
        await page.wait_for_timeout(2500)
        if await _first_visible(page, CONFIRMED, timeout_ms=4000):
            return ClaimResult(outcome="claimed", detail="Added to your Steam library.")
        logger.info("Steam: no confirmation banner; checking the library directly.")
        try:
            await _goto(page, offer.url)
            await self._pass_age_gate(page)
            if await _first_visible(page, OWNED, timeout_ms=6000):
                logger.info("Steam: %r is now in the library.", offer.title)
                return ClaimResult(
                    outcome="claimed", detail="Added to your Steam library."
                )
        except PlaywrightTimeout:
            pass

        raise NeedsAttention(
            "\"Add to Account\" was clicked but Trove could not confirm the "
            "game landed in the library. Check the account before Trove "
            "tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    # ── Shared ───────────────────────────────────────────────────────────

    async def _pass_age_gate(self, page: Page) -> None:
        """Step through the birth-date form, if the app put one up.

        A form, not a challenge: Steam asks a date and shows the page, with
        no bot detection attached (measured: the gate is plain selects at
        `/agecheck/app/<appid>/`). Filling it is what any visitor does, and
        skipping it would make every age-rated giveaway unclaimable. A page
        with no gate is untouched.
        """
        gated = "/agecheck/" in (page.url or "")
        if not gated and not await _first_visible(page, AGE_GATE, timeout_ms=800):
            return
        logger.info("Steam: answering the age-gate form.")
        try:
            await page.select_option("#ageDay", "1", timeout=4000)
            await page.select_option("#ageMonth", "January", timeout=4000)
            await page.select_option("#ageYear", "1990", timeout=4000)
        except Exception as exc:
            logger.info("Steam: the age gate's fields were not fillable: %s", exc)
            return  # let the caller read whatever page this actually is
        view = await _first_visible(page, AGE_GATE_VIEW, timeout_ms=2000)
        if view is None:
            logger.info("Steam: no View Page button on the age gate.")
            return
        try:
            await view.click(timeout=4000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await page.wait_for_timeout(800)
        except Exception as exc:
            logger.info("Steam: stepping past the age gate failed: %s", exc)

    async def _assert_free_click(self, page: Page, offer: FreeOffer, button) -> None:
        """Refuse to click anything not provably free. Raises `NeedsAttention`.

        Two independent ways a click proves itself, either suffices:

        1. **Structurally.** The control's own onclick/href names a
           free-license action (`AddFreeLicense`, `AddFreeBundle`, a
           `/freelicense/` URL). Those endpoints grant licenses; they cannot
           charge. A control naming a *cart* action (`addToCart`,
           `addBundleToCart`) is refused outright whatever else the page
           says, because that is the road to a paid checkout.
        2. **By the displayed price.** The purchase block around the button
           shows `-100%` in its discount marker or a final price of Free /
           0 - the store's own statement that this order costs nothing.

        Neither provable means no click and a screenshot: on a store where a
        neighbouring button spends real money, "probably free" is not a
        standard this app accepts.
        """
        action = ""
        try:
            action = (
                (await button.get_attribute("onclick") or "")
                + " "
                + (await button.get_attribute("href") or "")
            ).lower()
        except Exception:
            action = ""
        if any(cart in action for cart in _CART_ACTIONS):
            raise NeedsAttention(
                "The matched button is an add-to-cart, which leads to a paid "
                "checkout. Trove does not buy anything; the selectors in "
                "steam.ADD_TO_ACCOUNT need correcting.",
                await self._shot(page, offer, "cart-refused"),
            )
        if any(free in action for free in _FREE_ACTIONS):
            return

        # No action to read (markup changed, or the handler is bound in
        # script): fall back to the price the surrounding block displays.
        try:
            block = button.locator(
                'xpath=ancestor-or-self::*'
                '[contains(@class, "game_area_purchase_game")][1]'
            )
            pct = ""
            final = ""
            if await block.count():
                pct_el = block.locator(".discount_pct").first
                if await pct_el.count():
                    pct = (await pct_el.inner_text() or "").strip()
                final_el = block.locator(".discount_final_price").first
                if await final_el.count():
                    final = (await final_el.inner_text() or "").strip()
            if pct.replace("−", "-") == "-100%":
                return
            normalized = final.lower()
            if normalized == "free" or re.fullmatch(
                r"[^\d]*0([.,]0+)?[^\d]*", normalized or " "
            ):
                return
        except Exception as exc:
            logger.info("Steam: could not read the purchase block's price: %s", exc)

        raise NeedsAttention(
            "Found an \"Add to Account\"-shaped button but could not prove "
            "the order is free (no free-license action and no -100% / Free "
            "price beside it), so nothing was clicked. Run and watch to see "
            "the page.",
            await self._shot(page, offer, "price-unproven"),
        )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))


def _parse_rows(results_html: str) -> list[dict]:
    """The search rows, as {appid, title, discount, cents}.

    Measured against live rows (24 Aug 2026): each is one `<a>` carrying
    `data-ds-appid`, with the title, `data-discount` and `data-price-final`
    inside it. Package/bundle rows have no appid and are logged and skipped -
    they cannot be confirmed through appdetails, so they cannot be claimed.
    """
    rows: list[dict] = []
    for match in ROW_RE.finditer(results_html):
        body = match.group("body")
        title = TITLE_RE.search(body)
        discount = DISCOUNT_RE.search(body)
        cents = PRICE_FINAL_RE.search(body)
        rows.append(
            {
                "appid": match.group("appid"),
                "title": title.group("title") if title else None,
                "discount": int(discount.group("pct")) if discount else None,
                "cents": int(cents.group("cents")) if cents else None,
            }
        )
    stripped = ROW_RE.sub("", results_html)
    for match in PACKAGE_ROW_RE.finditer(stripped):
        logger.info(
            "Steam: skipping a package/bundle row (%s) - only apps can be "
            "confirmed free to keep.", match.group(0),
        )
    return rows
