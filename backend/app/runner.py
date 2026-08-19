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


async def run_account(account_id: int, trigger: str = "schedule") -> int:
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
        label = account.label
        profile_path = settings.profiles_path / account.profile_path

        run = Run(account_id=account.id, store=store, trigger=trigger, status="running")
        db.add(run)
        db.commit()
        run_id = run.id
        started = utcnow()

        try:
            await _do_run(db, account, run, profile_path)
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
                    context=store,
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
                    context=store,
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
                    context=store,
                ),
            )
        return run_id
    finally:
        db.close()


async def _do_run(db: Session, account: Account, run: Run, profile_path: Path) -> None:
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

            result = await adapter.claim(page, offer)
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
                        title=f"Claimed {row.title}",
                        detail=result.detail or f"Added to {account.label}.",
                        severity="good",
                        context=f"{store} . {account.label}",
                        url=row.url,
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
