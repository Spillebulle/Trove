"""Prime Gaming.

Amazon's monthly drops for Prime subscribers. The hardest store of the set,
and it breaks the app's cleanest boundary on purpose, so the reasons are
written here where they will be read.

**There is no public offers feed. Measured, 24 Aug 2026, from this container:**

  GET https://gaming.amazon.com/home  (plain httpx client, Chrome user-agent,
  no cookies) -> 302 to https://luna.amazon.com/claims/home?g=s -> 200, a
  10.6 kB SPA shell: `<div id="root"></div>`, a "We're having technical
  difficulties" fallback block, `<noscript>You need to enable JavaScript`,
  and **no offer data at all** - no embedded JSON state, no titles, zero
  occurrences of "Claim". Everything renders client-side, for a session.
  POST /graphql on both hosts, unauthenticated -> HTTP 403.

So `list_free_offers()` returns `[]` honestly, `discovery_needs_session` is
true, and discovery happens in `discover_in_session(page)` inside the same
browser session the claims use. That redirect is also a fact to remember:
**Prime Gaming now lives under luna.amazon.com/claims**, so selectors and URL
checks must not assume the gaming.amazon.com host survives navigation.

**Scope, v1: keep-forever games only.** Prime claims come in three shapes:

  (a) direct claims - a "Claim game" button adds it to the Amazon Games
      library. Automated.
  (b) key claims - the claim reveals a code for another store (GOG, Legacy
      Games, Microsoft). The reveal is automated and the code is recorded in
      the ledger (`ClaimResult.key_code` / `key_store`, encrypted at rest).
      Trove never redeems it anywhere: redeeming on a second store is a second
      store's login, and the ledger showing the key to the person is the whole
      point.
  (c) linked-account claims - need an Epic/EA/etc. account linked to Prime.
      Attempted; if the link is missing, the run stops with a sentence naming
      exactly which link, because that is a thing only the person can do.

In-game LOOT drops are out of scope and filtered out at discovery. They are
most of the page by volume, they expire constantly, and claiming them needs
the linked-game context per title. Future work, deliberately.

**Everything session-bound in this file is UNVERIFIED against a real Prime
account** - there is no Prime account to run it against here, and the
signed-out page is an empty shell, so there was no DOM to read the selectors
off. They are written from the page's known shape (the `data-a-target`
attribute convention Amazon's Twitch-derived frontend uses) plus generous
fallbacks, every step logs what it actually found, and anything unrecognised
stops with a screenshot rather than guessing. The first real run is the
measurement; fix the tables at the top of this file against what it logs.

**Amazon's login is the most bot-hostile of the set.** A fresh sign-in gets a
captcha, an OTP email, or a "verify it's you" push at the slightest excuse,
and 2FA re-prompts happen even on a warm session. Nothing here fights that:
the session is made by hand in the un-driven window, `health` reports rather
than repairs, and every challenge is the attention queue, immediately.
"""
from __future__ import annotations

import asyncio
import logging
import re

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from ..browser import NeedsAttention, screenshot, screenshot_name
from .base import (
    BaseAdapter,
    ChallengeWaiter,
    ClaimResult,
    FreeOffer,
    Requirement,
)

logger = logging.getLogger(__name__)

# The offers page. gaming.amazon.com redirects to luna.amazon.com/claims
# (measured); the browser follows it, and nothing below assumes which host it
# lands on.
HOME_URL = "https://gaming.amazon.com/home"


# ── The selectors ───────────────────────────────────────────────────────────
#
# Every string Amazon could change, in one table, same as epic.py. Each is a
# Playwright selector tried in order until one is visible.
#
# UNVERIFIED against a signed-in Prime account. See the module docstring: the
# signed-out page is an empty shell, so these are written from the frontend's
# known `data-a-target` convention plus text fallbacks. The first real run's
# log is what fixes them.

# The account is signed in: the nav shows the user's avatar / name.
SIGNED_IN = [
    '[data-a-target="user-dropdown-first-name-text"]',
    '[data-a-target="nav-avatar"]',
    'nav img[alt*="avatar" i]',
    'button[aria-label*="User menu" i]',
]

# The account is signed out: the page offers a sign-in instead.
SIGNED_OUT = [
    '[data-a-target="sign-in-button"]',
    'button:has-text("Sign in")',
    'a[href*="/ap/signin"]',
    'a:has-text("Sign in")',
]

# A challenge only a person can answer: Amazon's image captcha, an OTP prompt,
# a "verify it's you" interstitial, Arkose. The attention queue, immediately.
CHALLENGE = [
    'form[action*="validateCaptcha"]',
    'input#captchacharacters',
    'img[src*="captcha"]',
    'input#auth-mfa-otpcode',
    'form#auth-mfa-form',
    'iframe[src*="arkoselabs"]',
    'iframe[title*="challenge" i]',
    'text=/enter the characters you see/i',
    'text=/verification required/i',
    'text=/approve the notification/i',
]

# The button that claims a keep-forever game on its own page. The historical
# labels, longest first so "Claim game" wins over a bare "Claim".
CLAIM = [
    'button[data-a-target="buy-box_call-to-action"]',
    '[data-a-target="buy-box"] button:has-text("Claim")',
    'button:has-text("Claim game")',
    'button:has-text("Claim now")',
    'button:has-text("Get game")',
    'button:has-text("Claim")',
]

# The claim went through: the button flips, or a success banner shows. Prime
# has no library page in the usual sense, so this is also what `is_owned`
# reads off the offer's own page.
CLAIMED = [
    'button:has-text("Claimed")',
    '[data-a-target="buy-box"] :text-matches("Claimed", "i")',
    'text=/successfully claimed/i',
    'text=/success!/i',
    'text=/is yours/i',
    'text=/in your (games )?library/i',
    'text=/download on the amazon games app/i',
]

# A key claim's revealed code. Amazon shows it in a copyable input or a code
# box next to instructions naming the store it redeems on.
KEY_CODE = [
    '[data-a-target="copy-code-input"]',
    'input[aria-label*="code" i]',
    'input[id*="code" i][readonly]',
    '[class*="claim-code"] input',
    '[class*="claim-code"]',
    'code',
]

# A claim that needs another store's account linked, and it is not. The wall
# names the store; `_missing_link` reads which.
LINK_WALL = [
    'button:has-text("Link account")',
    'button:has-text("Link game account")',
    'a:has-text("Link account")',
    'text=/link your .{0,30} account/i',
]

# The stores a Prime key or link can belong to, mapped to the ledger's
# `key_store` word. Scanned in the page text near the claim, longest name
# first so "Epic Games" is not read as "Games".
KEY_STORES = [
    ("epic games", "epic"),
    ("legacy games", "legacy"),
    ("microsoft store", "microsoft"),
    ("battle.net", "battlenet"),
    ("ubisoft", "ubisoft"),
    ("gog.com", "gog"),
    ("gog", "gog"),
    ("epic", "epic"),
    ("ea app", "ea"),
    ("origin", "ea"),
    ("steam", "steam"),
]

# Words on a card that mean in-game content rather than a keep-forever game.
# v1 filters these out at discovery; see the module docstring.
LOOT_WORDS = re.compile(r"in-?game (content|loot|item)|loot|dlc drop", re.IGNORECASE)


async def _goto(page: Page, url: str) -> None:
    """Navigate, tolerating the redirect chain replacing the navigation.

    Same reasoning as epic._goto: gaming.amazon.com 302s to luna.amazon.com
    (measured), a signed-out session bounces further, and Playwright reports a
    superseded navigation as `net::ERR_ABORTED` on a page that in fact loaded.
    An abort is a cue to read where we landed; only a timeout means nothing
    arrived.
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
    """The first visible element matching any selector, in list order, or None.

    Identical to epic's: polls all the selectors each round so priority is
    honoured and an absent group costs one timeout, not one per selector.
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


# The in-page scan that enumerates the offer cards. JavaScript rather than a
# selector walk because the card markup is unverified: this reads *candidates*
# - every element that looks like an offer card by any of the known shapes -
# and hands back everything identifying about each (attributes, link, image,
# text), so the Python side can decide and, above all, LOG what the page
# actually contained. The first real run's log is the measurement this whole
# file is waiting for.
_SCAN_CARDS_JS = """
() => {
  const shapes = [
    '[data-a-target="item-card"]',
    '.item-card',
    '[data-test-selector="Offer"]',
    '[class*="offer-card"]',
    '[data-a-target*="offer"]',
  ];
  const seen = new Set();
  const cards = [];
  for (const shape of shapes) {
    for (const el of document.querySelectorAll(shape)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const attrs = {};
      // The card's own identifying attributes, and its children's data-*
      // ids: Amazon's offer/deal id is expected in one of these.
      for (const node of [el, ...el.querySelectorAll('[data-a-target],[data-item-id],[data-deal-id],[id]')].slice(0, 12)) {
        for (const name of ['data-a-target', 'data-item-id', 'data-deal-id', 'data-offer-id', 'id']) {
          const v = node.getAttribute && node.getAttribute(name);
          if (v && !(name in attrs)) attrs[name] = v;
        }
      }
      const link = el.querySelector('a[href]');
      const img = el.querySelector('img');
      cards.push({
        shape,
        attrs,
        href: link ? link.getAttribute('href') : null,
        img: img ? (img.currentSrc || img.getAttribute('src')) : null,
        alt: img ? img.getAttribute('alt') : null,
        aria: el.getAttribute('aria-label'),
        text: (el.innerText || '').slice(0, 300),
      });
    }
  }
  return { total: cards.length, cards: cards.slice(0, 120) };
}
"""

# An offer/deal id as Amazon writes them: a UUID or a long hex/word token in a
# data attribute. Anything shorter is a UI label, not an identity.
_ID_SHAPES = [
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I),
    re.compile(r"amzn1\.[\w.\-]{8,}"),
    re.compile(r"\b[0-9a-f]{16,}\b", re.I),
]


def _card_external_id(card: dict) -> str | None:
    """A stable id for one card, or None - never a title.

    Preference order: an id-shaped token in the card's data attributes (that is
    Amazon's own offer/deal id); failing that, the card link's path (stable for
    the life of the offer, and a URL rather than display text). A card with
    neither is skipped and logged rather than given a made-up identity: a title
    changes with marketing copy and would double-claim on the change.
    """
    for value in (card.get("attrs") or {}).values():
        for shape in _ID_SHAPES:
            found = shape.search(value or "")
            if found:
                return found.group(0)
    href = card.get("href") or ""
    path = href.split("?", 1)[0].strip("/")
    if path:
        return f"path:{path}"
    return None


def _card_title(card: dict) -> str | None:
    """The card's title: image alt, aria label, or the first text line."""
    for candidate in (card.get("alt"), card.get("aria")):
        if candidate and candidate.strip():
            return candidate.strip()[:300]
    for line in (card.get("text") or "").splitlines():
        line = line.strip()
        # Skip the button labels and badges that share the card's text.
        if line and line.lower() not in ("claim", "claim game", "learn more", "free with prime"):
            return line[:300]
    return None


class PrimeAdapter(BaseAdapter):
    store = "prime"
    display_name = "Prime Gaming"
    login_url = HOME_URL
    # Amazon's actual login form (/ap/signin) needs request-specific parameters
    # and shows an error opened bare, so the sign-in window opens on the store
    # front and the person presses its own "Sign in" - which routes through
    # amazon.com's login and back.
    signin_url = HOME_URL
    # No `sign_in_sequence` on purpose: nobody has looked at Amazon's login
    # form, and a sequence guessed from memory would type an Enter into a
    # half-filled form - a failed login attempt, on the one account this app
    # must not get locked. So "Sign in for me" is not offered here; the
    # Email / Password / Code buttons still work, because the person clicks
    # the field first and Trove only types.
    #
    # To fill it in: open the sign-in window, look at the form, and write down
    # whether the fields share a page (Tab between them) or the store asks for
    # the name first and the password on a second step (Enter, then a wait).
    blurb = (
        "Claims the monthly keep-forever games. Needs an active Amazon Prime "
        "subscription. Amazon's login is the most bot-hostile of the set: sign "
        "in by hand once and expect occasional re-verification. Games that are "
        "keys for other stores are claimed and the key is kept in the ledger; "
        "in-game loot drops are not yet supported."
    )

    # Offers only render for a signed-in session (measured; module docstring),
    # so discovery happens in `discover_in_session` and the sessionless path
    # below is an honest no-op.
    discovery_needs_session = True

    REQUIREMENTS = [
        Requirement(
            name="An active Amazon Prime subscription",
            description=(
                "Prime Gaming's drops are a Prime benefit; without the "
                "subscription the page lists nothing to claim."
            ),
        ),
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". Amazon "
                "challenges automated logins hardest of any store here, and "
                "may occasionally ask to re-verify even a good session."
            ),
        ),
        Requirement(
            name="Two-factor codes",
            description=(
                "If your Amazon account has two-step verification, keep your "
                "authenticator to hand for that first sign-in."
            ),
            required=False,
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """Nothing, honestly: Prime has no public feed.

        Measured (module docstring): the signed-out page is an empty SPA shell
        and the data endpoint answers 403. Discovery is `discover_in_session`.
        """
        return []

    async def discover_in_session(self, page: Page) -> list[FreeOffer]:
        """Enumerate the keep-forever games off the signed-in offers page.

        v1 scope: games only. Cards whose text reads as in-game loot are
        filtered out here, so the claim loop never sees them. Every step logs
        what it found - counts, shapes, sample attributes - because the card
        markup is unverified and the first real run's log is what the selector
        tables get corrected against.
        """
        try:
            await _goto(page, HOME_URL)
        except PlaywrightTimeout:
            logger.warning("Prime: the offers page did not load; discovering nothing.")
            return []
        # The SPA renders after load; give the cards a moment to exist at all.
        try:
            await page.wait_for_timeout(3000)
        except Exception:
            pass

        try:
            scan = await page.evaluate(_SCAN_CARDS_JS)
        except Exception as exc:
            logger.warning("Prime: the card scan failed (%s); discovering nothing.", exc)
            return []

        cards = scan.get("cards") or []
        logger.info(
            "Prime discovery: %s card-shaped elements on %s (reporting %s).",
            scan.get("total"), page.url, len(cards),
        )

        offers: list[FreeOffer] = []
        seen_ids: set[str] = set()
        skipped_loot = skipped_no_id = 0
        for card in cards:
            text = " ".join(filter(None, [card.get("text"), card.get("aria")]))
            if LOOT_WORDS.search(text or ""):
                skipped_loot += 1
                continue
            external_id = _card_external_id(card)
            if external_id is None:
                skipped_no_id += 1
                logger.info(
                    "Prime discovery: a card had no stable id; skipped. shape=%r attrs=%r href=%r text=%r",
                    card.get("shape"), card.get("attrs"), card.get("href"),
                    (card.get("text") or "")[:120],
                )
                continue
            if external_id in seen_ids:
                continue
            title = _card_title(card)
            if not title:
                logger.info(
                    "Prime discovery: card %s had no readable title; skipped. attrs=%r",
                    external_id, card.get("attrs"),
                )
                continue
            seen_ids.add(external_id)
            href = card.get("href") or ""
            if href.startswith("/"):
                # Relative to whichever host the SPA is on (gaming.amazon.com
                # redirects to luna.amazon.com; measured).
                origin = page.url.split("/", 3)
                href = f"{origin[0]}//{origin[2]}{href}" if len(origin) > 2 else href
            offers.append(
                FreeOffer(
                    external_id=external_id,
                    title=title,
                    url=href or None,
                    image_url=card.get("img"),
                    # A key claim only announces itself at claim time; "game"
                    # is the honest default and the ledger row carries the key
                    # when one is revealed.
                    kind="game",
                    source="store",
                )
            )
            logger.info(
                "Prime discovery: %r id=%s url=%s", title, external_id, href or None
            )

        logger.info(
            "Prime discovery: %d keep-forever offers (%d loot filtered out, %d without a stable id).",
            len(offers), skipped_loot, skipped_no_id,
        )
        return offers

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        try:
            await _goto(page, HOME_URL)
        except PlaywrightTimeout:
            return False, "The Prime Gaming page did not load in time."

        if await _first_visible(page, CHALLENGE):
            return False, "Amazon is showing a challenge that needs a person."
        if await _first_visible(page, SIGNED_IN, timeout_ms=6000):
            return True, "Signed in."
        if await _first_visible(page, SIGNED_OUT):
            return False, (
                "This account is signed out of Amazon. Sign in again with "
                "\"Sign in here\"."
            )
        # Neither shape: the page changed, or rendered nothing. Say so rather
        # than guessing - "signed out" would send the person to sign in to no
        # effect.
        logger.info("Prime health: neither signed-in nor signed-out markers on %s.", page.url)
        return False, "Could not tell whether this account is signed in."

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        """Prime has no library page to ask; the offer's own button is the tell.

        A claimed offer's page shows "Claimed" / success wording where the
        claim button was. Unreadable is None, never False - the contract's
        rule, and doubly so here where every selector is unverified.
        """
        if not offer.url:
            return None
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            return None
        if await _first_visible(page, CLAIMED, timeout_ms=4000):
            return True
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        """One attempt: click the claim button once, then only read.

        Phase-aware in the same spirit as epic._drive_checkout: the claim
        button is clicked at most once, and after the click the loop only ever
        reads states - success, a revealed key, a link-account wall, a
        challenge - and never clicks the claim again. A second click on a
        processing claim is the double-order race the Epic checkout already
        taught this app about.
        """
        if not offer.url:
            return ClaimResult(
                outcome="failed",
                detail="This offer has no page URL, so there is nothing to open.",
            )
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The offer's page did not load. Amazon may be slow, or the "
                "session may have expired.",
                await self._shot(page, offer, "claim-timeout"),
            ) from None

        await self._guard(page, offer)

        if await _first_visible(page, CLAIMED, timeout_ms=2000):
            return ClaimResult(outcome="already_owned", detail="Already claimed.")

        # A link-account wall standing where the claim button should be means
        # the prerequisite is missing before anything is clicked.
        await self._check_link_wall(page, offer, clicked=False)

        button = await _first_visible(page, CLAIM, timeout_ms=8000)
        if button is None:
            raise NeedsAttention(
                "Could not find the button that claims this game. Amazon has "
                "probably changed the page. Run this with \"Run and watch\" to "
                "see it; the button's label goes in prime.CLAIM.",
                await self._shot(page, offer, "no-claim-button"),
            )
        try:
            label = " ".join((await button.inner_text() or "").split())[:40]
        except Exception:
            label = "?"
        logger.info("Prime claim: clicking %r on %r.", label, offer.title)
        try:
            await button.click(timeout=5000)
        except PlaywrightTimeout:
            # Something covered the click. If it is a challenge, that is the
            # attention queue; anything else is worth a person's eyes too.
            if await _first_visible(page, CHALLENGE, timeout_ms=800):
                raise NeedsAttention(
                    "Amazon raised a challenge the moment the claim was "
                    "pressed. Trove never answers those; the account needs a "
                    "person once.",
                    await self._shot(page, offer, "claim-challenge"),
                ) from None
            raise NeedsAttention(
                "The claim button could not be clicked - something is covering "
                "it. Run and watch to see what.",
                await self._shot(page, offer, "click-blocked"),
            ) from None

        # The button was clicked once. From here: only read.
        for _ in range(14):
            await self._guard(page, offer)
            await self._check_link_wall(page, offer, clicked=True)

            key = await self._read_key(page)
            if key is not None:
                code, key_store = key
                logger.info(
                    "Prime claim: %r revealed a key for %s.", offer.title,
                    key_store or "an unstated store",
                )
                return ClaimResult(
                    outcome="claimed",
                    detail=(
                        "Claimed as a key"
                        + (f" for {key_store}" if key_store else "")
                        + "; the code is in the ledger."
                    ),
                    key_code=code,
                    key_store=key_store,
                )

            if await _first_visible(page, CLAIMED, timeout_ms=1000):
                logger.info("Prime claim: %r claimed.", offer.title)
                return ClaimResult(outcome="claimed", detail="Claimed to your library.")

            await page.wait_for_timeout(1200)

        # Clicked, and nothing recognisable followed. One attempt means one
        # attempt: stop with the evidence rather than clicking again.
        raise NeedsAttention(
            "The claim was pressed but the page never showed a result Trove "
            "recognises. Check the account before it tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    # ── Shared ───────────────────────────────────────────────────────────

    async def _guard(self, page: Page, offer: FreeOffer) -> None:
        """Stop on anything only a person can answer. Never solves, never waits.

        Amazon's challenges (image captcha, OTP, verify-push) all need the
        person's own device or inbox, so there is nothing a screen-solve could
        do even where one is offered - the waiter is not used here.
        """
        if await _first_visible(page, CHALLENGE, timeout_ms=700):
            raise NeedsAttention(
                "Amazon is asking for a verification only you can do (a "
                "captcha, a code, or an approval on your device). Sign in "
                "again with \"Sign in here\" and answer it there.",
                await self._shot(page, offer, "challenge"),
            )
        if await _first_visible(page, SIGNED_OUT, timeout_ms=600):
            raise NeedsAttention(
                "This account is signed out of Amazon. Sign in again with "
                "\"Sign in here\".",
                await self._shot(page, offer, "signed-out"),
            )

    async def _check_link_wall(self, page: Page, offer: FreeOffer, clicked: bool) -> None:
        """Stop with a sentence naming the missing store link, if a wall is up."""
        wall = await _first_visible(page, LINK_WALL, timeout_ms=800)
        if wall is None:
            return
        store_name = await self._missing_link(page)
        which = f"your {store_name} account" if store_name else "another store account"
        logger.info(
            "Prime claim: %r needs %s linked (seen %s the claim click).",
            offer.title, which, "after" if clicked else "before",
        )
        raise NeedsAttention(
            f"This game is delivered through {which}, which is not linked to "
            "your Prime Gaming account. Link it once at gaming.amazon.com and "
            "Trove will take the game on its next run.",
            await self._shot(page, offer, "link-missing"),
        )

    async def _missing_link(self, page: Page) -> str | None:
        """Which store the link wall is asking for, read off the page text."""
        try:
            body = (await page.inner_text("body"))[:4000].lower()
        except Exception:
            return None
        found = re.search(r"link your ([\w .'&+-]{2,30}?) account", body)
        if found:
            return found.group(1).strip().title()
        for needle, _store in KEY_STORES:
            if needle in body:
                return needle.title()
        return None

    async def _read_key(self, page: Page) -> tuple[str, str | None] | None:
        """A revealed claim code and the store it redeems on, or None.

        Codes are read from a copyable input first, a code box second, and only
        believed when they look like a key (long, dashed or 12+ characters) -
        a stray `<code>` element in the page must not become a ledger row's
        "key".
        """
        for selector in KEY_CODE:
            try:
                # `visible=true` and not `.first`: a locator's first match is
                # not necessarily a visible one. itch.io renders the sale
                # widget twice - a `visibility: hidden` 0x0 copy for the
                # narrow layout, then the real one - and taking `.first`
                # found the hidden copy, answered "not visible" and reported a
                # live 100% sale as ended. Ask Playwright for the first
                # *visible* match, which is what this function is called.
                locator = page.locator(f"{selector} >> visible=true").first
                if not await locator.is_visible():
                    continue
                value = ""
                try:
                    value = (await locator.input_value()) or ""
                except Exception:
                    value = (await locator.inner_text()) or ""
                value = value.strip()
            except Exception:
                continue
            if not value:
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9?-]{7,60}", value) or (
                "-" not in value and len(value) < 12
            ):
                logger.debug("Prime: %r matched %r but does not read as a key.", value, selector)
                continue
            return value, await self._key_target(page)
        return None

    async def _key_target(self, page: Page) -> str | None:
        """Which store the revealed key redeems on, from the page text."""
        try:
            body = (await page.inner_text("body"))[:4000].lower()
        except Exception:
            return None
        for needle, store_word in KEY_STORES:
            if needle in body:
                return store_word
        return None

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))
