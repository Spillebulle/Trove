"""IndieGala.

IndieGala runs "galaFreebies": a standing page of indie games that a signed-in
account can add to its IndieGala library for nothing, DRM-free. The page says
"Every week, we bring you a new free PC game", and older ones stay listed, so
this is closer to itch.io than to Epic: several small free games at once and
no deadline on any of them.

**Discovery, measured against the live site (23 Nov 2026), sessionless:**

* `GET https://freebies.indiegala.com/` answers 200 `text/html` (23.5 kB,
  served through Cloudflare, `server: cloudflare`) to a plain httpx client
  with Trove's own `Trove/0.1` user agent. **No Cloudflare challenge** was
  served to that client on any of ten requests spaced 3.5 s apart.
  `https://www.indiegala.com/freebies` is a **404**; the subdomain is the
  page.
* There is no JSON endpoint behind it: the grid is server-rendered. Each game
  is one cell:

      <div class="col-3 products-col">
        <div class="products-col-inner box-shadow-1 relative">
          <a class="fit-click" href="https://freebies.indiegala.com/my-darkest-witch"
             title="Go to My Darkest Witch page"></a>
          <figure class="product-img"><img alt="..." class="display-none async-img-load"
             data-img-src="https://www.indiegalacdn.com/imgs/devs/freebies/products/
                           9303ce41-bad2-40e2-8b3f-728d85fcb2dc/prodmain/1761983673.png"/></figure>
          <figcaption class="overflow-auto product-title-cont">
            <div class="left product-title">My Darkest Witch</div>
            <div class="right product-get-btn">get</div>
          </figcaption></div></div>

  Eight cells on the day, no pagination markup, and a JSON-LD `ItemList`
  naming the same eight URLs. There is no price and no end date anywhere: a
  freebie is free until it is taken off the page, which the ledger's
  "unlisted for two days" rule already handles.
* **The UUID in the cover's path is the product's identity.** On the product
  page the comments widget posts `gala_page_id = '9303ce41-...'` - the same
  UUID as the cover path on the listing (checked on two products). That is
  the `external_id`; the slug is not used as one. (Each product page also
  carries one stray `products/75f0a357-.../prodmain/1601683612.png` image,
  identical on every product; it is not the product and is never read.)
* The product page `https://freebies.indiegala.com/<slug>` (200, ~64 kB) has
  a "Download" section. **Signed out** it holds exactly:

      <div class="developer-product-download ...">
        <div class="developer-product-download-button-cont">
          <a class="developer-product-download-button-login ..." href="#"
             onclick="loginRedirectAnchor(this, event, 'loginBeforeAddToLibrary')">ADD TO LIBRARY</a>
        </div>
        <p ...>In order to add the product to your library you must be logged in.</p>
      </div>

  The `-login` button only redirects to the login page; it is never clicked.
* Every IndieGala page (freebies, www, login, terms) carries an inline
  `const userIsLogged = false;` signed out. That is what `health` reads - a
  flag the site writes for its own scripts, more reliable than a header.
* Login is `https://www.indiegala.com/login`: an Email field
  (`input.login-form-email`), a Password field (`input.login-form-password`),
  a **Google reCAPTCHA v2 checkbox** rendered into `#login_grecaptcha` directly
  under them, and a `button.login-btn` "login" with `onclick=processLogin`.
  There is no `<form>` element and no key handler, so Enter is not known to
  submit anything; there is no "Remember me" checkbox in the markup.
* `robots.txt` on www disallows `/library` and `/profile`; the freebies
  subdomain has none (404). Nothing here reads either path.

**The signed-in half is NOT verified.** There is no IndieGala account here, so
what the Download section becomes for a signed-in account - the real "add to
library" control, what it posts, what it shows afterwards, whether some
freebies hand out a Steam key instead, whether any require an account level
or a newsletter opt-in - is written from reasoning. It lives in the selector
tables at the top, and everything unrecognised fails safe: ownership that
cannot be read is `None`, a page with no Download section stops with a
screenshot, a Download section with no claim control is `not_eligible`, and
a newsletter or level requirement is `not_eligible` with its own reason code
- Trove never ticks an opt-in on the person's behalf.

**Terms.** IndieGala's Terms of Service (updated 25 May 2020) prohibit
"using any automated system, including without limitation robots, spiders
... to access the Service in a manner that sends more request messages ...
than a human can reasonably produce in the same period of time by using a
conventional on-line web browser", and automated "scraping". Trove's cadence
is far below a human's, but automation is automation; the blurb says so.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re

import httpx
from playwright.async_api import Page, Response, TimeoutError as PlaywrightTimeout

from ..browser import (
    CheckoutBlocked,
    NeedsAttention,
    SignedOut,
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
    SignInForm,
    SignInStep,
)

logger = logging.getLogger(__name__)

FREEBIES_ROOT = "https://freebies.indiegala.com"
FREEBIES_HOST = "freebies.indiegala.com"
WWW_ROOT = "https://www.indiegala.com"
LISTING_URL = f"{FREEBIES_ROOT}/"
LOGIN_URL = f"{WWW_ROOT}/login"

# The volume cap, itch.io's rule. Eight freebies were listed on the measured
# day and the page only grows by about one a week, so this rarely bites - but
# a first run on a new account claims every one of them, one browser visit
# each, and a cap is what keeps that polite if the list is ever long. What is
# dropped is LOGGED, and comes back on the next run (the freebies do not end).
MAX_OFFERS = 10

# Between claims is the runner's business; discovery is one request and needs
# no delay of its own. Kept for symmetry with itch.io if a second listing page
# ever appears.
POLITE_DELAY_S = 3.0

USER_AGENT = "Trove/0.1 (self-hosted free game claimer)"

# Why an IndieGala freebie gave nothing. Each is settled news for a card, not
# a fault; the frontend's BLOCKED_LABEL may learn words for them.
REASON_NEWSLETTER = "newsletter_optin"
REASON_ACCOUNT_LEVEL = "account_level"
REASON_NO_CONTROL = "no_claim_control"


# ── The selectors ───────────────────────────────────────────────────────────
#
# Every string IndieGala could change, in one table each. MEASURED means read
# off the live signed-out markup on 23 Nov 2026; UNVERIFIED means written for
# a signed-in page nobody here has seen, to be corrected from the first real
# run's log and screenshot.

# A challenge only a person can answer. None was served on any measured
# request; these are Cloudflare's and the usual captcha frames, kept for the
# day one appears. Note the login page ALWAYS carries a reCAPTCHA - these are
# only ever checked on the freebies pages, never on /login.
CHALLENGE = [
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[title*="challenge" i]',
    'text=/verify you are human/i',
    'text=/performing security verification/i',
]
# Cloudflare's interstitial by its page title, read without a selector.
CHALLENGE_TITLES = ("just a moment", "attention required", "one more step")

# MEASURED: the signed-out header's Login button on the freebies host, and the
# signed-out Download section's redirect-to-login button.
SIGNED_OUT = [
    '.header-top-notlogged a:has-text("Login")',
    'a.developer-product-download-button-login',
]

# MEASURED: the product page's Download section (signed out). Its presence is
# what says "this is a product page we understand".
DOWNLOAD_SECTION = [
    '.developer-product-download',
    '#download-section',
]

# MEASURED signed in, 23 Nov 2026: `<div class="developer-product-download-
# button-cont"><button id="loginBeforeAddToLibrary" onclick="addToLibrary(this,
# event)">ADD TO LIBRARY</button></div>` - the same button whether or not the
# account owns the game. Scoped to the Download section, never page-wide, so
# a "Get" in a carousel or a store link cannot be matched.
ADD_TO_LIBRARY = [
    '.developer-product-download-button-cont button[onclick*="addToLibrary"]',
    '.developer-product-download-button-cont a:not(.developer-product-download-button-login)',
    '.developer-product-download-button-cont button',
    '.developer-product-download a:has-text("Add to library")',
    '.developer-product-download button:has-text("Add to library")',
    '.developer-product-download a:has-text("Get")',
]

# MEASURED from the page's own `addToLibrary` (23 Nov 2026): **a freebie page
# never says whether the account owns the game.** The button reads ADD TO
# LIBRARY either way, and the help text under it always carries two links to
# `www.indiegala.com/library` - which the first, unverified version of this
# list matched (`a[href*="/library"]`), so every freebie read "already owned"
# and nothing was ever claimed. Ownership is learnt only by pressing the
# button: it POSTs `/developers/ajax/add-to-library/<uuid>/<slug>/freebies`
# and the JSON `status` is `ok` (added now), `added` (already had it),
# `login` (signed out) or anything else (an error). The press is harmless for
# an owned game. After `ok` the button turns `-button-success` "Added!" for
# 2 s; after `ok` or `added` the button is replaced by a library link *inside
# the button's container* - which is what these two read, scoped so the help
# text's links can never match again.
ADD_TO_LIBRARY_REPLY = "/developers/ajax/add-to-library/"
ADDED_NOW = ['.developer-product-download-button-success']
OWNED = ['.developer-product-download-button-cont a[href*="indiegala.com/library"]']

# UNVERIFIED: an opt-in the claim would depend on. Trove never ticks one; it
# reports the freebie as not eligible and the person decides.
NEWSLETTER = [
    '.developer-product-download input[type="checkbox"]',
    '.developer-product-download :text-matches("newsletter", "i")',
    '.developer-product-download :text-matches("marketing", "i")',
    '.developer-product-download :text-matches("subscribe", "i")',
]

# UNVERIFIED: a minimum account level, which IndieGala's giveaways use and a
# freebie might.
ACCOUNT_LEVEL = [
    '.developer-product-download :text-matches("level\\s*\\d", "i")',
    '.developer-product-download :text-matches("not high enough", "i")',
]

# UNVERIFIED: the store's own failure message after a click.
CLAIM_ERROR = [
    '.developer-product-download :text-matches("error", "i")',
    ':text-matches("something went wrong", "i")',
]

# Words that mean money. A control whose label or link carries any of these is
# never clicked, whatever else it matched.
MONEY_WORDS = re.compile(
    r"buy|purchase|cart|checkout|pay\b|price|[$€£]\s*\d|\d\s*[$€£]", re.I
)
# What a free claim control may be labelled.
CLAIM_WORDS = re.compile(r"add to (?:your )?library|\bget\b|claim|download", re.I)

# A Steam-style key, if a freebie hands one out rather than adding to the
# library. Five-character groups, three to five of them.
_KEY = re.compile(r"\b[A-Z0-9]{5}(?:-[A-Z0-9]{5}){2,4}\b")


async def _goto(page: Page, url: str) -> Response | None:
    """Navigate, tolerating a superseded navigation the way epic._goto does."""
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
    """First *visible* match in priority order, one shared timeout.

    `>> visible=true` and not `.first`: a locator's first match is not
    necessarily a visible one (the itch.io lesson in CLAUDE.md). IndieGala
    renders several blocks twice for its responsive layouts - the product
    title and its image appear once `show-under-610` and once
    `hide-under-610` - so the hidden copy is a real possibility here too.
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


def read_logged_flag(page_html: str) -> bool | None:
    """The page's own `const userIsLogged = true|false;`, or None if absent.

    MEASURED signed out on freebies, www, login and terms pages, always as
    `const userIsLogged = false;`. The signed-in `true` is the obvious
    counterpart and is not measured. Anchored on the declaration so a later
    `if ( !userIsLogged )` in the same page cannot be mistaken for it.
    """
    found = re.search(
        r"\b(?:const|let|var)\s+userIsLogged\s*=\s*(true|false)\b", page_html or ""
    )
    if not found:
        return None
    return found.group(1) == "true"


def _product_uuid(offer: FreeOffer) -> str | None:
    pid = offer.extra.get("product_uuid")
    if pid:
        return str(pid)
    return offer.external_id or None


class IndieGalaAdapter(BaseAdapter):
    store = "indiegala"
    display_name = "IndieGala"
    login_url = LISTING_URL
    signin_url = LOGIN_URL
    # Read off the signed-out login page's markup, 23 Nov 2026 (not a
    # screenshot): Email, then Password, adjacent in the DOM, then a Google
    # reCAPTCHA checkbox, then a "login" button. So the sequence types the two
    # fields with a Tab between them and **stops**: no Enter, because the
    # reCAPTCHA has to be ticked by the person first, there is no <form> for
    # an Enter to submit, and a blind Enter that did submit would be a login
    # attempt with no captcha answer - a failure on the one account that must
    # not get locked.
    sign_in_forms = [
        SignInForm(
            key="full",
            label="Email and password",
            description=(
                "The Email and Password fields on IndieGala's login page. "
                "Click the Email field first."
            ),
            steps=[
                SignInStep("email", "Typing your email…"),
                SignInStep("tab", "Moving to the password…", wait_ms=300),
                SignInStep("password", "Typing your password…"),
            ],
        ),
    ]
    sign_in_note = (
        "IndieGala's login has a reCAPTCHA checkbox under the password: tick it "
        "yourself and press “login” - Trove types the two fields and stops "
        "there. Click the Email field before pressing this, since Trove types "
        "wherever the window's focus already is."
    )
    tagline = "Free DRM-free indie games"
    blurb = (
        "Claims IndieGala's galaFreebies - free DRM-free games added to your "
        "IndieGala library. Plainly: IndieGala's terms forbid automated "
        "access beyond what a person could do in a browser, and automated "
        "scraping; Trove keeps far below that pace, but this is your own "
        "account at your own risk. Trove never ticks a newsletter opt-in for "
        "you - a freebie that needs one is left for you to take by hand."
    )

    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". IndieGala's login "
                "has a reCAPTCHA, so this is the step Trove cannot do for you."
            ),
        ),
        Requirement(
            name="Accepting the risk",
            description=(
                "IndieGala's terms of service forbid automated access beyond a "
                "human pace. Trove keeps a polite cadence and a per-run cap, "
                "but the account is yours and so is the risk."
            ),
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """The galaFreebies grid, capped at MAX_OFFERS. One request."""
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(
            timeout=20.0, headers=headers, follow_redirects=True
        ) as client:
            response = await client.get(LISTING_URL)
            response.raise_for_status()
            page_html = response.text

        if _looks_like_challenge(page_html):
            # Not measured - the plain client was always let through - but if
            # Cloudflare ever fronts the page for plain clients, an empty list
            # would read as "nothing free". Say what happened instead.
            logger.warning(
                "IndieGala: the freebies page answered with what looks like a "
                "Cloudflare challenge; reporting nothing this run."
            )
            return []

        parsed = _parse_listing(page_html)
        if "products-col-inner" in page_html and not parsed:
            # The failure mode CLAUDE.md warns about: a 200, a full page, and
            # a cheerful empty list. Say it out loud.
            logger.error(
                "IndieGala: the freebies page returned %d bytes with game cells "
                "in it and none parsed. The markup has probably changed; "
                "discovery is reporting nothing free when the page is not empty.",
                len(page_html),
            )
        dropped = len(parsed) - MAX_OFFERS
        if dropped > 0:
            logger.info(
                "IndieGala: %d freebies listed; keeping the first %d and "
                "dropping %d this run (they return on later runs).",
                len(parsed), MAX_OFFERS, dropped,
            )
        return [
            FreeOffer(
                external_id=entry["uuid"],
                title=entry["title"],
                url=entry["url"],
                image_url=entry["image_url"],
                kind="game",
                source="store",
                extra={"product_uuid": entry["uuid"], "slug": entry["slug"]},
            )
            for entry in parsed[:MAX_OFFERS]
        ]

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        """Signed in or not, from the page's own `userIsLogged` flag.

        Read on the freebies host, because that is where the claims happen:
        whether the www login's session cookie reaches the subdomain is not
        measured, and asking the host that matters answers it either way.
        """
        response: Response | None = None
        try:
            response = await _goto(page, LISTING_URL)
        except PlaywrightTimeout:
            return False, "IndieGala did not load in time."

        if await self._challenged(page):
            raise StoreChallenged(
                "IndieGala is showing a challenge that needs a person."
                " This is the store judging Trove's own browser, not your"
                " account, so the session behind it may be fine - Trove has"
                " left it alone and will try again on the next run."
            )

        try:
            page_html = await page.content()
        except Exception:
            page_html = ""
        flag = read_logged_flag(page_html)
        if flag is True:
            return True, "Signed in."
        if flag is False or await _first_visible(page, SIGNED_OUT[:1]):
            return False, "This account is signed out. Sign in again with \"Sign in here\"."
        refused = refusal_reason("IndieGala", response.status if response else None)
        if refused:
            raise StoreUnavailable(refused)
        return False, "Could not tell whether this account is signed in."

    # ── Ownership ────────────────────────────────────────────────────────

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        if not offer.url:
            return None
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            return None
        if await _first_visible(page, OWNED, timeout_ms=1000):
            return True
        # A freebie page does not say whether the account owns the game (see
        # OWNED), so this is nearly always "could not tell" - never "not
        # owned". The claim's press is what finds out, harmlessly.
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    def checkout_url(self, offer: FreeOffer) -> str | None:
        """The freebie's own page: its Download section is the whole checkout."""
        return offer.url

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        """One attempt: product page -> the Download section's one control ->
        ownership as ground truth.

        Money cannot move by construction: the only thing ever clicked is a
        control inside the product page's Download section on the freebies
        host, whose label says add-to-library/get/claim/download and whose
        label and link say nothing about buying, carts or prices. No checkbox
        is ever ticked.
        """
        if not offer.url:
            return ClaimResult(
                outcome="failed",
                detail="This offer has no IndieGala page recorded, so it cannot be claimed.",
            )

        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The IndieGala freebie page did not load.",
                await self._shot(page, offer, "page-timeout"),
            ) from None

        await self._guard(page, offer)

        section = await _first_visible(page, DOWNLOAD_SECTION, timeout_ms=5000)
        if section is None:
            raise NeedsAttention(
                "The IndieGala page has no Download section, which every "
                "freebie page had when this was written. The page has changed "
                "or the freebie was withdrawn; nothing was clicked.",
                await self._shot(page, offer, "no-download-section"),
            )

        if await _first_visible(page, NEWSLETTER, timeout_ms=800):
            return ClaimResult(
                outcome="not_eligible",
                detail=(
                    "IndieGala asks for a newsletter or marketing opt-in to "
                    "claim this one. Trove never agrees to that on your behalf; "
                    "claim it by hand if you want it."
                ),
                reason_code=REASON_NEWSLETTER,
            )
        if await _first_visible(page, ACCOUNT_LEVEL, timeout_ms=800):
            return ClaimResult(
                outcome="not_eligible",
                detail=(
                    "IndieGala says this freebie needs a higher account level "
                    "than this account has."
                ),
                reason_code=REASON_ACCOUNT_LEVEL,
            )

        control = await _first_visible(page, ADD_TO_LIBRARY, timeout_ms=4000)
        if control is None:
            return ClaimResult(
                outcome="not_eligible",
                detail=(
                    "The freebie's Download section shows no add-to-library "
                    "control for this account, so nothing was added."
                ),
                reason_code=REASON_NO_CONTROL,
            )

        label, href = await _label_and_href(control)
        logger.info("IndieGala: the control for %r says %r (href %r).", offer.title, label, href)
        if not _is_free_control(label, href, page.url or ""):
            raise NeedsAttention(
                f"The control on the freebie page reads {label!r}, which does "
                "not look like a free add-to-library, so nothing was clicked. "
                "If it is the right button, its label belongs in ADD_TO_LIBRARY.",
                await self._shot(page, offer, "not-free-control"),
            )

        # One click, once. Everything after this only reads. The store's own
        # answer to the click is the verdict (see OWNED); the page is the
        # fallback for a reply that could not be read.
        logger.info("IndieGala: adding %r to the library.", offer.title)
        status: str | None = None
        try:
            async with page.expect_response(
                lambda r: ADD_TO_LIBRARY_REPLY in r.url, timeout=15000
            ) as reply:
                await control.click(timeout=6000)
            try:
                status = str((await (await reply.value).json()).get("status") or "")
            except Exception as exc:
                logger.info("IndieGala: the add-to-library reply was not JSON: %s", exc)
        except PlaywrightTimeout:
            await self._guard(page, offer)
            if not await _first_visible(page, ADDED_NOW + OWNED, timeout_ms=500):
                raise NeedsAttention(
                    "The add-to-library control could not be clicked, or "
                    "IndieGala did not answer it.",
                    await self._shot(page, offer, "click-blocked"),
                ) from None
        if status is not None:
            logger.info("IndieGala: add-to-library for %r answered %r.", offer.title, status)
            verdict = reply_verdict(status)
            if verdict == "claimed":
                return ClaimResult(outcome="claimed", detail="Added to your IndieGala library.")
            if verdict == "already_owned":
                return ClaimResult(outcome="already_owned", detail="Already in your IndieGala library.")
            if verdict == "signed_out":
                raise SignedOut(
                    'IndieGala answered the claim with "login needed": this '
                    "account is signed out.",
                    await self._shot(page, offer, "login-needed"),
                )
            raise NeedsAttention(
                f"IndieGala answered the claim with {status!r} rather than "
                "adding it. One attempt only; check the account.",
                await self._shot(page, offer, "claim-error"),
            )

        outcome = await self._read_outcome(page, offer)
        if outcome is not None:
            return outcome
        raise NeedsAttention(
            "The add-to-library control was clicked but Trove could not confirm "
            "the game landed in the library. Check the account before Trove "
            "tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    async def _read_outcome(self, page: Page, offer: FreeOffer) -> ClaimResult | None:
        """What the page shows after the click, watched for a bounded while."""
        for _ in range(10):
            await asyncio.sleep(1.0)
            await self._guard(page, offer)
            key = await self._read_key(page)
            if key is not None:
                code, key_store = key
                logger.info(
                    "IndieGala: %r revealed a key for %s.",
                    offer.title, key_store or "an unstated store",
                )
                return ClaimResult(
                    outcome="claimed",
                    detail=(
                        "IndieGala handed out a key"
                        + (f" for {key_store}" if key_store else "")
                        + "; it is stored encrypted and was not redeemed."
                    ),
                    key_code=code,
                    key_store=key_store,
                )
            if await _first_visible(page, ADDED_NOW, timeout_ms=400):
                logger.info("IndieGala: %r claimed (page says Added!).", offer.title)
                return ClaimResult(outcome="claimed", detail="Added to your IndieGala library.")
            # The library link with no "Added!" first is the `added` answer:
            # the account had it already.
            if await _first_visible(page, OWNED, timeout_ms=400):
                logger.info("IndieGala: %r was already in the library (page says so).", offer.title)
                return ClaimResult(outcome="already_owned", detail="Already in your IndieGala library.")
            if await _first_visible(page, CLAIM_ERROR, timeout_ms=200):
                raise NeedsAttention(
                    "IndieGala answered the claim with an error. One attempt "
                    "only; check the account.",
                    await self._shot(page, offer, "claim-error"),
                )
        return None

    async def _read_key(self, page: Page) -> tuple[str, str | None] | None:
        """A key shown in the Download section, with the store it names."""
        section = await _first_visible(page, DOWNLOAD_SECTION, timeout_ms=200)
        if section is None:
            return None
        try:
            text = await section.inner_text()
        except Exception:
            return None
        return extract_key(text)

    # ── Shared ───────────────────────────────────────────────────────────

    async def _challenged(self, page: Page) -> bool:
        try:
            title = (await page.title() or "").lower()
        except Exception:
            title = ""
        if any(marker in title for marker in CHALLENGE_TITLES):
            return True
        return await _first_visible(page, CHALLENGE, timeout_ms=700) is not None

    async def _guard(self, page: Page, offer: FreeOffer) -> None:
        """Stop on anything only a person can answer. One attempt, no retries.

        A captcha at the claim goes to the un-driven window, Epic's proven
        remedy: `CheckoutBlocked` carries the offer so "Finish the claim here"
        opens this freebie's page in the plain Chrome with nothing attached.
        """
        if await self._challenged(page):
            raise CheckoutBlocked(
                "IndieGala put up a challenge at the freebie. Finish this one "
                "claim by hand in the sign-in window - press “Finish the claim "
                "here”.",
                await self._shot(page, offer, "challenge"),
                offer_id=offer.external_id,
            )
        if await _first_visible(page, SIGNED_OUT, timeout_ms=600):
            raise NeedsAttention(
                "This account is signed out of IndieGala. Sign in again with "
                "\"Sign in here\".",
                await self._shot(page, offer, "signed-out"),
            )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))


async def _label_and_href(control) -> tuple[str, str]:
    label = ""
    href = ""
    try:
        label = " ".join((await control.inner_text() or "").split())
    except Exception:
        pass
    try:
        href = await control.get_attribute("href") or ""
    except Exception:
        pass
    return label, href


def reply_verdict(status: str) -> str:
    """What IndieGala's add-to-library `status` means (its own handler, read)."""
    return {"ok": "claimed", "added": "already_owned", "login": "signed_out"}.get(
        (status or "").strip().lower(), "error"
    )


def _is_free_control(label: str, href: str, page_url: str) -> bool:
    """The money guard. True only for a claim-worded control on the freebies
    host that says nothing about buying.

    Pure, so the sim can pin it. A control with a real link must stay on the
    freebies host or IndieGala's own library; anything pointing at the store,
    a cart or a checkout is refused.
    """
    if FREEBIES_HOST not in (page_url or ""):
        return False
    if not CLAIM_WORDS.search(label or ""):
        return False
    if MONEY_WORDS.search(label or "") or MONEY_WORDS.search(href or ""):
        return False
    link = (href or "").strip()
    if link and not link.startswith("#") and not link.lower().startswith("javascript"):
        if not re.match(r"^(?:/|https://(?:freebies|www)\.indiegala\.com/)", link):
            return False
        if re.search(r"/store/|/cart|/checkout", link, re.I):
            return False
    return True


def extract_key(text: str) -> tuple[str, str | None] | None:
    """(code, store word) for a key in some text, or None.

    The store is named only when the text says so; "steam" is the one the
    freebies page title promises ("DRM-Free & Steam Freebies").
    """
    found = _KEY.search(text or "")
    if not found:
        return None
    store = "steam" if re.search(r"steam", text, re.I) else None
    return found.group(0), store


# ── Listing parsing ─────────────────────────────────────────────────────────

# One cell of the freebies grid (shape quoted in the module docstring). Split
# on the inner box rather than the column, and attribute-order independent:
# itch.io's markup shuffled attribute order between responses, and a split
# pattern that assumed an order silently parsed nothing (CLAUDE.md).
#
# Classes are matched as whole words between spaces or quotes, never with
# `\b`: `\bproduct-title\b` also matches the figcaption's `product-title-cont`
# (a hyphen is a word boundary), which read every title as empty - caught by
# tools/indiegala_sim.py before it shipped.
def _class_re(name: str) -> str:
    return r'class="(?:[^"]*\s)?' + re.escape(name) + r'(?:\s[^"]*)?"'


_CELL_SPLIT = re.compile(r"(?=<div[^>]*" + _class_re("products-col-inner") + ")")
_CELL_START = re.compile(r"<div[^>]*" + _class_re("products-col-inner"))
_A_TAG = re.compile(r"<a\b([^>]*)>")
_IMG_TAG = re.compile(r"<img\b([^>]*)>")
_TITLE = re.compile(_class_re("product-title") + r"[^>]*>([^<]*)<")
_GET_BTN = re.compile(_class_re("product-get-btn") + r"[^>]*>([^<]*)<")
_UUID_IN_PATH = re.compile(
    r"/products/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
    re.I,
)
_PRODUCT_URL = re.compile(r"^https://freebies\.indiegala\.com/([a-z0-9][a-z0-9-]*)/?$", re.I)
_PRICE = re.compile(r"[$€£]\s*\d|\d\s*[$€£]")
_CHALLENGE_TEXT = re.compile(
    r"<title>\s*(?:just a moment|attention required)|challenges\.cloudflare\.com|cf-chl-",
    re.I,
)


def _attr(attrs: str, name: str) -> str:
    found = re.search(rf'\b{name}="([^"]*)"', attrs)
    return html_lib.unescape(found.group(1)) if found else ""


def _looks_like_challenge(page_html: str) -> bool:
    return bool(_CHALLENGE_TEXT.search(page_html or ""))


def _parse_listing(page_html: str) -> list[dict]:
    """The freebie cells, in page order, each with a stable product UUID.

    A cell is kept only when all of it is there and it is plainly a freebie:
    a product link on the freebies host, a title, a cover whose path carries
    the product UUID, the "get" badge, and no price anywhere in the cell. A
    cell that fails any of those is skipped with a log line - never guessed
    at, and never given the slug as an identity.
    """
    entries: list[dict] = []
    seen: set[str] = set()
    for cell in _CELL_SPLIT.split(page_html or ""):
        if not _CELL_START.match(cell):
            continue
        # Stop at the end of this cell's figcaption, so a later block on the
        # page (the footer, a promo) cannot lend a field to the last cell.
        end = cell.find("</figcaption>")
        if end != -1:
            cell = cell[: end + len("</figcaption>")]

        url = ""
        for attrs in _A_TAG.findall(cell):
            if "fit-click" in _attr(attrs, "class").split():
                url = _attr(attrs, "href")
                break
        slug_match = _PRODUCT_URL.match(url)
        title_match = _TITLE.search(cell)
        get_match = _GET_BTN.search(cell)

        image_url = ""
        for attrs in _IMG_TAG.findall(cell):
            image_url = _attr(attrs, "data-img-src") or _attr(attrs, "src")
            if image_url:
                break
        uuid_match = _UUID_IN_PATH.search(image_url)

        if _PRICE.search(cell):
            logger.info("IndieGala: a freebies cell carried a price; skipped.")
            continue
        if not get_match or get_match.group(1).strip().lower() != "get":
            logger.info("IndieGala: a freebies cell had no \"get\" badge; skipped.")
            continue
        if not (slug_match and title_match and title_match.group(1).strip() and uuid_match):
            logger.info(
                "IndieGala: a freebies cell was missing a field (url=%r, "
                "title=%s, uuid=%s); skipped.",
                url, bool(title_match), bool(uuid_match),
            )
            continue
        uuid = uuid_match.group(1).lower()
        if uuid in seen:
            continue
        seen.add(uuid)
        entries.append(
            {
                "uuid": uuid,
                "title": html_lib.unescape(title_match.group(1).strip()),
                "url": url.rstrip("/"),
                "slug": slug_match.group(1),
                "image_url": image_url,
            }
        )
    return entries
