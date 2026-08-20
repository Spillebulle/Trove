"""One run: visit a store as one account, claim what is free, write it down.

This is the loop CLAUDE.md asks to be proven end to end before a second
adapter exists: discover, claim, ledger row, UI.

The order matters and is worth stating, because it is what keeps the app
polite:

  1. **Discover without a session.** Ask the adapter what is free. This is a
     public endpoint; no browser is opened and no account is touched. If
     nothing is free, the run ends here having cost a single HTTP request.
  2. **Skip what is already recorded.** An offer this account has a `claimed`
     row for is not attempted again. The ledger is the memory, so a restart,
     a manual run and a scheduled run cannot combine to claim the same thing
     three times.
  3. **Only then open a browser.** One profile, one store, one account at a
     time, with the session checked before anything is clicked.
  4. **One attempt per offer.** An attempt that meets a challenge stops the
     whole run and files the account. It does not move on to the next offer:
     whatever asked the question will ask it again.

Everything the run learns lands in the database before it is announced, so a
notification never describes something the UI cannot show a row for.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from . import notify
from .adapters import FreeOffer, get_adapter
from .browser import (
    NeedsAttention,
    ProfileBusy,
    first_page,
    manager,
    screenshot,
    screenshot_name,
)
from .config import get_settings
from .crypto import encrypt
from .db import SessionLocal
from .models import Account, Claim, Offer, Run
from .timeutil import utcnow

logger = logging.getLogger(__name__)

settings = get_settings()

# Between two claims in the same run. Human cadence at the small scale: a
# person reading a store page does not place two orders in the same second, and
# the cost of being wrong about that is the account, not the game.
PAUSE_BETWEEN_CLAIMS_S = 4.0

# How long a watched run keeps the browser open on the screen after it finishes
# or stops, so a person can look at the page that failed. They close it sooner
# with the "Done" button; this is only the cap for when they wander off.
WATCH_HOLD_MAX_S = 300.0

# The release signal for each account's held-open watched run. The screen view's
# "Done" sets it through `release_watch`, which lets the run close the browser
# and finish. A dict rather than one event because two accounts can be watched
# at once, though rarely.
_watch_release: dict[int, asyncio.Event] = {}
# Sticky "stop" so a Done pressed *before* the run reaches its hold is not lost:
# without it, closing the watch dialog early would set nothing (no waiter yet),
# and the hold would then open and sit for the full cap with nobody watching.
_watch_stop: set[int] = set()


def begin_watch(account_id: int) -> None:
    """Clear any stale stop before a fresh watched run, so it holds normally."""
    _watch_stop.discard(account_id)


def release_watch(account_id: int) -> bool:
    """Let a held-open watched run close its browser. Returns whether one waited.

    Sticky: if the hold has not started yet, the request is remembered and the
    hold is skipped when it would begin.
    """
    _watch_stop.add(account_id)
    event = _watch_release.get(account_id)
    if event is not None:
        event.set()
        return True
    return False


def is_watch_holding(account_id: int) -> bool:
    """Is a watched run currently holding this account's browser open?"""
    return account_id in _watch_release


# How long a watched run will wait for a person to solve a captcha on the screen
# before it gives up. Long, because reading nine blurry forklifts takes a while.
CAPTCHA_WAIT_MAX_S = 300.0

# Accounts whose watched run is paused on a captcha right now, so a UI can say
# "solve it on the screen" rather than leave a spinner unexplained.
_awaiting_captcha: set[int] = set()


def is_awaiting_captcha(account_id: int) -> bool:
    return account_id in _awaiting_captcha


class _CaptchaWaiter:
    """Holds a watched claim while the person solves a captcha on the screen.

    The adapter calls ``wait`` with a check for its own challenge being gone;
    this blocks - the browser stays open on the screen the whole time - until it
    clears (resume), the person presses Done (stop), or the cap passes. It is
    the runner's half of the adapter's ``ChallengeWaiter`` protocol: the store
    knows how to see its captcha, the runner knows how to wait and what state a
    page should show.
    """

    def __init__(self, account_id: int, label: str, store_name: str) -> None:
        self.account_id = account_id
        self.label = label
        self.store_name = store_name

    async def wait(self, is_cleared, image_name: str | None = None) -> None:
        aid = self.account_id
        _awaiting_captcha.add(aid)
        logger.info("Account %s: paused on a captcha; waiting for it on the screen.", aid)
        image_path = (
            str(settings.screenshots_path / image_name) if image_name else None
        )
        # Ping once, so someone who started the watch and walked away knows to
        # come back. It respects the same "on attention" switch as every other
        # nudge; a webhook is enough, no bot needed to *send*.
        notify.send_soon(
            "attention",
            notify.Notification(
                title=f"{self.label}: a captcha needs you",
                detail=(
                    f"{self.store_name} put up a captcha at the checkout. Open "
                    "Trove, watch this account, and solve it on the screen - the "
                    "claim finishes on its own the moment it clears."
                ),
                severity="caution",
                context=self.store_name,
                image_path=image_path,
            ),
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + CAPTCHA_WAIT_MAX_S
        try:
            while loop.time() < deadline:
                if await is_cleared():
                    logger.info("Account %s: captcha cleared; resuming the claim.", aid)
                    return
                if aid in _watch_stop:
                    raise NeedsAttention(
                        "The captcha was not solved - you closed the watch. It "
                        "is still there for next time."
                    )
                await asyncio.sleep(2)
        finally:
            _awaiting_captcha.discard(aid)
        # Do not then hold the browser open a second time for the full cap.
        _watch_stop.add(aid)
        raise NeedsAttention(
            "A captcha appeared at checkout and was not solved in time. Press "
            "Run and watch to solve it on the screen, and the claim continues."
        )


async def _hold_open_for_watch(account_id: int) -> None:
    """Keep the browser open until the watcher presses Done or the cap passes.

    Runs inside the `manager.session` block, so the context - and the window on
    the container's screen - stays open for as long as this waits. It is what
    turns a run that flashes past into one a person can actually see fail.
    """
    if account_id in _watch_stop:
        _watch_stop.discard(account_id)
        return  # Done was pressed before we got here; do not hold.
    event = asyncio.Event()
    _watch_release[account_id] = event
    logger.info("Holding the browser open on the screen for account %s.", account_id)
    try:
        await asyncio.wait_for(event.wait(), timeout=WATCH_HOLD_MAX_S)
    except (asyncio.TimeoutError, TimeoutError):
        logger.info("The watch hold for account %s timed out; closing.", account_id)
    finally:
        _watch_release.pop(account_id, None)
        _watch_stop.discard(account_id)


class RunCancelled(Exception):
    """The app is shutting down mid-run."""


async def discover(db: Session, store: str) -> list[FreeOffer]:
    """Ask a store what is free, and record it. No account, no browser.

    Returns what the adapter said, and updates the `offers` table so the UI can
    show what is free whether or not anybody has claimed it. An offer already
    known keeps its `first_seen_at` and gets a fresh `last_seen_at`, which is
    what lets the UI say how long something has been up.
    """
    adapter = get_adapter(store)
    offers = await adapter.list_free_offers()
    now = utcnow()

    for offer in offers:
        row = (
            db.query(Offer)
            .filter(Offer.store == store, Offer.external_id == offer.external_id)
            .first()
        )
        if row is None:
            row = Offer(
                store=store,
                external_id=offer.external_id,
                first_seen_at=now,
            )
            db.add(row)
        row.title = offer.title
        row.url = offer.url
        row.image_url = offer.image_url
        row.kind = offer.kind
        row.starts_at = offer.starts_at
        row.ends_at = offer.ends_at
        row.source = offer.source
        row.last_seen_at = now
    db.commit()
    return offers


def _offer_row(db: Session, store: str, external_id: str) -> Offer | None:
    return (
        db.query(Offer)
        .filter(Offer.store == store, Offer.external_id == external_id)
        .first()
    )


def _already_claimed(db: Session, account_id: int, offer_id: int | None) -> bool:
    """Has this account got a successful row for this offer already?

    `claimed` and `already_owned` both count. The second is the more common
    one after the first week, and re-checking a title the store has already
    said the account owns is a page load spent to learn nothing.
    """
    if offer_id is None:
        return False
    return (
        db.query(Claim.id)
        .filter(
            Claim.account_id == account_id,
            Claim.offer_id == offer_id,
            Claim.outcome.in_(("claimed", "already_owned")),
        )
        .first()
        is not None
    )


def _set_status(
    db: Session,
    account: Account,
    status: str,
    reason: str | None = None,
    shot: str | None = None,
) -> None:
    account.status = status
    account.status_reason = reason
    account.status_at = utcnow()
    account.status_screenshot = shot
    db.commit()


async def check_session(account_id: int) -> tuple[bool, str]:
    """Ask the store whether this account is signed in, and record the answer.

    The other half of the un-driven sign-in window. A person signs in somewhere
    Trove has no connection to, so Trove cannot know it happened - and it must
    not simply believe it did, because "signed in" is the flag that lets the
    scheduler start opening browsers. So the window closing is a prompt to go
    and look, not evidence in itself.

    Cheap: one page load and one selector check, no claiming. Returns (ok, a
    sentence), and writes the account's status either way, so a person who
    closed the window without finishing sees that rather than a silent success.
    """
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if account is None:
            raise ValueError(f"No account with id {account_id}")
        adapter = get_adapter(account.store)
        profile_path = settings.profiles_path / account.profile_path

        try:
            async with manager.session(
                account.id, profile_path, holder="a sign-in check", wait_s=5.0
            ) as context:
                page = await first_page(context)
                healthy, sentence = await adapter.health(page)
                shot = None
                if not healthy:
                    shot = await screenshot(
                        page, screenshot_name(account.id, account.store, "signin-check")
                    )
        except ProfileBusy as exc:
            return False, str(exc)

        if healthy:
            _set_status(db, account, "ok", None, None)
            # Only now is it worth scheduling: an account that has never been
            # signed in has nothing for a run to do but fail.
            from . import scheduler

            scheduler.schedule_next(account)
            db.commit()
        else:
            _set_status(db, account, "needs_attention", sentence, shot)
        return healthy, sentence
    finally:
        db.close()


async def run_account(account_id: int, trigger: str = "schedule", watch: bool = False) -> int:
    """Run one account once. Returns the run id.

    Opens its own database session: this is called from a scheduler task and
    from a request handler, and holding a request's session across a browser
    session would keep a connection checked out for minutes.
    """
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if account is None:
            raise ValueError(f"No account with id {account_id}")
        store = account.store
        store_name = get_adapter(store).display_name
        label = account.label
        profile_path = settings.profiles_path / account.profile_path

        run = Run(account_id=account.id, store=store, trigger=trigger, status="running")
        db.add(run)
        db.commit()
        run_id = run.id
        started = utcnow()

        begin_watch(account.id)
        try:
            await _do_run(db, account, run, profile_path, watch=watch)
        except ProfileBusy as exc:
            run.status = "failed"
            run.message = str(exc)
            logger.info("Run %s skipped: %s", run_id, exc)
        except NeedsAttention as exc:
            run.status = "attention"
            run.message = exc.reason
            _set_status(db, account, "needs_attention", exc.reason, exc.screenshot)
            notify.send_soon(
                "attention",
                notify.Notification(
                    title=f"{label} needs a hand",
                    detail=exc.reason,
                    severity="caution",
                    context=store_name,
                ),
            )
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.message = "Trove was shutting down."
            raise
        except Exception as exc:
            logger.exception("Run %s failed", run_id)
            run.status = "failed"
            run.message = f"{type(exc).__name__}: {exc}"
            _set_status(db, account, "needs_attention", run.message)
            notify.send_soon(
                "failed",
                notify.Notification(
                    title=f"{label} could not be checked",
                    detail=run.message,
                    severity="critical",
                    context=store_name,
                ),
            )
        else:
            run.status = "ok"
        finally:
            run.finished_at = utcnow()
            run.duration_s = (run.finished_at - started).total_seconds()
            account.last_run_at = run.finished_at
            db.commit()

        if run.status == "ok" and run.claimed:
            notify.send_soon(
                "run_summary",
                notify.Notification(
                    title=f"{label}: {run.claimed} claimed",
                    detail=(
                        f"{run.claimed} claimed, {run.already_owned} already "
                        f"owned, out of {run.offers_seen} free right now."
                    ),
                    severity="good",
                    context=store_name,
                ),
            )
        return run_id
    finally:
        db.close()


async def _do_run(
    db: Session, account: Account, run: Run, profile_path: Path, watch: bool = False
) -> None:
    """The body of a run, with the bookkeeping left to the caller."""
    store = account.store
    adapter = get_adapter(store)

    offers = await discover(db, store)
    run.offers_seen = len(offers)
    db.commit()

    pending: list[tuple[FreeOffer, Offer]] = []
    for offer in offers:
        row = _offer_row(db, store, offer.external_id)
        if row is None:
            continue
        if _already_claimed(db, account.id, row.id):
            run.already_owned += 1
            continue
        # The account id rides along so the adapter can name its screenshots
        # without being handed the database.
        offer.extra["account_id"] = account.id
        pending.append((offer, row))

    db.commit()

    if not pending:
        logger.info(
            "Nothing to claim for %s on %s: %d free, all accounted for.",
            account.label,
            store,
            len(offers),
        )
        if account.status == "needs_attention":
            # A run that found nothing to do also found nothing wrong, but it
            # never opened a browser, so it cannot clear an attention flag. Say
            # nothing rather than clearing it on no evidence.
            pass
        return

    async with manager.session(
        account.id, profile_path, holder="a claim run", wait_s=2.0
    ) as context:
        page = await first_page(context)
        try:
            # A waiter for every run - watched or not - so a captcha pauses
            # the run and can be solved by jumping into it on the screen,
            # rather than failing and needing the whole run again.
            waiter = _CaptchaWaiter(account.id, account.label, adapter.display_name)
            await _run_claims(db, account, run, adapter, page, pending, waiter)
        finally:
            # In watch mode the browser stays on the screen until the person
            # presses Done, whatever the run came to - so a checkout that fails
            # is still there to look at rather than gone in the half-second
            # before the window closes. Outside watch mode this is a no-op.
            if watch:
                await _hold_open_for_watch(account.id)


async def _run_claims(db, account, run, adapter, page, pending, waiter) -> None:
    """Health-check the session, then attempt each pending offer."""
    store = account.store

    healthy, sentence = await adapter.health(page)
    if not healthy:
        shot = await screenshot(
            page, screenshot_name(account.id, store, "health")
        )
        raise NeedsAttention(sentence, shot)

    # The session is good, so an old attention flag is stale.
    if account.status != "ok":
        _set_status(db, account, "ok", None, None)

    for index, (offer, row) in enumerate(pending):
        if index:
            await asyncio.sleep(PAUSE_BETWEEN_CLAIMS_S)

        owned = await adapter.is_owned(page, offer)
        if owned is True:
            _record(
                db,
                account,
                run,
                row,
                outcome="already_owned",
                detail="Already in your library.",
            )
            run.already_owned += 1
            db.commit()
            continue

        result = await adapter.claim(page, offer, waiter=waiter)
        _record(
            db,
            account,
            run,
            row,
            outcome=result.outcome,
            detail=result.detail,
            key_code=result.key_code,
            key_store=result.key_store,
            shot=result.screenshot,
        )
        if result.outcome == "claimed":
            run.claimed += 1
            notify.send_soon(
                "claimed",
                notify.Notification(
                    # The game is the headline and its poster is the picture;
                    # the store and account go in the footer. A claim should
                    # look like the thing it is, not like a log line.
                    title=row.title,
                    detail=result.detail or "Added to your library.",
                    severity="good",
                    context=f"{adapter.display_name} · {account.label}",
                    url=row.url,
                    image_url=row.image_url,
                ),
            )
        elif result.outcome == "already_owned":
            run.already_owned += 1
        db.commit()


def _record(
    db: Session,
    account: Account,
    run: Run,
    offer: Offer,
    outcome: str,
    detail: str | None = None,
    key_code: str | None = None,
    key_store: str | None = None,
    shot: str | None = None,
) -> Claim:
    """Write the ledger row.

    The title is copied off the offer rather than left to a join: an offer row
    can be pruned once its promotion is a year gone, and the ledger has to keep
    reading properly on its own.
    """
    claim = Claim(
        account_id=account.id,
        offer_id=offer.id,
        run_id=run.id,
        store=account.store,
        title=offer.title,
        outcome=outcome,
        detail=detail,
        key_code=encrypt(key_code) if key_code else None,
        key_store=key_store,
        screenshot=shot,
    )
    db.add(claim)
    return claim
