"""itch.io.

itch.io runs frequent 100%-off sales: a normally-paid game is discounted to
zero for a window, and while the sale runs a signed-in account can **claim**
it - add it to the library permanently, to keep after the sale ends. That is
a different thing from itch's ordinary free games, which are simply
downloadable (or browser-playable) and have nothing to claim.

Two things make this store unlike Epic, and both shape this file:

**Volume.** Epic gives away one or two games a week; itch.io can have dozens
of 100%-off sales running at once (measured 24 Aug 2026: 16 of the 36 games on
the first page of `/games/on-sale` were at -100%). An adapter that surfaced
all of them would turn Trove into a firehose - dozens of browser claims per
run, exactly the hammering cadence CLAUDE.md forbids. So discovery is capped
at `MAX_OFFERS` per run and **logs what it dropped** rather than hiding it.
The rest are picked up on later runs as sales rotate out.

**Terms.** Automated claiming may be against itch.io's terms of service, the
same way automating Epic's login can be against Epic's. The blurb says so
plainly, per CLAUDE.md's honesty rule: this is for the user's own account, on
their own machine, at their own risk - and the polite cadence and the cap are
what keep it from being abusive on top of being automated.

**Discovery, measured against the live site (24 Aug 2026), sessionless:**

* `https://itch.io/games/on-sale?format=json` answers 200 with
  `{"content": "<grid html>", "num_items": 36, "page": N}` - the same cell
  markup as the HTML page, minus the chrome. It answers a plain
  `Trove/0.1` user agent (measured), so no browser UA is worn.
* Each cell is `<div data-game_id="4896784" class="game_cell ...">` with a
  title link (`class="title game_link"`, href = the game's own page), a price
  tag (`class="price_tag meta_tag sale"`, href = `/s/<sale_id>/<slug>` - the
  sale page) containing `price_value` ("$0") and `sale_tag` ("-100%", or
  "-15%", or "In bundle"), and a cover in `data-lazy_src`.
* The sale page `/s/<sale_id>/<slug>` (HTML only; `format=json` returns the
  page again) carries the sale's own end time as ISO 8601 in
  `promotion_dates`: `Ends <span class="date_format">2026-09-20T13:07:00Z`.
  Its buy button is `<button class="buy_game_btn" data-game='{"id":4896784,
  "min_price":100,"actual_price":0,...}' data-href="/game/purchase/4896784">
  Download or claim</button>` - `min_price` is the normal price in cents, so
  a positive `min_price` with `actual_price` 0 is the claimable-to-keep
  shape, and the label saying "claim" is the store agreeing.
* `/game/purchase/<game_id>` 302s to `<game_url>/purchase`, which shows the
  pay-what-you-want form and the link **"No thanks, just take me to the
  downloads"** - the honest zero-dollar path. Nothing on it is ever submitted
  with an amount; Trove does not spend money.

**The signed-in half is NOT verified.** There is no itch.io account here, so
the signed-in markers (avatar menu vs "Log in"), the "You own this" ownership
banner, and the claim control on the download page are written from how the
flow is known to work (the ItchClaim project proves claiming works over plain
authenticated HTTP) and not from measurement. They live in the selector
tables at the top, Epic-style, so the first person with an account fixes them
in one place. Anything unrecognised stops with a screenshot rather than
guessing.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
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
)

logger = logging.getLogger(__name__)

STORE_ROOT = "https://itch.io"
# The store's own on-sale browse, as JSON: the grid HTML in a `content` field,
# 36 cells a page. Verified live 24 Aug 2026.
LISTING_URL = f"{STORE_ROOT}/games/on-sale"

# The volume cap. itch.io can run dozens of 100%-off sales at once (16 on the
# first listing page alone, measured), and claiming is one browser visit each -
# so an uncapped run would hammer the store for an hour, which is the cadence
# CLAUDE.md exists to prevent. Newest-first as the listing orders them; what
# is dropped is LOGGED, never silently discarded, and sales last days, so the
# rest surface on later runs as claimed ones fall off the front.
MAX_OFFERS = 20

# Between the per-sale page fetches discovery makes for end times. Sessionless
# and cheap, but still one host being asked twenty things in a row.
POLITE_DELAY_S = 0.5


# ── The selectors ───────────────────────────────────────────────────────────
#
# Epic-style: every string itch.io could change, in one table each. The
# signed-OUT panel is measured (the logged-out header carries
# `user_panel_widget` with a "Log in" panel_button); the signed-IN shapes are
# UNVERIFIED - written from the known layout of the logged-in header, to be
# corrected by the first person who runs this with a real account.

SIGNED_OUT = [
    '.user_panel_widget a[href="/login"]',
    'a.panel_button:has-text("Log in")',
]

# UNVERIFIED: the signed-in header replaces the log-in panel with the user's
# avatar/menu.
SIGNED_IN = [
    '.user_panel_widget a[href*="/my-feed"]',
    'a[href="/my-feed"]',
    '.user_panel_widget .drop_menu_wrap',
    '.header_widget img.avatar',
]

# A challenge only a person can answer. Trove never solves one: it stops and
# files the account, immediately.
CHALLENGE = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    'iframe[title*="challenge" i]',
    'text=/verify you are human/i',
]

# The account already owns it. UNVERIFIED wording: itch.io puts an ownership
# banner on the game page for a purchase/claim; the regex is loose on purpose.
OWNED = [
    'text=/you own this/i',
    '.purchase_banner',
    '.ownership_reason',
]

# The game page's buy button during a 100% sale. Measured signed-out: an
# `a.buy_btn` labelled "Download or claim" linking `<game_url>/purchase`,
# beside a sale label whose `.sale_rate` reads "100%".
BUY_BUTTON = [
    'a.buy_btn:has-text("claim")',
    '.buy_row a.button.buy_btn',
    'a.buy_btn',
]

# Proof the 100% sale is still live on the page itself, checked before any
# click: the sale label's rate. A page without it means the sale ended between
# discovery and the claim, and nothing is clicked.
SALE_100 = [
    '.sale_rate:has-text("100%")',
    'text=/100%\\s*off/i',
]

# The zero-dollar path through the pay-what-you-want form. Measured signed-out
# on `<game_url>/purchase`: this exact wording. It is the ONLY thing clicked on
# that form - never a pay/submit control - which is what guarantees no money
# can move.
NO_THANKS = [
    'a:has-text("No thanks, just take me to the downloads")',
    'a:has-text("No thanks")',
]

# The claim control on the download page, which is what actually adds the game
# to the library. UNVERIFIED: signed out, itch.io shows only the downloads; the
# claim control appears for a signed-in account on a claimable sale. Loose on
# purpose, and scoped to things that say "claim".
CLAIM_CONTROL = [
    'form[action*="claim"] button',
    'button:has-text("Claim")',
    'a.button:has-text("Claim")',
    'a:has-text("claim it")',
]

# A game that is browser-play only: nothing to download and nothing to keep,
# so nothing to claim. Answering "not eligible" for one is an answer, not a
# failure.
BROWSER_PLAY_ONLY = [
    'button.load_iframe_btn',
    'text=/run game/i',
]


async def _goto(page: Page, url: str) -> None:
    """Navigate, tolerating a superseded navigation the way epic._goto does."""
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
    """First visible match in priority order, one shared timeout (epic's shape)."""
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


class ItchAdapter(BaseAdapter):
    store = "itchio"
    display_name = "itch.io"
    login_url = f"{STORE_ROOT}/login"
    signin_url = f"{STORE_ROOT}/login"
    blurb = (
        "Claims games whose itch.io sale is 100% off, so they stay in your "
        "library after the sale ends. Plainly: automated claiming may be "
        "against itch.io's terms of service - this is for your own account, "
        "at your own risk. Expect many small games; Trove claims at most "
        f"{MAX_OFFERS} per run to stay polite."
    )

    REQUIREMENTS = [
        Requirement(
            name="A signed-in session",
            description=(
                "Sign in by hand once, with \"Sign in here\". Trove reuses "
                "that session and never stores your password."
            ),
        ),
        Requirement(
            name="Accepting the risk",
            description=(
                "Automated claiming may violate itch.io's terms of service. "
                "Trove keeps a polite cadence and a per-run cap, but the "
                "account is yours and so is the risk."
            ),
        ),
    ]

    # ── Discovery ────────────────────────────────────────────────────────

    async def list_free_offers(self) -> list[FreeOffer]:
        """The 100%-off sales, capped at MAX_OFFERS, with end times.

        One request for the listing, then one per kept offer for its sale
        page's end time and claimability - so at most 1 + MAX_OFFERS cheap
        sessionless GETs, spaced by POLITE_DELAY_S. A sale page that will not
        load degrades to "no end time, claimability unknown" rather than
        dropping the offer: the claim itself establishes the truth.
        """
        headers = {"User-Agent": "Trove/0.1 (self-hosted free game claimer)"}
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            response = await client.get(LISTING_URL, params={"format": "json"})
            response.raise_for_status()
            payload = response.json()
            content = payload.get("content") or ""

            parsed = _parse_listing(content)
            dropped = len(parsed) - MAX_OFFERS
            if dropped > 0:
                # The cap, out loud. CLAUDE.md's rule: never a silent cap.
                logger.info(
                    "itch.io: %d games are at -100%%; keeping the first %d and "
                    "dropping %d this run (they return on later runs while "
                    "their sales last).",
                    len(parsed), MAX_OFFERS, dropped,
                )
            kept = parsed[:MAX_OFFERS]

            offers: list[FreeOffer] = []
            for entry in kept:
                ends_at, claimable, min_price = await self._read_sale_page(
                    client, entry["sale_url"]
                )
                offers.append(
                    FreeOffer(
                        external_id=entry["game_id"],
                        title=entry["title"],
                        url=entry["url"],
                        image_url=entry["image_url"],
                        kind="game",
                        ends_at=ends_at,
                        source="store",
                        extra={
                            "game_id": entry["game_id"],
                            "sale_url": entry["sale_url"],
                            # True when the sale page's own button said "claim"
                            # on a normally-paid game; None when the page could
                            # not be read. claim() treats None as "find out".
                            "claimable": claimable,
                            "min_price_cents": min_price,
                        },
                    )
                )
                await asyncio.sleep(POLITE_DELAY_S)
            return offers

    async def _read_sale_page(
        self, client: httpx.AsyncClient, sale_url: str
    ) -> tuple[datetime | None, bool | None, int | None]:
        """(ends_at, claimable, min_price_cents) off one sale page, best effort.

        Measured markup (24 Aug 2026): the end time is
        `promotion_dates ... <span class="date_format">2026-09-20T13:07:00Z`,
        and the buy button carries `data-game` JSON with `min_price` (cents,
        the normal price) plus a label reading "Download or claim". A page
        that fails to load or parse answers None across the board - unknown,
        not false.

        Also measured: a MULTI-game sale page (one sale covering several
        games, e.g. `/s/197970/summer-sale`) has **no** buy button at all -
        the games are claimed from their own pages. Its end time still
        parses; its claimability honestly stays None and claim() finds out.
        """
        try:
            response = await client.get(sale_url)
            response.raise_for_status()
            page_html = response.text
        except Exception as exc:
            logger.info("itch.io: could not read the sale page %s: %s", sale_url, exc)
            return None, None, None

        ends_at = None
        # `date_format[^>]*>` rather than `class="date_format">`: itch.io
        # shuffles attribute order between responses (see the listing-parsing
        # comment), so the span's other attributes can land either side.
        found = re.search(
            r"promotion_dates.{0,200}?date_format[^>]*>"
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)",
            page_html,
            re.DOTALL,
        )
        if found:
            try:
                ends_at = datetime.fromisoformat(
                    found.group(1).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except ValueError:
                pass
        else:
            logger.info("itch.io: no end time found on %s.", sale_url)

        claimable: bool | None = None
        min_price: int | None = None
        button = re.search(
            r'<button[^>]*data-game="([^"]*)"[^>]*>([^<]*)</button>', page_html
        )
        if button:
            try:
                data = json.loads(html_lib.unescape(button.group(1)))
                min_price = data.get("min_price")
            except (ValueError, AttributeError):
                pass
            label = html_lib.unescape(button.group(2)).strip().lower()
            if "claim" in label and (min_price or 0) > 0:
                claimable = True
        return ends_at, claimable, min_price

    # ── Session ──────────────────────────────────────────────────────────

    async def health(self, page: Page) -> tuple[bool, str]:
        # The store front, not the /login form: signed in, the front page's
        # header shows the account; the login form would show a form either way.
        try:
            await _goto(page, f"{STORE_ROOT}/")
        except PlaywrightTimeout:
            return False, "itch.io did not load in time."

        if await _first_visible(page, CHALLENGE):
            return False, "itch.io is showing a challenge that needs a person."
        if await _first_visible(page, SIGNED_IN, timeout_ms=4000):
            return True, "Signed in."
        if await _first_visible(page, SIGNED_OUT):
            return False, "This account is signed out. Sign in again with \"Sign in here\"."
        return False, "Could not tell whether this account is signed in."

    # ── Ownership ────────────────────────────────────────────────────────

    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        if not offer.url:
            return None
        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            return None
        if await _first_visible(page, OWNED, timeout_ms=3000):
            return True
        # No marker is "could not tell", never "not owned": the banner wording
        # is unverified and a slow render must not teach the ledger a lie.
        return None

    # ── Claiming ─────────────────────────────────────────────────────────

    async def claim(
        self, page: Page, offer: FreeOffer, waiter: ChallengeWaiter | None = None
    ) -> ClaimResult:
        """One attempt: game page -> "Download or claim" -> "No thanks" ($0) ->
        the claim control -> ownership as ground truth.

        Money cannot move here by construction: the only things ever clicked
        are the buy button (which opens the pay-what-you-want form and orders
        nothing), the measured "No thanks, just take me to the downloads"
        link (the zero-dollar path), and a control labelled "claim". No
        amount field is filled, no pay control is touched, and the claim is
        only started while the page itself still shows the 100% sale.
        """
        if not offer.url:
            return ClaimResult(
                outcome="failed",
                detail="This offer has no itch.io page recorded, so it cannot be claimed.",
            )

        try:
            await _goto(page, offer.url)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The itch.io game page did not load.",
                await self._shot(page, offer, "page-timeout"),
            ) from None

        await self._guard(page, offer)

        if await _first_visible(page, OWNED, timeout_ms=2500):
            return ClaimResult(outcome="already_owned", detail="Already in your library.")

        # The never-spend guard: the page itself must still say the sale is
        # 100% off. Discovery saw it free, but a sale can end in between, and
        # a page showing a price is a page where nothing gets clicked.
        if not await _first_visible(page, SALE_100, timeout_ms=3000):
            return ClaimResult(
                outcome="failed",
                detail=(
                    "The page no longer shows a 100% sale, so nothing was "
                    "clicked. The sale probably ended."
                ),
            )

        buy = await _first_visible(page, BUY_BUTTON, timeout_ms=4000)
        if buy is None:
            if await _first_visible(page, BROWSER_PLAY_ONLY, timeout_ms=1500):
                return ClaimResult(
                    outcome="not_eligible",
                    detail=(
                        "This one is browser-play only - there is nothing to "
                        "download or keep, so there is nothing to claim."
                    ),
                )
            raise NeedsAttention(
                "Could not find the \"Download or claim\" button on the game "
                "page. itch.io has probably changed the layout; the label "
                "belongs in BUY_BUTTON.",
                await self._shot(page, offer, "no-buy-button"),
            )

        # A button that does not mention claiming is a plain "Download": free,
        # but with nothing to keep. Say so instead of pretending it was a claim.
        label = ""
        try:
            label = " ".join((await buy.inner_text() or "").split()).lower()
        except Exception:
            pass
        if label and "claim" not in label:
            return ClaimResult(
                outcome="not_eligible",
                detail=(
                    f"itch.io offers only “{label}” here, not a claim - "
                    "the game would not stay in the library, so it was left alone."
                ),
            )

        logger.info("itch.io: opening the purchase form for %r.", offer.title)
        try:
            await buy.click(timeout=6000)
        except PlaywrightTimeout:
            raise NeedsAttention(
                "The \"Download or claim\" button could not be clicked - "
                "something is covering it.",
                await self._shot(page, offer, "click-blocked"),
            ) from None
        await page.wait_for_timeout(1200)
        await self._guard(page, offer)

        # The pay-what-you-want form. The measured wording of the free path;
        # this is the only thing on the form that is ever clicked.
        no_thanks = await _first_visible(page, NO_THANKS, timeout_ms=6000)
        if no_thanks is not None:
            logger.info("itch.io: taking the $0 path for %r.", offer.title)
            try:
                await no_thanks.click(timeout=6000)
            except PlaywrightTimeout:
                raise NeedsAttention(
                    "The \"No thanks, just take me to the downloads\" link "
                    "could not be clicked.",
                    await self._shot(page, offer, "no-thanks-blocked"),
                ) from None
            await page.wait_for_timeout(1500)
            await self._guard(page, offer)

        # The download page. The claim control is the unverified half of this
        # flow; not finding it is an answer (browser/download-only, nothing to
        # keep), not a fault - but the answer is checked against ownership
        # first, because the claim may also have happened along the way.
        control = await _first_visible(page, CLAIM_CONTROL, timeout_ms=5000)
        clicked_claim = False
        if control is not None:
            logger.info("itch.io: claiming %r.", offer.title)
            try:
                await control.click(timeout=6000)
                clicked_claim = True
            except PlaywrightTimeout:
                raise NeedsAttention(
                    "The claim control could not be clicked - something is "
                    "covering it.",
                    await self._shot(page, offer, "claim-blocked"),
                ) from None
            await page.wait_for_timeout(1500)
            await self._guard(page, offer)

        # Ground truth, Epic's pattern: ask the game page whether the account
        # owns it now, whatever the flow appeared to do.
        try:
            await _goto(page, offer.url)
            if await _first_visible(page, OWNED, timeout_ms=6000):
                logger.info("itch.io: %r is now in the library.", offer.title)
                return ClaimResult(outcome="claimed", detail="Added to your library.")
        except PlaywrightTimeout:
            pass

        if not clicked_claim:
            return ClaimResult(
                outcome="not_eligible",
                detail=(
                    "itch.io offered downloads but no claim control, so this "
                    "looks download-only for the sale - nothing was added to "
                    "the library."
                ),
            )
        raise NeedsAttention(
            "The claim was clicked but Trove could not confirm the game landed "
            "in the library. Check the account before Trove tries again.",
            await self._shot(page, offer, "unconfirmed"),
        )

    # ── Shared ───────────────────────────────────────────────────────────

    async def _guard(self, page: Page, offer: FreeOffer) -> None:
        """Stop on anything only a person can answer. One attempt, no retries."""
        if await _first_visible(page, CHALLENGE, timeout_ms=700):
            raise NeedsAttention(
                "itch.io is showing a challenge that needs a person.",
                await self._shot(page, offer, "challenge"),
            )
        if await _first_visible(page, SIGNED_OUT, timeout_ms=600):
            raise NeedsAttention(
                "This account is signed out of itch.io. Sign in again with "
                "\"Sign in here\".",
                await self._shot(page, offer, "signed-out"),
            )

    async def _shot(self, page: Page, offer: FreeOffer, tag: str) -> str | None:
        account_id = int(offer.extra.get("account_id") or 0)
        return await screenshot(page, screenshot_name(account_id, self.store, tag))


# ── Listing parsing ─────────────────────────────────────────────────────────

# One game cell of the on-sale grid, all measured 24 Aug 2026 (shapes quoted in
# the module docstring). The cells arrive inside the JSON `content` string.
#
# A trap, measured the hard way: **itch.io serializes a tag's attributes in a
# different order on different requests.** The same cell arrived once as
# `<a class="price_tag meta_tag sale" title="..." href="/s/...">` and minutes
# later as `<a href="/s/..." title="..." class="price_tag meta_tag sale">` -
# same page, same game, shuffled attributes. A regex that assumes class-then-
# href parses one response and returns nothing for the next, which presents as
# discovery working "sometimes". So links are parsed as whole `<a>` tags and
# the class and href are read out of the attribute string separately.
_CELL_SPLIT = re.compile(r'(?=<div data-game_id=")')
_GAME_ID = re.compile(r'data-game_id="(\d+)"')
_A_TAG = re.compile(r"<a\b([^>]*)>([^<]*)")
_PRICE = re.compile(r'class="price_value"[^>]*>([^<]*)<')
_SALE_TAG = re.compile(r'class="sale_tag"[^>]*>([^<]*)<')
_COVER = re.compile(r'data-lazy_src="([^"]+)"')


def _attr(attrs: str, name: str) -> str:
    found = re.search(rf'{name}="([^"]*)"', attrs)
    return found.group(1) if found else ""


def _links(cell: str) -> tuple[tuple[str, str] | None, str | None]:
    """(the title link as (href, text), the sale-page href) - attribute-order
    independent, for the reason in the comment above."""
    title = None
    sale = None
    for attrs, text in _A_TAG.findall(cell):
        classes = _attr(attrs, "class").split()
        if "title" in classes and "game_link" in classes and title is None:
            title = (_attr(attrs, "href"), text)
        elif "price_tag" in classes and "sale" in classes and sale is None:
            href = _attr(attrs, "href")
            if href.startswith("/s/"):
                sale = href
    return title, sale


def _parse_listing(content: str) -> list[dict]:
    """The -100% cells of the on-sale grid, in the listing's (newest) order.

    Filter, both halves required: the cell's own `sale_tag` must be exactly
    "-100%" (not "In bundle", not a lesser discount) AND its `price_value`
    must read zero. A zero price with a smaller tag would be a game that was
    cheap anyway; a -100% tag with a price would be a parse gone wrong. Either
    way, only the pair is a giveaway.
    """
    entries: list[dict] = []
    seen: set[str] = set()
    for cell in _CELL_SPLIT.split(content):
        if not cell.startswith('<div data-game_id="'):
            continue
        tag = _SALE_TAG.search(cell)
        if not tag or tag.group(1).strip() != "-100%":
            continue
        price = _PRICE.search(cell)
        if not price or not _is_zero_price(price.group(1)):
            continue
        game_id = _GAME_ID.search(cell)
        title, sale = _links(cell)
        if not (game_id and title and title[0] and sale):
            logger.info("itch.io: a %s cell was missing a field; skipped.", "-100%")
            continue
        if game_id.group(1) in seen:
            continue
        seen.add(game_id.group(1))
        cover = _COVER.search(cell)
        entries.append(
            {
                # The numeric game id, which is stable; a slug is not (the
                # developer can rename the page mid-sale).
                "game_id": game_id.group(1),
                "title": html_lib.unescape(title[1].strip()),
                "url": title[0],
                "sale_url": f"{STORE_ROOT}{sale}",
                "image_url": cover.group(1) if cover else None,
            }
        )
    return entries


def _is_zero_price(text: str) -> bool:
    """"$0", "$0.00", "0€"... - a price whose digits are all zero."""
    digits = re.sub(r"[^\d]", "", text)
    return bool(digits) and set(digits) == {"0"}
