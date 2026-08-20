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

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ..browser import CheckoutBlocked, NeedsAttention, screenshot, screenshot_name
from .base import (
    BaseAdapter,
    BaseGame,
    ChallengeWaiter,
    ClaimResult,
    FreeOffer,
    Requirement,
)

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
# The consent buttons Epic puts up mid-checkout: the "Right of Withdrawal"
# dialog ("I accept"), an EULA ("I Agree"), a refund notice ("Accept"). Verified
# Aug 2026: a free claim shows the Right of Withdrawal dialog after "Add to
# library" (and after the captcha, when there is one), and the order only goes
# through once it is accepted. Dialog-scoped variants lead so the click lands on
# "I accept" and never on the "Cancel" beside it. "Accept" is a substring, so it
# also catches "I accept".
ACCEPT = [
    'div[role="dialog"] button:has-text("I accept")',
    'div[role="dialog"] button:has-text("I Accept")',
    'div[role="dialog"] button:has-text("Accept")',
    'div[role="dialog"] button:has-text("I Agree")',
    'button:has-text("I accept")',
    'button:has-text("I Agree")',
    'button:has-text("Accept")',
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

# The product page's call to action, which is how a person tells at a glance
# whether they can have a game: "Get" for a free or free-to-play title, "Buy
# Now" (or a price) for a paid one, "In Library" once it is owned. Read rather
# than clicked - this is the DLC prerequisite check, not a purchase.
PRODUCT_CTA = [
    '[data-testid="purchase-cta-button"]',
    'button[data-testid="purchase-cta-button"]',
    'aside button:has-text("Get")',
    'aside button:has-text("Buy Now")',
    'button:has-text("Buy Now")',
    'button:has-text("Get")',
]

# A price on the product page, for saying *how* paid a base game is when a DLC
# is skipped over it. Any currency: the account's country decides the symbol.
PRODUCT_PRICE = [
    '[data-testid="purchase-discount-price"]',
    '[data-component="PriceLayout"]',
    'span:has-text("$")',
    'span:has-text("€")',
    'span:has-text("£")',
]

# An error Epic raises while processing the order - the "An error occurred while
# trying to process your request. Please check your network connection and try
# again." toast. Seen after the order button was clicked a second time while the
# first order was still in flight; the checkout loop now avoids that, and if the
# error appears anyway it is shown rather than clicked into again.
ERROR = [
    'text=/error occurred while trying to process your request/i',
    'text=/please check your network connection/i',
]


# Epic's own backend hosts, the ones the checkout talks to rather than the
# CDN/Cloudflare front. A failure here - not on the store page - is what shows
# as "an error occurred, check your network connection", and the `.ol.` hosts
# are the ones this app has already seen resolve to an IPv6 address a container
# cannot reach (discovery uses an `-ipv4` host for the same reason).
_EPIC_BACKEND = (
    ".ol.epicgames.com",
    "payment-website-pci",
    "account-public-service",
    "eulatracking",
    "/purchase/",
    "store.epicgames.com/graphql",
)


def _is_epic_backend(url: str) -> bool:
    return any(fragment in url for fragment in _EPIC_BACKEND)


def _net_note(net_errors) -> str:
    """A sentence naming the failed backend requests, for an attention reason."""
    if not net_errors:
        return ""
    # The last few are the ones that matter; the order request is last.
    tail = "; ".join(net_errors[-3:])
    return f" The failing request(s): {tail}."


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
    """The first visible element matching any of these selectors, or None.

    **All the selectors are waited on together, in one `timeout_ms`.** The old
    version waited the full timeout for *each* selector in turn, so asking "is
    there a captcha?" against seven selectors when there is none cost seven
    timeouts - about eight seconds - and the checkout loop paid that several
    times over. Playwright's `or_` unions the locators so a single `wait_for`
    watches all of them at once, and `filter(visible=True)` keeps a hidden
    element that happens to sort first in the DOM from masking a visible match.
    """
    # Poll all the selectors in priority order until one is visible or the
    # timeout passes. The order matters and must be honoured: the dialog-scoped
    # "I accept" has to win over a stray "Accept" elsewhere on the page, and the
    # real add-to-library button over a look-alike. `is_visible()` is an instant
    # check with no per-selector wait, so a whole round costs milliseconds; the
    # timeout is spent sleeping between rounds, not multiplied by the number of
    # selectors. So this is both correct (priority kept) and fast (an absent
    # group costs one timeout, not one per selector) - the earlier `or_` version
    # was fast but lost the order, which is what made it click the wrong thing.
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

    async def inspect_base_game(self, page: Page, offer: FreeOffer) -> BaseGame | None:
        """The game this add-on belongs to: is it owned, and can it be had free?

        A free DLC is only worth claiming if the account owns the game it
        extends, and Epic gives away add-ons for games it does not give away -
        the free "Epic Mage Bundle" is for Albion Online. So before spending a
        checkout on one, ask about the game.

        The relationship comes free with discovery: an add-on shares its
        namespace with its base game, and `catalogNs.mappings[productHome]` is
        the base game's page (measured Aug 2026). This loads that page once and
        reads three things off it, the way a person would:

        * **Owned** - the "In Library" marker, the same one `is_owned` uses.
        * **Free** - the call to action. "Get" means free or free-to-play; "Buy
          Now" or a price means paid. That is the store's own summary of the
          question, rather than a price parsed out of the markup.
        * **How to claim it** - the offer id, which the page carries in the
          purchase links it builds. Without it the game can be reported but not
          bought, which is still worth saying.

        Everything it could not establish stays `None`. A page that has changed
        shape produces "could not tell", and the runner then skips the DLC with
        that as the reason rather than claiming into the dark.
        """
        base_url = offer.extra.get("base_url")
        if not base_url:
            return None
        try:
            await _goto(page, base_url)
        except PlaywrightTimeout:
            logger.info("Epic: the base game's page (%s) did not load.", base_url)
            return BaseGame(title="the base game", url=base_url)

        title = None
        try:
            title = (await page.title() or "").split("|")[0].strip() or None
        except Exception:
            pass

        owned: bool | None = None
        if await _first_visible(page, OWNED, timeout_ms=3000):
            owned = True

        cta = await _first_visible(page, PRODUCT_CTA, timeout_ms=4000)
        cta_text = ""
        if cta is not None:
            try:
                cta_text = " ".join((await cta.inner_text() or "").split())
            except Exception:
                cta_text = ""
        lowered = cta_text.lower()

        free: bool | None = None
        if "in library" in lowered or "owned" in lowered:
            owned = True
        elif "get" in lowered:
            free = True
        elif "buy" in lowered or any(sym in cta_text for sym in "$€£¥"):
            free = False

        price_note = None
        if free is False:
            price = await _first_visible(page, PRODUCT_PRICE, timeout_ms=1500)
            if price is not None:
                try:
                    price_note = " ".join((await price.inner_text() or "").split())[:24]
                except Exception:
                    pass
            price_note = price_note or (cta_text[:24] or None)

        base_offer = None
        offer_id = await self._base_offer_id(page, offer)
        if offer_id:
            base_offer = FreeOffer(
                external_id=f"{offer.extra.get('namespace')}:{offer_id}",
                title=title or "the base game",
                url=base_url,
                kind="game",
                extra={
                    "namespace": offer.extra.get("namespace"),
                    "offer_id": offer_id,
                    "account_id": offer.extra.get("account_id"),
                },
            )

        logger.info(
            "Epic: %r needs %r - owned=%s free=%s cta=%r claimable=%s.",
            offer.title, title or base_url, owned, free, cta_text, bool(base_offer),
        )
        return BaseGame(
            title=title or "the base game",
            url=base_url,
            owned=owned,
            free=free,
            price_note=price_note,
            offer=base_offer,
        )

    async def _base_offer_id(self, page: Page, offer: FreeOffer) -> str | None:
        """The base game's offer id, read off its own product page.

        Epic's public catalog API refuses a plain HTTP client (measured: the
        store's GraphQL answers 403 to anything that is not a browser), so this
        is asked of the page that is already open - which is signed in and past
        Cloudflare, and therefore the one client that can see it. The page
        builds its own purchase links, and an offer id is 32 hex characters
        under the same namespace the add-on carries.
        """
        namespace = offer.extra.get("namespace")
        if not namespace:
            return None
        try:
            content = await page.content()
        except Exception as exc:
            logger.debug("Could not read the base game's page: %s", exc)
            return None
        # A purchase link, which is unambiguous about what the id is for.
        found = re.search(rf"1-{re.escape(namespace)}-([0-9a-f]{{32}})", content)
        if found:
            return found.group(1)
        # Failing that, an offerId the page carries for its own use. Only
        # trusted when there is exactly one, so a page listing add-ons as well
        # as the game cannot hand back the wrong one.
        ids = set(re.findall(r'"offerId"\s*:\s*"([0-9a-f]{32})"', content))
        ids.discard(offer.extra.get("offer_id") or "")
        if len(ids) == 1:
            return ids.pop()
        logger.info(
            "Epic: could not work out how to claim the base game (%d candidate ids).",
            len(ids),
        )
        return None

    def checkout_url(self, offer: FreeOffer) -> str | None:
        """The purchase page for one offer, for the un-driven window to finish.

        The same URL a run drives to, opened this time in a browser with no CDP
        on it - the one Epic's Talon captcha will actually accept a solve from.
        """
        namespace = offer.extra.get("namespace")
        offer_id = offer.extra.get("offer_id")
        if not namespace or not offer_id:
            return None
        return _purchase_url(namespace, offer_id)

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

        # Watch Epic's backend requests, so a checkout that fails names the
        # request that failed instead of leaving us to guess. This is what turns
        # "an error occurred" into "POST .../confirm-order -> net::ERR_...".
        net_errors: list[str] = []

        def _on_request_failed(request) -> None:
            if _is_epic_backend(request.url):
                where = request.url.split("?", 1)[0]
                msg = f"{request.method} {where} -> {request.failure}"
                net_errors.append(msg)
                logger.warning("Epic backend request FAILED: %s", msg)

        def _on_response(response) -> None:
            try:
                if response.status < 400 or not _is_epic_backend(response.url):
                    return
            except Exception:  # a response object mid-teardown is not worth failing on
                return
            request = response.request
            where = response.url.split("?", 1)[0]
            net_errors.append(f"{request.method} {where} -> HTTP {response.status}")

            async def _detail() -> None:
                # Epic's *response* body is its own error message (not a secret),
                # and it says WHY a 400 is a 400 - a captcha it rejected, a field
                # it wanted. Whether the *request* carried a captcha token is the
                # other half; we log the yes/no, never the token itself.
                try:
                    body = (await response.text())[:400]
                except Exception:
                    body = "<body unavailable>"
                token_sent = False
                try:
                    post = request.post_data or ""
                    token_sent = any(
                        k in post.lower() for k in ("captcha", "talon", "token", "arkose")
                    )
                except Exception:
                    pass
                logger.warning(
                    "Epic backend %s %s -> HTTP %s | captcha-token-in-request=%s | response: %s",
                    request.method, where, response.status, token_sent, body,
                )

            try:
                asyncio.get_event_loop().create_task(_detail())
            except Exception:
                pass

        page.on("requestfailed", _on_request_failed)
        page.on("response", _on_response)
        try:
            return await self._claim(page, offer, waiter, namespace, offer_id, net_errors)
        finally:
            try:
                page.remove_listener("requestfailed", _on_request_failed)
                page.remove_listener("response", _on_response)
            except Exception:
                pass

    async def _claim(
        self, page, offer, waiter, namespace, offer_id, net_errors
    ) -> ClaimResult:
        url = _purchase_url(namespace, offer_id)
        try:
            await _goto(page, url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "Epic's checkout did not load. It may be under load, or the "
                "session may have expired." + _net_note(net_errors),
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

        # Drive the checkout: click whatever it puts up next - Add to library,
        # then the Right of Withdrawal / EULA "I accept", a device-compat
        # "Continue" - until it confirms. A step-loop rather than a fixed
        # order, because Epic interleaves these differently per title and a
        # captcha can land between any two of them.
        outcome = await self._drive_checkout(page, offer, waiter, net_errors)
        if outcome is not None:
            detail = {
                "claimed": "Added to your library.",
                "already_owned": "Already in your library.",
                "not_eligible": "Epic will not sell this to your account, usually a region limit.",
            }[outcome]
            return ClaimResult(outcome=outcome, detail=detail)

        # No banner is not proof of failure - ownership is. Ask the product page
        # directly: if the account now owns it, the checkout worked whatever the
        # overlay said.
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
            "library. Check the account before Trove tries again." + _net_note(net_errors),
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
        if await _first_visible(page, CHALLENGE, timeout_ms=700):
            await self._handle_challenge(page, offer, waiter)
        if await _first_visible(page, SIGNED_OUT, timeout_ms=600):
            raise NeedsAttention(
                "This account is signed out of Epic. Sign in again in the live "
                "view.",
                await self._shot(page, offer, "signed-out"),
            )

    async def _handle_challenge(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None
    ) -> None:
        """A captcha is up at checkout. Route it to the un-driven window.

        Trove does not and will not solve a captcha. And the thing this app
        learned the hard way (Aug 2026, from a real account): a captcha at
        Epic's checkout **cannot be solved in the driven browser at all**, even
        by a person. The order request that follows a solve returns HTTP 400
        ``epic.error.captcha.challenge.failed`` with the token attached - Talon
        refuses the solve because the browser is CDP-driven, and that is
        structural, not a fingerprint to patch.

        So this no longer waits for a solve on the driven screen (the old
        ``waiter`` path, now proven futile for checkout). It raises
        `CheckoutBlocked`, which tells the person to finish this one order in
        the **un-driven** sign-in window - the plain Chrome, no CDP, that Epic's
        captcha already accepts for sign-in. ``waiter`` is left in the signature
        for the adapter contract, but a checkout captcha does not use it.
        """
        logger.info(
            "Epic checkout: a captcha is up; the driven browser cannot pass it, "
            "so stopping for the un-driven window."
        )
        # A screenshot for context in the notification - what is waiting, not a
        # puzzle for Trove to solve.
        shot = await self._shot(page, offer, "captcha")
        raise CheckoutBlocked(
            "Epic put up a captcha to finish this order, and it will not accept "
            "a solve from Trove's automated browser - so this one claim has to "
            "be finished by hand in the sign-in window, the same browser you "
            "signed in with. Press \u201cFinish the claim here\u201d.",
            shot,
            offer_id=offer.external_id,
        )

    async def _drive_checkout(
        self, page: Page, offer: FreeOffer, waiter, net_errors=None
    ) -> str | None:
        """Click through the checkout until it confirms. Returns an outcome or None.

        Phase-aware, and that matters. **The add-to-library button is clicked at
        most once.** The first version clicked "whatever is on the page next",
        and after the consent dialog was accepted it still saw the add-to-library
        button behind the (now closing) dialog and clicked it again - a second
        order racing the first, which Epic answers with "An error occurred while
        trying to process your request." So once the order is placed the loop
        never touches that button again: it only answers a consent dialog, shows
        a real error, or waits for the confirmation.

        Each turn: stop on a captcha or sign-out (`_guard`); return if the page
        says confirmed / owned / not-eligible; answer a consent dialog if one is
        up; stop on an error toast; otherwise, in the opening phase click the
        add-to-library button (once), and after that just wait for the order to
        go through. Bounded, so a checkout that never resolves stops rather than
        spins. None means "clicked but saw no confirmation" - the caller then
        checks the library directly.
        """
        phase = "start"  # start -> ordered -> accepted
        for _ in range(14):
            await self._guard(page, offer, waiter)
            if await _first_visible(page, CONFIRMED, timeout_ms=600):
                logger.info("Epic checkout: confirmed %r.", offer.title)
                return "claimed"
            if await _first_visible(page, OWNED, timeout_ms=500):
                return "already_owned"
            if await _first_visible(page, NOT_ELIGIBLE, timeout_ms=400):
                return "not_eligible"

            # A consent dialog is answered first whatever the phase - it is the
            # thing in front of the person, and the order is not placed until it
            # is accepted.
            accept = await _first_visible(page, ACCEPT, timeout_ms=700)
            if accept is not None:
                logger.info("Epic checkout: accepting the consent dialog.")
                if await self._click(page, offer, accept, waiter):
                    phase = "accepted"
                    await page.wait_for_timeout(1000)
                continue

            # An error Epic raised while processing. Show it rather than clicking
            # into it again.
            if await _first_visible(page, ERROR, timeout_ms=400) is not None:
                raise NeedsAttention(
                    "Epic returned an error at the checkout (its \"could not "
                    "process your request\" message)." + _net_note(net_errors)
                    + " If a request to one of Epic's `.ol.epicgames.com` hosts "
                    "failed to connect, it is the container's networking - most "
                    "likely IPv6; see the README.",
                    await self._shot(page, offer, "checkout-error"),
                )

            if phase == "start":
                compat = await _first_visible(page, COMPAT_CONTINUE, timeout_ms=500)
                if compat is not None:
                    logger.info("Epic checkout: dismissing a device notice.")
                    await self._click(page, offer, compat, waiter)
                    await page.wait_for_timeout(600)
                    continue
                order = await _first_visible(page, PLACE_ORDER, timeout_ms=8000)
                if order is None:
                    raise NeedsAttention(
                        "Could not find the button that adds the game to your "
                        "library. Epic has probably changed the checkout. Run "
                        "this with \"Run and watch\" to see the page, then the "
                        "button's label goes in PLACE_ORDER or ACCEPT.",
                        await self._shot(page, offer, "no-order-button"),
                    )
                logger.info("Epic checkout: adding %r to the library.", offer.title)
                if await self._click(page, offer, order, waiter):
                    phase = "ordered"
                    await page.wait_for_timeout(1000)
                # If the click did not land (a captcha was handled), loop and
                # re-read: the order button is still there to click once.
                continue

            # Past the order button, with no dialog and no error: the order is
            # being placed. **Do not touch the add-to-library button again.**
            # Just wait and let the top of the loop catch the confirmation.
            await page.wait_for_timeout(1200)
        return None

    async def _click(self, page: Page, offer: FreeOffer, locator, waiter) -> bool:
        """Click once, answering a captcha rather than hammering. True if it landed.

        Epic's Talon can raise an hCaptcha the instant a checkout button is
        pressed, and its iframe then covers the button - which is why the naive
        click looped for forty-five seconds and died with a raw timeout. A
        blocked click is read for what it is: if a challenge is on the page it is
        handed to `_handle_challenge` (the person solves it on the screen) and
        this returns False so the caller re-reads the page; if nothing is
        covering it, the block is a real fault worth stopping on.
        """
        try:
            label = " ".join((await locator.inner_text() or "").split())[:40]
        except Exception:
            label = "?"
        logger.info("Epic checkout: clicking %r.", label)
        try:
            await locator.click(timeout=5000)
            return True
        except PlaywrightTimeout:
            if await _first_visible(page, CHALLENGE, timeout_ms=800):
                await self._handle_challenge(page, offer, waiter)
                return False
            raise NeedsAttention(
                "A checkout button could not be clicked - something is covering "
                "it. Run and watch to see what.",
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
        extra={
            "namespace": namespace,
            "offer_id": offer_id,
            # Only set for an add-on: the page of the game it belongs to, so a
            # DLC claim can check the prerequisite before spending a checkout on
            # something the account cannot use.
            "base_url": _base_game_url(element),
        },
    )


def _slug_url(slug: str | None) -> str | None:
    if not slug:
        return None
    # A `productSlug` arrives with a `/home` suffix that the URL does not want.
    return f"{STORE_ROOT}/{LOCALE}/p/{slug.split('/')[0]}"


def _mapping_slug(element: dict, key: str, page_type: str | None = None) -> str | None:
    """The first `pageSlug` under `element[key]`, optionally of one page type."""
    mappings = element.get(key)
    if isinstance(mappings, dict):  # catalogNs carries its list one level down
        mappings = mappings.get("mappings")
    for mapping in mappings or []:
        if not mapping.get("pageSlug"):
            continue
        if page_type and mapping.get("pageType") != page_type:
            continue
        return mapping["pageSlug"]
    return None


def _product_url(element: dict) -> str | None:
    """The store page **for this offer**, which for a DLC is not the game's page.

    Measured against the live endpoint (Aug 2026), and this is the trap: an
    add-on's `catalogNs.mappings` points at the **base game**, and only
    `offerMappings` points at the add-on. The free "Epic Mage Bundle" lists
    `catalogNs → albion-online-7eb24d` (pageType `productHome`) and
    `offerMappings → albion-online-epic-mage-bundle-2ceb19` (pageType `offer`).

    So preferring `catalogNs` first - which this did - gave a DLC the base
    game's URL, and `is_owned` then answered a question nobody asked: whether
    the account owns *Albion Online*, reported as whether it owns the bundle.
    That is precisely the "teaches the ledger a lie" case the contract warns
    about. For an add-on the offer's own mapping leads; for everything else the
    old order stands, since `catalogNs` is the field most often populated (seven
    of eleven promotions had a null `productSlug`).
    """
    if (element.get("offerType") or "").upper() == "ADD_ON":
        return (
            _slug_url(_mapping_slug(element, "offerMappings", "offer"))
            or _slug_url(_mapping_slug(element, "offerMappings"))
            or _slug_url(element.get("urlSlug"))
            or _slug_url(_mapping_slug(element, "catalogNs"))
        )
    return (
        _slug_url(_mapping_slug(element, "catalogNs"))
        or _slug_url(_mapping_slug(element, "offerMappings"))
        or _slug_url(element.get("productSlug") or element.get("urlSlug"))
    )


def _base_game_url(element: dict) -> str | None:
    """The page of the game a DLC belongs to, or None if this is not a DLC.

    `catalogNs.mappings` with pageType `productHome` is the base product, and a
    DLC shares its namespace with the game it extends - which is what makes the
    relationship discoverable at all, without a second request.
    """
    if (element.get("offerType") or "").upper() != "ADD_ON":
        return None
    return _slug_url(
        _mapping_slug(element, "catalogNs", "productHome")
        or _mapping_slug(element, "catalogNs")
    )


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
