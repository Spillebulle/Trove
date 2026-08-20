"""Epic Games Store.

The weekly giveaway is the main event of this whole app, so this is the first
adapter and the one the others are written against.

**Discovery is free and needs no account.** Epic publishes its own promotions
as public JSON at `freeGamesPromotions`, which is what the store's own front
page reads. No key, no session, no browser. That is the boundary CLAUDE.md
asks for: the app can know what is free without spending a session finding out,
and the browser only wakes up when there is something to claim.

The filter is two conditions and it needs both. `promotions.promotionalOffers`
being non-empty means a promotion is *running now* rather than announced for
next week, and `price.totalPrice.discountPrice == 0` means the promotion makes
it free rather than merely cheaper. Checked against the live endpoint: at the
time of writing, "Epic Mage Bundle" has a discount price of 0 with no current
offer (it is next week's), and every upcoming giveaway still shows its full
price. Either condition alone claims the wrong thing.

**Claiming needs a real browser.** Epic's checkout is behind bot detection even
at a price of zero, and the implementations in the wild all drive a browser
rather than the GraphQL API for exactly that reason. The flow is: open the
purchase URL for the offer, place the order, agree to whatever Epic wants
agreed to, and read the result off the page.

A caveat this file must carry until it is not true. The JSON endpoint above is
verified against the live service. The **selectors in the claim flow are
not** - they were written from how the flow is known to work and have not been
run against a signed-in account, because there is no account to run them
against here. They are therefore all in one table at the top, each with what it
is looking for, so the first person with an account can fix them in one place
rather than hunting through the flow. Anything unrecognised stops the run and
files the account for attention with a screenshot, which is the correct
behaviour whether the page changed or the selector was always wrong.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ..browser import NeedsAttention, screenshot, screenshot_name
from .base import BaseAdapter, ChallengeWaiter, ClaimResult, FreeOffer, Requirement

logger = logging.getLogger(__name__)

# The store's own promotions endpoint. The `-ipv4` host is the one that answers
# reliably from a container with no IPv6 route; the plain hostname resolves to
# an IPv6 address that a default Docker network cannot reach, which presents as
# a hang rather than an error.
PROMOTIONS_URL = (
    "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
)

STORE_ROOT = "https://store.epicgames.com"
# The country decides which promotions are listed and what the prices are, and
# a giveaway is not always the same in every region. This is the one place it
# is stated.
LOCALE = "en-US"
COUNTRY = "US"


def _purchase_url(namespace: str, offer_id: str) -> str:
    """The checkout page for one offer.

    Going straight here rather than to the product page and clicking "Get" is
    deliberate: the product page renders the same checkout inside an iframe,
    and driving a cross-origin iframe adds a frame lookup that breaks whenever
    Epic changes the container. This is the same URL that button navigates to.
    """
    return (
        f"{STORE_ROOT}/purchase"
        f"?highlightColor=0078f2&offers=1-{namespace}-{offer_id}"
        f"&orderId&purchaseToken&showNavigation=true"
    )


# ── The selectors ───────────────────────────────────────────────────────────
#
# Every string Epic could change, in one table. Each is a Playwright selector
# and each is tried in order until one is visible, because Epic runs several
# variants of the checkout at once and a single selector has never survived a
# year.
#
# UNVERIFIED against a signed-in account. See the module docstring.

# The account is signed out. Any of these on the page means the session is gone
# and no amount of clicking will fix it: the user has to sign in by hand.
SIGNED_OUT = [
    'a[href*="/login"]:has-text("Sign In")',
    'button:has-text("Sign In")',
    '#login-with-epic',
]

# The account is signed in. Epic's header shows an account button once there is
# a session.
SIGNED_IN = [
    '[data-testid="user-avatar"]',
    'button[aria-label*="Account"]',
    'a[href*="/account/personal"]',
]

# A challenge only a person can answer. Finding one of these is not a failure
# and not something to retry: it is the attention queue, immediately.
CHALLENGE = [
    'iframe[src*="hcaptcha"]',
    'iframe[src*="arkoselabs"]',
    'iframe[title*="captcha" i]',
    'iframe[title*="challenge" i]',
    '#h_captcha_challenge_login_prod',
    'text=/verify (your|it.s) you/i',
    'text=/security check/i',
]

# The button that places a zero-price order. Epic runs several checkouts at
# once and renames this button often, so this list is long on purpose and is
# the first thing to extend when a claim stops with "could not find the button
# that places the order". Watch a run (Run and watch) to read the real label
# off the screen, then add it here.
PLACE_ORDER = [
    # The current one, verified from a real checkout (Aug 2026): a free game's
    # purchase overlay says "This is free. Add it to your library to get
    # started." with a single "Add to library" button, and the age/EULA consent
    # is folded into that click rather than a separate step. This is the button
    # that actually claims, so it leads.
    'button:has-text("Add to library")',
    'button:has-text("Add To Library")',
    'button:has-text("Add to Library")',
    # Older and paid-flow variants, kept because Epic runs several checkouts at
    # once and renames this button often. Extend this list, do not replace it.
    'button:has-text("Place Order")',
    'button:has-text("Get Now")',
    'button:has-text("Confirm")',
    'button[data-testid="purchase-cta-button"]',
    '[data-testid="purchase-cta-button"]',
    'button.payment-btn',
]

# A device-compatibility notice ("This product is not compatible with your
# current device") that Epic can put up before the checkout button. It is a
# warning, not a challenge, and the store's own "Get" flow shows a "Continue"
# to move past it. Dismissed best-effort; absent for most games and for the
# free game verified in Aug 2026, which went straight to "Add to library".
COMPAT_CONTINUE = [
    'div[role="dialog"] button:has-text("Continue")',
    'button:has-text("Continue")',
]

# Epic asks for agreement to a refund policy or an end user licence before the
# order goes through. It is one click and it is not a challenge.
AGREEMENT = [
    'button:has-text("I Agree")',
    'button:has-text("Accept")',
    'label:has-text("I have read")',
]

# The order went through.
CONFIRMED = [
    'text=/thank you for (your order|buying)/i',
    'text=/thanks for your (order|purchase)/i',
    'text=/order (complete|confirmed|successful)/i',
    'text=/success/i',
    'text=/(in|added to) your library/i',
    'text=/you now own/i',
    '[data-testid="purchase-confirmation"]',
]

# The account already has it. Epic says so on the product page and refuses the
# purchase page outright.
OWNED = [
    'text=/in library/i',
    'text=/you (already )?own this/i',
    'text=/owned/i',
]

# The offer cannot be claimed by this account: wrong region, age rating,
# already-redeemed. Not a failure of the app.
NOT_ELIGIBLE = [
    'text=/not available in your region/i',
    'text=/cannot be purchased in your (region|country)/i',
    'text=/this product is not available/i',
]


async def _goto(page: Page, url: str) -> None:
    """Navigate, tolerating a navigation the page replaced under us.

    Epic redirects during load - a locale redirect, a sign-in bounce, and
    Cloudflare's interstitial handing back to the store - and Playwright reports
    a navigation that the renderer superseded as `net::ERR_ABORTED`, even though
    the browser is now sitting on a perfectly good page. Treating that as a
    failure made a healthy signed-in account read as a broken one: measured, a
    session check on a real profile raised ERR_ABORTED and returned a 500 while
    the store had in fact loaded.

    So an abort is not an error here. It is a cue to wait for whatever replaced
    it and let the caller judge the page it actually got. A timeout still
    raises, because that genuinely means nothing arrived.
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
    """The first of these selectors that is actually on screen, or None.

    Short timeout by design. This asks "which of these several shapes is the
    page in", and a long wait on the wrong shape is a long wait per candidate.
    The waiting for a page to *become* something is done by the caller, once.
    """
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except PlaywrightTimeout:
            continue
        except Exception as exc:  # a malformed selector is a bug, not a page state
            logger.debug("Selector %r did not resolve: %s", selector, exc)
    return None


class EpicAdapter(BaseAdapter):
    store = "epic"
    display_name = "Epic Games Store"
    login_url = f"{STORE_ROOT}/{LOCALE}/"
    # The email/password form. `www.epicgames.com/id/login` is where the store's
    # own "Sign In" lands; going straight there is what the assisted sign-in
    # needs, and it saves a person a click.
    signin_url = "https://www.epicgames.com/id/login"
    blurb = (
        "Claims the weekly giveaway. Sign in once through the live view and "
        "Trove reuses that session; it never stores your password."
    )

    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". Epic challenges "
                "a fresh login, so this is the step Trove cannot do for you."
            ),
        ),
        Requirement(
            name="Two-factor codes",
            description=(
                "If your account has two-factor sign-in, keep your "
                "authenticator to hand for that first sign-in."
            ),
            required=False,
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        params = {"locale": LOCALE, "country": COUNTRY, "allowCountries": COUNTRY}
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                PROMOTIONS_URL,
                params=params,
                # Epic answers a bare client, but a request with no user agent
                # is the kind of thing that gets a CDN rule written about it.
                headers={"User-Agent": "Trove/0.1 (self-hosted free game claimer)"},
            )
            response.raise_for_status()
            payload = response.json()

        try:
            elements = payload["data"]["Catalog"]["searchStore"]["elements"]
        except (KeyError, TypeError):
            logger.error("Epic's promotions payload was not the shape expected.")
            return []

        offers: list[FreeOffer] = []
        for element in elements:
            offer = _parse_element(element)
            if offer is not None:
                offers.append(offer)
        return offers

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        try:
            await _goto(page, self.login_url)
        except PlaywrightTimeout:
            return False, "The Epic store did not load in time."

        if await _first_visible(page, CHALLENGE):
            return False, "Epic is showing a challenge that needs a person."
        if await _first_visible(page, SIGNED_IN, timeout_ms=4000):
            return True, "Signed in."
        if await _first_visible(page, SIGNED_OUT):
            return False, "This account is signed out. Sign in again with \"Sign in here\"."
        # Neither shape. Say so rather than guessing: an unrecognised page is
        # the thing that most often means Epic changed something, and reporting
        # it as "signed out" would send the user to sign in to no effect.
        return False, "Could not tell whether this account is signed in."

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        if not offer.url:
            return None
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            return None
        if await _first_visible(page, OWNED, timeout_ms=3000):
            return True
        # Not finding the marker is not the same as not owning it: the page may
        # simply not have rendered the library state yet. The claim flow checks
        # again, and Epic itself refuses a second purchase.
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        namespace = offer.extra.get("namespace")
        offer_id = offer.extra.get("offer_id")
        if not namespace or not offer_id:
            return ClaimResult(
                outcome="failed",
                detail="This offer has no Epic namespace, so it cannot be claimed.",
            )

        url = _purchase_url(namespace, offer_id)
        try:
            await _goto(page, url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "Epic's checkout did not load. It may be under load, or the "
                "session may have expired.",
                await self._shot(page, offer, "checkout-timeout"),
            ) from None

        await self._guard(page, offer, waiter)

        if await _first_visible(page, OWNED, timeout_ms=2000):
            return ClaimResult(
                outcome="already_owned", detail="Already in your library."
            )
        if await _first_visible(page, NOT_ELIGIBLE, timeout_ms=1000):
            return ClaimResult(
                outcome="not_eligible",
                detail="Epic will not sell this to your account, usually a region limit.",
            )

        # A device-compatibility "Continue" can sit in front of the button on
        # some titles; step past it if it is there. Best-effort and quick, so a
        # game that never shows one (the common case) is not slowed.
        compat = await _first_visible(page, COMPAT_CONTINUE, timeout_ms=1000)
        if compat is not None:
            logger.info("Epic checkout: dismissing a device-compatibility notice.")
            await compat.click()

        logger.info("Epic checkout: looking for the order button for %r.", offer.title)
        order = await _first_visible(page, PLACE_ORDER, timeout_ms=10000)
        if order is None:
            raise NeedsAttention(
                "Could not find the button that adds the game to your library. "
                "Epic has probably changed the checkout. Run this with "
                "\"Run and watch\" to see the page, then the button's label "
                "goes in PLACE_ORDER.",
                await self._shot(page, offer, "no-order-button"),
            )

        logger.info("Epic checkout: clicking the order button.")
        await self._click_through(page, offer, order, PLACE_ORDER, waiter)

        # One agreement click, if Epic asks. Not a loop: if it asks twice,
        # something is wrong and a person should look at it.
        agree = await _first_visible(page, AGREEMENT, timeout_ms=4000)
        if agree is not None:
            await self._click_through(page, offer, agree, AGREEMENT, waiter)
            confirm = await _first_visible(page, PLACE_ORDER, timeout_ms=3000)
            if confirm is not None:
                await self._click_through(page, offer, confirm, PLACE_ORDER, waiter)

        # The order was placed and now the page has to say what happened. This
        # is the one long wait in the flow, because a zero-price order still
        # goes through Epic's payment pipeline.
        await self._guard(page, offer, waiter)
        logger.info("Epic checkout: order placed, waiting for confirmation.")
        if await _first_visible(page, CONFIRMED, timeout_ms=20000):
            logger.info("Epic checkout: confirmed %r.", offer.title)
            return ClaimResult(outcome="claimed", detail="Added to your library.")
        if await _first_visible(page, OWNED, timeout_ms=2000):
            return ClaimResult(
                outcome="already_owned", detail="Already in your library."
            )

        # The confirmation banner's wording changes and a missing one is not
        # proof of failure - ownership is. Ask the product page directly: if the
        # account now owns it, the "Add to library" click worked whatever the
        # overlay said. This is the ground truth the banner only hints at.
        if offer.url:
            logger.info("Epic checkout: no banner; checking the library directly.")
            try:
                await _goto(page, offer.url)
                if await _first_visible(page, OWNED, timeout_ms=6000):
                    logger.info("Epic checkout: %r is now in the library.", offer.title)
                    return ClaimResult(outcome="claimed", detail="Added to your library.")
            except PlaywrightTimeout:
                pass

        raise NeedsAttention(
            "The game was ordered but Trove could not confirm it landed in the "
            "library. Check the account before Trove tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    # ── Shared ───────────────────────────────────────────────────────────

    async def _guard(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> None:
        """Stop on anything only a person can answer.

        Called before and after the order click, because Epic can raise a
        challenge at either point. This is the whole of the app's captcha
        strategy: notice it, and either wait for a person to solve it on the
        screen (a watched run, ``waiter`` given) or stop and ask (a scheduled
        run). Trove never solves it.
        """
        if await _first_visible(page, CHALLENGE, timeout_ms=1200):
            await self._handle_challenge(page, offer, waiter)
        if await _first_visible(page, SIGNED_OUT, timeout_ms=1000):
            raise NeedsAttention(
                "This account is signed out of Epic. Sign in again in the live "
                "view.",
                await self._shot(page, offer, "signed-out"),
            )

    async def _handle_challenge(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None
    ) -> None:
        """A captcha is up. Wait for the person to solve it, or stop.

        Trove does not and will not solve a captcha: it is against the store's
        terms, it is exactly what bot detection is built to catch, and a
        solved-by-a-robot captcha is the fastest route to a locked account. So
        in a watched run the person solves it on the screen and this waits for
        that; in a scheduled run it stops and asks for a watched run.
        """
        if waiter is None:
            raise NeedsAttention(
                "Epic is asking for a captcha at checkout, and Trove never "
                "solves these itself. Press \"Run and watch\" and answer it on "
                "the screen \u2014 the claim carries on the moment it clears.",
                await self._shot(page, offer, "challenge"),
            )
        logger.info("Epic checkout: a captcha is up; waiting for it to be solved.")

        async def _cleared() -> bool:
            return await _first_visible(page, CHALLENGE, timeout_ms=600) is None

        await waiter.wait(_cleared)

    async def _click_through(
        self,
        page: Page,
        offer: FreeOffer,
        locator,
        selectors: list[str],
        waiter: ChallengeWaiter | None,
    ) -> None:
        """Click, answering a captcha that pops up on top rather than hammering.

        Epic's Talon can raise an hCaptcha the instant the checkout button is
        pressed, and its iframe then covers the button - which is why the naive
        click looped for forty-five seconds and died with a raw timeout. Here a
        blocked click is read for what it is: if a challenge is on the page it is
        handed to ``_handle_challenge`` (the person solves it on the screen),
        and the button - which may have moved or been replaced - is found again
        and clicked. If nothing is covering it, the block is a real fault worth
        stopping on.
        """
        for _ in range(4):
            try:
                await locator.click(timeout=6000)
                return
            except PlaywrightTimeout:
                if await _first_visible(page, CHALLENGE, timeout_ms=800):
                    await self._handle_challenge(page, offer, waiter)
                    relocated = await _first_visible(page, selectors, timeout_ms=8000)
                    if relocated is None:
                        # The button is gone: the order most likely went through
                        # while the challenge was being solved.
                        return
                    locator = relocated
                    continue
                raise NeedsAttention(
                    "The checkout button could not be clicked - something is "
                    "covering it. Run and watch to see what.",
                    await self._shot(page, offer, "click-blocked"),
                )
        raise NeedsAttention(
            "The checkout button stayed blocked after several tries.",
            await self._shot(page, offer, "click-blocked"),
        )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))


def _parse_element(element: dict) -> FreeOffer | None:
    """One search-store element, if it is free right now.

    Returns None for everything else, which is most of them: the endpoint lists
    next week's giveaway and a handful of ordinary discounts alongside the one
    thing that is actually free today.
    """
    promotions = element.get("promotions") or {}
    current = promotions.get("promotionalOffers") or []
    if not current:
        return None  # announced, not running

    try:
        price = element["price"]["totalPrice"]["discountPrice"]
    except (KeyError, TypeError):
        return None
    if price != 0:
        return None  # a discount, not a giveaway

    namespace = element.get("namespace")
    offer_id = element.get("id")
    title = element.get("title")
    if not (namespace and offer_id and title):
        return None

    window = {}
    try:
        window = current[0]["promotionalOffers"][0]
    except (IndexError, KeyError, TypeError):
        pass

    return FreeOffer(
        external_id=f"{namespace}:{offer_id}",
        title=title,
        url=_product_url(element),
        image_url=_image_url(element),
        kind=_kind(element),
        starts_at=_parse_date(window.get("startDate")),
        ends_at=_parse_date(window.get("endDate")),
        source="store",
        extra={"namespace": namespace, "offer_id": offer_id},
    )


def _product_url(element: dict) -> str | None:
    """The store page for an offer.

    Three fields carry a slug and which one is populated varies by offer, which
    is why this tries all of them. Checked against the live endpoint: of eleven
    listed promotions, seven had a null `productSlug` and every one of them had
    a `catalogNs` mapping. A `productSlug` also arrives with a `/home` suffix
    that the URL does not want.
    """
    slug = None
    for mapping in (element.get("catalogNs") or {}).get("mappings") or []:
        if mapping.get("pageSlug"):
            slug = mapping["pageSlug"]
            break
    if not slug:
        for mapping in element.get("offerMappings") or []:
            if mapping.get("pageSlug"):
                slug = mapping["pageSlug"]
                break
    if not slug:
        slug = element.get("productSlug") or element.get("urlSlug")
    if not slug:
        return None
    slug = slug.split("/")[0]
    return f"{STORE_ROOT}/{LOCALE}/p/{slug}"


def _image_url(element: dict) -> str | None:
    """The widest artwork Epic offers for the tile.

    The offer's picture is content rather than chrome (STYLE-GUIDE 7.21), so it
    is worth picking properly: the wide image first, because the offer cards in
    the interface are landscape, then anything with a URL.
    """
    images = element.get("keyImages") or []
    by_type = {image.get("type"): image.get("url") for image in images if image.get("url")}
    for wanted in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail", "OfferImageTall"):
        if by_type.get(wanted):
            return by_type[wanted]
    return next(iter(by_type.values()), None)


def _kind(element: dict) -> str:
    """What claiming this gets you.

    Epic's `offerType` is its own vocabulary; the ledger has three words. A
    bundle is a game as far as a person reading the ledger is concerned.
    """
    offer_type = (element.get("offerType") or "").upper()
    if offer_type == "ADD_ON":
        return "dlc"
    if element.get("isCodeRedemptionOnly"):
        return "key"
    return "game"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Epic sends `2026-08-13T15:00:00.000Z`, and `fromisoformat` before
        # Python 3.11 refuses the trailing Z rather than reading it as UTC.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        logger.debug("Could not read the date %r from Epic.", value)
        return None
