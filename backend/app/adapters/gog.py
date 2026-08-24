"""GOG.com.

GOG runs occasional giveaways - a banner on the store's front page with a
countdown and one button - rather than Epic's weekly cadence. There may be
nothing to claim for weeks, which is fine: discovery is cheap and says so.

**Discovery is free and needs no account.** GOG's front page is assembled from
a public sections API, and the giveaway is a section like any other. Measured
against the live service (Aug 2026):

    GET https://sections.gog.com/v1/pages/2f?countryCode=US&locale=en-US&currencyCode=USD
      -> {"config": {"pageUrl": "/", ...}, "sections": [{"sectionId": ...,
          "sectionType": "TAKEOVER_SECTION" | "PRODUCTS_SECTION" | ...}, ...]}

`2f` is the front page: the page id is the URL-encoded page path, and `%2f`
is `/`. A giveaway appears in that list as `sectionType: "GIVEAWAY_SECTION"`
(the store's own enum in its bundle names it, and its renderer is a lazy chunk
that was read in full), and the section's content is one more request:

    GET https://sections.gog.com/v1/pages/2f/sections/<sectionId>?<same params>
      -> {"properties": {...}, "colors": [...]}

For the giveaway, `properties` carries `endDate` (the countdown) and
`product` - and the product DTO, measured on a live section of another type,
is `{"id": "1889754300", "title": ..., "slug": "the_blood_of_dawnwalker",
"coverHorizontal": "https://images.gog-statics.com/....png", ...}`. The `id`
is the stable GOG product id and is the offer's identity; a title or slug is
not, and neither is ever used as one.

**No giveaway was live when this was written**, so the giveaway *section
body* itself is unverified: the parser is written against the renderer's own
code (which reads exactly `properties.endDate`, `properties.product.title`,
`.slug`, `.coverHorizontal`) and against the measured product DTO shape, and
it logs what it finds so the first live giveaway is the measurement.

**Claiming is one button on the front page.** The banner renders as a
`<giveaway>` element with `id="giveaway"`, and its one button is labelled
"Add to library" (`.giveaway__button`). Behind the button the store does
`POST https://www.gog.com/giveaway/claim` with the session's bearer token and
navigates to `/giveaway/thank-you` on success; a previously-claimed giveaway
renders the banner in its claimed state ("Success! <title> was added to your
library.") instead of the button. `GET https://www.gog.com/giveaway/status`
answers `{"isClaimed": bool}` for a signed-in session and 401
`{"message":"Unauthorized"}` for anybody else (measured), which is the ground
truth this adapter checks after clicking rather than trusting the page.
Trove clicks the store's own button rather than posting to the endpoint
itself: the click is what a person does, and the store's own code attaches
the token and the consent that go with it.

**Claiming subscribes the account to GOG's marketing newsletter.** That is
GOG's own deal, written next to the button (measured, from the banner's
template): "By clicking the 'Add to library' button, I agree to receive
marketing communications from GOG...". Trove does not and must not touch the
newsletter settings afterwards - silently unsubscribing would be Trove
undoing a consent the person gave, and it is the person's to manage. It is
stated in the blurb so nobody is surprised.

**The session check reads the account's own data, not the menu.** The menu on
gog.com is rendered client-side and its markup was not measurable without a
browser, so the reliable signal is `GET https://www.gog.com/user/data/games`:
signed out it 302s to the front page with `#openlogin` appended (measured);
signed in it returns the account's owned product ids as JSON (community
knowledge - lgogdownloader and friends read it - the signed-in shape is
unverified here and the code says so where it parses it). That same list is
`is_owned`, with the product page as the honest "could not tell" fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ..browser import CheckoutBlocked, NeedsAttention, screenshot, screenshot_name
from .base import (
    BaseAdapter,
    ChallengeWaiter,
    ClaimResult,
    FreeOffer,
    Requirement,
)

logger = logging.getLogger(__name__)

STORE_ROOT = "https://www.gog.com"
# The language decides the front page variant; the giveaway itself is the same.
LOCALE = "en-US"
COUNTRY = "US"
CURRENCY = "USD"

# The sections API the front page is assembled from. Public: answers a plain
# client with Trove's own user agent (measured), no session, no key. `2f` is
# the URL-encoded page path `/`, i.e. the front page.
SECTIONS_ROOT = "https://sections.gog.com/v1/pages/2f"
SECTION_PARAMS = {"countryCode": COUNTRY, "locale": LOCALE, "currencyCode": CURRENCY}
GIVEAWAY_SECTION_TYPE = "GIVEAWAY_SECTION"

# The store's own giveaway endpoints, the ones the banner's button calls.
# `status` answers {"isClaimed": bool} for a signed-in session (and 401 for
# anybody else - measured), which makes it the ground truth after a click.
FRONT_PAGE = f"{STORE_ROOT}/en"
STATUS_URL = f"{STORE_ROOT}/giveaway/status"
THANK_YOU_URL = f"{STORE_ROOT}/giveaway/thank-you"

# The signed-in session's own library, as product ids. Signed out this 302s to
# the front page with `#openlogin` appended (measured); signed in it returns
# JSON (community knowledge; shape read defensively below).
USER_GAMES_URL = f"{STORE_ROOT}/user/data/games"


# ── The selectors ───────────────────────────────────────────────────────────
#
# Everything GOG could change, in one table, same as Epic. The banner
# selectors come from the giveaway section's own renderer (its lazy chunk was
# read in full, Aug 2026): container `id="giveaway"`, one button
# `.giveaway__button` labelled "Add to library", state classes
# `giveaway__content--{loading,claimed,ready}`, success text "Success! <title>
# was added to your library.", error text "Something went wrong. Please try
# again." They are read from code rather than from a rendered page - no
# giveaway was live to render one - so the first live giveaway verifies them.

# A challenge only a person can answer. GOG's login uses Google reCAPTCHA
# (measured on login.gog.com); the rest are the usual suspects, kept for the
# day GOG fronts a page with them.
CHALLENGE = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="arkoselabs"]',
    'iframe[title*="captcha" i]',
    'iframe[title*="challenge" i]',
    'text=/verify you are human/i',
    'text=/security check/i',
]

# The account is signed out and the page is asking for a login: the login
# form's own field (measured on login.gog.com: `login[username]`), or the
# login service framed into the store.
LOGIN_FORM = [
    'input[name="login[username]"]',
    'iframe[src*="login.gog.com"]',
    'form[action*="login_check"]',
]

# The giveaway banner and its parts, from the renderer's template.
GIVEAWAY_BANNER = [
    "#giveaway",
    "giveaway",
]
CLAIM_BUTTON = [
    '#giveaway button:has-text("Add to library")',
    "#giveaway .giveaway__button",
    ".giveaway__button",
]
# Rendered when this account has already claimed it (state `Claimed` on load).
ALREADY_CLAIMED = [
    "#giveaway .giveaway__content--claimed",
    'text=/was added to your library/i',
]
# The banner's own failure toast after a click.
CLAIM_ERROR = [
    'text=/something went wrong\\. please try again/i',
]


async def _goto(page: Page, url: str) -> None:
    """Navigate, tolerating a navigation the page replaced under us.

    Same tolerance as Epic's, for the same reason: a store that redirects
    during load (a locale redirect, the signed-out `#openlogin` bounce) makes
    Playwright report `net::ERR_ABORTED` on a page that in fact loaded, and
    treating that as a failure turns a healthy account into a broken one. A
    timeout still raises, because that genuinely means nothing arrived.
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
    honoured (the list's first match wins a round), and an absent group costs
    one timeout rather than one per selector.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while True:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible():
                    return locator
            except Exception as exc:  # a malformed selector is a bug, not a state
                logger.debug("Selector %r did not resolve: %s", selector, exc)
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(0.1)


def _product_id(offer: FreeOffer) -> str | None:
    """The GOG product id this offer is about, however the offer arrived.

    A freshly discovered offer carries it in `extra["product_id"]`; an offer
    rebuilt from a stored row only has `external_id`, which for GOG *is* the
    product id (possibly behind a prefix, so the tail after any colon is
    taken).
    """
    pid = offer.extra.get("product_id")
    if pid:
        return str(pid)
    tail = (offer.external_id or "").rpartition(":")[2]
    return tail or None


class GogAdapter(BaseAdapter):
    store = "gog"
    display_name = "GOG"
    login_url = FRONT_PAGE
    # The store front with its own login dialog opened - the same URL GOG
    # itself bounces a signed-out account to (measured: /user/data/games
    # redirects to /en##openlogin). The dialog frames login.gog.com, which is
    # where the email/password form and its reCAPTCHA live.
    signin_url = f"{STORE_ROOT}/#openlogin"
    blurb = (
        "Claims GOG's occasional giveaways from the store front page. Sign in "
        "once through the sign-in window and Trove reuses that session. Heads "
        "up: GOG's own giveaway terms subscribe you to their marketing "
        "newsletter when a giveaway is claimed - Trove never changes your "
        "newsletter settings, so unsubscribe on GOG if you do not want it."
    )

    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". GOG's login has "
                "a reCAPTCHA, so this is the step Trove cannot do for you."
            ),
        ),
        Requirement(
            name="The newsletter side effect",
            description=(
                "Claiming a GOG giveaway signs you up for GOG's marketing "
                "emails - that is GOG's condition, not Trove's. Trove will "
                "not touch the setting; unsubscribe on GOG if unwanted."
            ),
            required=False,
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """What the front page's giveaway section says, or nothing.

        Two requests at most, no session, no browser: the front page's section
        list, and - only when a `GIVEAWAY_SECTION` is in it - that section's
        content. Most weeks the first request answers "no giveaway" by itself.
        """
        headers = {"User-Agent": "Trove/0.1 (self-hosted free game claimer)"}
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            response = await client.get(SECTIONS_ROOT, params=SECTION_PARAMS)
            response.raise_for_status()
            payload = response.json()

            sections = payload.get("sections")
            if not isinstance(sections, list):
                logger.error("GOG's sections payload was not the shape expected.")
                return []

            offers: list[FreeOffer] = []
            for section in sections:
                if section.get("sectionType") != GIVEAWAY_SECTION_TYPE:
                    continue
                section_id = section.get("sectionId")
                if not section_id:
                    continue
                body = await client.get(
                    f"{SECTIONS_ROOT}/sections/{section_id}", params=SECTION_PARAMS
                )
                body.raise_for_status()
                offer = _parse_giveaway(body.json())
                if offer is not None:
                    offers.append(offer)
            return offers

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        """Signed in or not, read off the account's own data rather than the menu.

        gog.com's menu is rendered client-side and its markup could not be
        measured, so the deterministic signal is `/user/data/games`: signed out
        it bounces to the front page with `#openlogin` (measured), signed in it
        is the library as JSON. One navigation either way.
        """
        try:
            await _goto(page, USER_GAMES_URL)
        except PlaywrightTimeout:
            return False, "GOG did not load in time."

        if await _first_visible(page, CHALLENGE):
            return False, "GOG is showing a challenge that needs a person."
        if "openlogin" in (page.url or ""):
            return False, "This account is signed out. Sign in again with \"Sign in here\"."
        if await self._read_owned_ids(page) is not None:
            return True, "Signed in."
        if await _first_visible(page, LOGIN_FORM):
            return False, "This account is signed out. Sign in again with \"Sign in here\"."
        # Neither shape. Say so rather than guessing, same as Epic: an
        # unrecognised page most often means GOG changed something, and calling
        # it "signed out" would send the user to sign in to no effect.
        return False, "Could not tell whether this account is signed in."

    async def _read_owned_ids(self, page: Page) -> set[str] | None:
        """The signed-in library as product ids, or None if unreadable.

        The page at `/user/data/games` is a JSON document; its signed-in shape
        is community knowledge (an `owned` list of product ids) and is read
        defensively: anything else - signed out, changed shape, an HTML error
        page - is None, never an empty set, because "could not read the
        library" must not become "owns nothing".
        """
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception as exc:
            logger.debug("Could not read the GOG library page: %s", exc)
            return None
        try:
            data = json.loads(text or "")
        except ValueError:
            return None
        owned = data.get("owned") if isinstance(data, dict) else None
        if not isinstance(owned, list):
            logger.info(
                "GOG's /user/data/games did not carry an 'owned' list; keys were %r.",
                sorted(data.keys()) if isinstance(data, dict) else type(data).__name__,
            )
            return None
        return {str(pid) for pid in owned}

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        pid = _product_id(offer)
        if not pid:
            return None
        try:
            await _goto(page, USER_GAMES_URL)
        except PlaywrightTimeout:
            return None
        owned = await self._read_owned_ids(page)
        if owned is None:
            # Could not read the library - signed out, or the endpoint changed.
            # None, not False: recording "not owned" off a failed check teaches
            # the ledger a lie.
            return None
        return pid in owned

    # ── Claiming ─────────────────────────────────────────────────────────

    def checkout_url(self, offer: FreeOffer) -> str | None:
        """Where a person finishes this by hand: the front page's banner.

        GOG's giveaway is claimed where it is shown - there is no separate
        checkout page - so the un-driven window opens on the store front and
        the person presses the same "Add to library" button there.
        """
        return FRONT_PAGE

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        try:
            await _goto(page, FRONT_PAGE)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "GOG's front page did not load, so the giveaway banner could "
                "not be reached.",
                await self._shot(page, offer, "front-timeout"),
            ) from None

        await self._guard(page, offer)

        banner = await _first_visible(page, GIVEAWAY_BANNER, timeout_ms=8000)
        if banner is None:
            # The giveaway the discovery saw is not on the page: most likely it
            # ended between discovery and this run. A failed row with the
            # reason, not an attention item - the account is fine.
            return ClaimResult(
                outcome="failed",
                detail=(
                    "GOG's front page is not showing the giveaway banner - the "
                    "giveaway has probably ended."
                ),
            )

        if await _first_visible(page, ALREADY_CLAIMED, timeout_ms=2500):
            return ClaimResult(
                outcome="already_owned", detail="Already claimed on this account."
            )

        button = await _first_visible(page, CLAIM_BUTTON, timeout_ms=8000)
        if button is None:
            raise NeedsAttention(
                "Could not find the giveaway's \"Add to library\" button. GOG "
                "has probably changed the banner; run this with \"Run and "
                "watch\" to read the real label, then it goes in CLAIM_BUTTON.",
                await self._shot(page, offer, "no-claim-button"),
            )

        # One click, once. The store's own code posts the claim and navigates
        # to the thank-you page on success; everything after this is reading
        # what happened, never clicking again.
        logger.info("GOG: claiming %r.", offer.title)
        try:
            await button.click(timeout=5000)
        except PlaywrightTimeout:
            await self._guard(page, offer)
            raise NeedsAttention(
                "The giveaway button could not be clicked - something is "
                "covering it. Run and watch to see what.",
                await self._shot(page, offer, "click-blocked"),
            ) from None

        outcome = await self._read_claim_outcome(page, offer)
        if outcome is not None:
            return outcome

        # No signal on the page. Ask the store rather than assuming: the status
        # endpoint answers {"isClaimed": bool} for this session (measured shape
        # from the store's own code; 401 for a signed-out one, measured live).
        logger.info("GOG: no confirmation on the page; asking /giveaway/status.")
        claimed = await self._status_is_claimed(page)
        if claimed is True:
            return ClaimResult(outcome="claimed", detail="Added to your library.")
        raise NeedsAttention(
            "The giveaway button was clicked but GOG did not confirm the "
            "claim. Check the account before Trove tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    async def _read_claim_outcome(self, page: Page, offer: FreeOffer) -> ClaimResult | None:
        """What the page says the click came to, watched for a bounded while."""
        for _ in range(12):
            await asyncio.sleep(1.0)
            url = page.url or ""
            if "/giveaway/thank-you" in url:
                logger.info("GOG: %r claimed (thank-you page).", offer.title)
                return ClaimResult(outcome="claimed", detail="Added to your library.")
            if "openlogin" in url or await _first_visible(page, LOGIN_FORM, timeout_ms=300):
                raise NeedsAttention(
                    "This account is signed out of GOG. Sign in again with "
                    "\"Sign in here\".",
                    await self._shot(page, offer, "signed-out"),
                )
            await self._guard(page, offer)
            if await _first_visible(page, ALREADY_CLAIMED, timeout_ms=500):
                logger.info("GOG: %r claimed (banner says so).", offer.title)
                return ClaimResult(outcome="claimed", detail="Added to your library.")
            if await _first_visible(page, CLAIM_ERROR, timeout_ms=300):
                raise NeedsAttention(
                    "GOG answered the claim with its \"Something went wrong\" "
                    "message. One attempt only; check the account.",
                    await self._shot(page, offer, "claim-error"),
                )
        return None

    async def _status_is_claimed(self, page: Page) -> bool | None:
        """`/giveaway/status` for this session, read defensively."""
        try:
            await _goto(page, STATUS_URL)
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            data = json.loads(text or "")
        except Exception as exc:
            logger.debug("Could not read GOG's giveaway status: %s", exc)
            return None
        if isinstance(data, dict) and isinstance(data.get("isClaimed"), bool):
            return data["isClaimed"]
        logger.info("GOG's /giveaway/status was not the shape expected: %r", data)
        return None

    # ── Shared ───────────────────────────────────────────────────────────

    async def _guard(self, page: Page, offer: FreeOffer) -> None:
        """Stop on a challenge. Trove never solves one.

        A captcha at the claim is routed to the un-driven window, same remedy
        as Epic's checkout captcha: `CheckoutBlocked` carries the offer so the
        “Finish the claim here” button opens the front page (the
        banner is the checkout) in the plain Chrome with no CDP on it. Whether
        GOG would even accept a driven solve has not been tested - the
        un-driven window is the path that is already proven, so it is the one
        offered.
        """
        if await _first_visible(page, CHALLENGE, timeout_ms=700):
            raise CheckoutBlocked(
                "GOG put up a challenge at the giveaway. Finish this one claim "
                "by hand in the sign-in window - press “Finish the claim "
                "here”.",
                await self._shot(page, offer, "challenge"),
                offer_id=offer.external_id,
            )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))


def _parse_giveaway(body: dict) -> FreeOffer | None:
    """One giveaway section's content, as an offer.

    Written against the section renderer's own code (it reads exactly
    `properties.endDate` and `properties.product.{title,slug,coverHorizontal}`)
    and the measured product DTO of a sibling section, which also carries the
    stable `id`. Logs what it finds either way: no giveaway was live to run
    this against, so the first live one is the measurement.
    """
    properties = body.get("properties") or {}
    product = properties.get("product") or {}
    product_id = product.get("id")
    title = product.get("title")
    if not product_id or not title:
        # Without the product id there is no stable identity to dedupe on, and
        # a slug or title must not stand in for one. Skip loudly so the next
        # session can fix the parser from what the payload actually held.
        logger.error(
            "GOG's giveaway section had no product id/title; properties keys "
            "were %r, product keys %r.",
            sorted(properties.keys()),
            sorted(product.keys()),
        )
        return None

    slug = product.get("slug")
    url = f"{STORE_ROOT}/en/game/{slug.replace('-', '_')}" if slug else None
    kind = "dlc" if (product.get("productType") or "").lower() in ("dlc", "pack") else "game"

    offer = FreeOffer(
        external_id=str(product_id),
        title=title,
        url=url,
        image_url=product.get("coverHorizontal"),
        kind=kind,
        ends_at=_parse_date(properties.get("endDate")),
        source="store",
        extra={"product_id": str(product_id), "slug": slug},
    )
    logger.info(
        "GOG giveaway: %r (product %s), ends %s.", title, product_id, offer.ends_at
    )
    return offer


def _parse_date(value: str | None) -> datetime | None:
    """A sections-API datetime, e.g. `2026-08-24T14:59:59+02:00` (measured on a
    sibling section's `visibleTo`); the giveaway's `endDate` is fed to the
    JavaScript `Date.parse`, so ISO 8601 with an offset or a trailing Z."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        logger.debug("Could not read the date %r from GOG.", value)
        return None
