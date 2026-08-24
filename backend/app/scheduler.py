"""The scheduler: one loop per account, not one tick for the app.

CLAUDE.md asks for this shape specifically, and the reason is worth keeping in
front of whoever changes it. A global tick that walks every account in turn
makes one slow store delay every other account, and it makes every account run
at the same instant, which is a pattern. A loop per account means each has its
own interval, its own last-run time and its own jitter, and a store that hangs
for two minutes costs its own account two minutes and nobody else anything.

`next_run_at` is persisted rather than computed from `last_run_at` plus an
interval. That is what stops a restart from re-rolling the jitter and bunching
every account back onto the same minute.

Shutdown is graceful in the way HomeLab's poller is: the tasks are cancelled
and then awaited, so a run that is mid-claim gets to write its ledger row
before the process goes. A claim that happened and was not recorded is the one
failure mode this app must not have.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

from . import settings_store
from .adapters import ADAPTER_MAP
from .config import get_settings
from .db import SessionLocal
from .models import Account
from .runner import discover, discover_upcoming, run_account, sweep_pending_checkouts
from .timeutil import as_utc, utcnow

logger = logging.getLogger(__name__)

settings = get_settings()

# How often a per-account loop wakes to ask whether it is due. Not the
# interval: this is the granularity, and it is small so that turning the
# scheduler on, or changing an interval, takes effect within the minute rather
# than at the end of the current wait.
_TICK_S = 30

_tasks: dict[int, asyncio.Task] = {}
_supervisor: asyncio.Task | None = None
_housekeeper: asyncio.Task | None = None


def effective_interval_hours(account: Account) -> int:
    hours = account.interval_hours or settings.default_interval_hours
    return max(int(hours), settings.min_interval_hours)


def schedule_next(account: Account) -> None:
    """Set `next_run_at`, with jitter. Does not commit.

    The jitter is symmetric around the interval rather than added to it, so the
    average cadence is the interval the user asked for and not a slowly
    lengthening one.
    """
    hours = effective_interval_hours(account)
    spread = hours * settings.interval_jitter
    offset = random.uniform(-spread, spread)
    account.next_run_at = utcnow() + timedelta(hours=hours + offset)


async def _account_loop(account_id: int) -> None:
    """One account, forever: wait until due, run, schedule the next."""
    while True:
        try:
            await asyncio.sleep(_TICK_S)
            db = SessionLocal()
            try:
                if not settings_store.get(db, "schedule.enabled"):
                    continue
                account = db.query(Account).filter(Account.id == account_id).first()
                if account is None:
                    return  # deleted; the supervisor will drop the task
                if not account.enabled or account.status == "disabled":
                    continue
                # An account waiting for a person is not run. Opening a browser
                # against a store that has just asked a question is how a
                # question becomes a lock.
                if account.status == "needs_attention":
                    continue
                if account.status == "never_signed_in":
                    continue

                due = as_utc(account.next_run_at)
                if due is None:
                    schedule_next(account)
                    db.commit()
                    continue
                if due > utcnow():
                    continue

                schedule_next(account)
                db.commit()
            finally:
                db.close()

            logger.info("Scheduled run for account %s.", account_id)
            await run_account(account_id, trigger="schedule")
        except asyncio.CancelledError:
            raise
        except Exception:
            # A loop that dies takes its account with it until a restart, so
            # nothing but cancellation is allowed out. The run itself already
            # records its own failure; this is the backstop for a bug in the
            # scheduling around it.
            logger.exception("The scheduler loop for account %s hit an error.", account_id)
            await asyncio.sleep(60)


async def _supervise() -> None:
    """Keep one loop per account, following accounts being added and removed."""
    while True:
        try:
            db = SessionLocal()
            try:
                ids = {row.id for row in db.query(Account.id).all()}
            finally:
                db.close()

            for account_id in ids - _tasks.keys():
                _tasks[account_id] = asyncio.create_task(
                    _account_loop(account_id), name=f"trove-account-{account_id}"
                )
                logger.debug("Watching account %s.", account_id)

            for account_id in list(_tasks.keys() - ids):
                task = _tasks.pop(account_id)
                task.cancel()
                logger.debug("Stopped watching account %s.", account_id)

            for account_id, task in list(_tasks.items()):
                if task.done():
                    _tasks.pop(account_id, None)

            await asyncio.sleep(_TICK_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The scheduler supervisor hit an error.")
            await asyncio.sleep(60)


async def _housekeep() -> None:
    """The one global loop beside the per-account ones: the cheap work.

    Everything in here is sessionless - one HTTP request per store, no
    browser, no account touched - which is why one loop for the whole app is
    fine where claim runs get a loop per account: there is no store session
    to be slow with and nothing here can get anybody flagged. Each tick it

    * asks every store what is free and what is announced (`discover` +
      `discover_upcoming`), so a giveaway that drops between two claim runs is
      noticed - and `discover` then pulls the due accounts forward, which is
      the whole reason the tick runs more often than the accounts do;
    * sweeps the pending checkouts (`sweep_pending_checkouts`): expires the
      ones whose giveaway has gone, and reminds - once - about the ones about
      to.

    The cadence is `discovery_interval_hours`, jittered plus or minus 20 % so
    the app does not hit the endpoint on a clock edge, and the whole tick sits
    behind the same "schedule.enabled" switch as the runs: an install still
    being set up should be completely quiet.
    """
    # The first tick comes a few minutes after start rather than an interval
    # later, so an app that was down over a giveaway's drop catches up soon
    # after it is back - and rather than *at* start, so a restart loop cannot
    # turn into a request loop.
    delay = random.uniform(120, 300)
    while True:
        try:
            await asyncio.sleep(delay)
            delay = settings.discovery_interval_hours * 3600 * random.uniform(0.8, 1.2)
            db = SessionLocal()
            try:
                enabled = settings_store.get(db, "schedule.enabled")
            finally:
                db.close()
            if not enabled:
                continue
            await _housekeep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Same rule as the account loops: nothing but cancellation gets
            # out, because a housekeeping loop that dies takes discovery's
            # early warning with it until a restart.
            logger.exception("The housekeeping tick hit an error.")


async def _housekeep_once() -> None:
    """One tick: discovery for every store, then the checkout sweep."""
    db = SessionLocal()
    try:
        fresh: set[str] = set()
        for store in ADAPTER_MAP:
            try:
                await discover(db, store)
                # Only a discovery that answered may later be read as "the
                # offer is gone from the list"; a fetch that failed says
                # nothing about the offer, only about the network.
                fresh.add(store)
                await discover_upcoming(db, store)
            except Exception as exc:
                # A broken endpoint degrades to the per-account cadence, never
                # to a stopped app - the claim runs discover for themselves.
                db.rollback()
                logger.warning("The discovery tick for %s failed: %s", store, exc)
        sweep_pending_checkouts(db, fresh)
    finally:
        db.close()


def _unpark_blocked_checkouts() -> None:
    """One-time recovery for databases written by builds that parked these.

    Those builds answered a checkout captcha by filing the account
    `needs_attention`, which the scheduler skips forever - so one missed
    captcha week silently parked the account for every future giveaway. The
    marker and the reason survive (the button and the note still show); only
    the parking is undone. An account that is *also* genuinely broken gets
    re-filed by its next run's health check, which is the check that parked
    it honestly in the first place.
    """
    db = SessionLocal()
    try:
        stuck = (
            db.query(Account)
            .filter(
                Account.status == "needs_attention",
                Account.checkout_offer.isnot(None),
            )
            .all()
        )
        for account in stuck:
            account.status = "ok"
            logger.info(
                "Account %s was parked over a blocked checkout; scheduling it "
                "again. The claim for %r still waits in the un-driven window.",
                account.id,
                account.checkout_offer,
            )
        if stuck:
            db.commit()
    finally:
        db.close()


def start() -> None:
    global _supervisor, _housekeeper
    if _supervisor is not None:
        return
    _unpark_blocked_checkouts()
    _supervisor = asyncio.create_task(_supervise(), name="trove-scheduler")
    _housekeeper = asyncio.create_task(_housekeep(), name="trove-housekeeping")
    logger.info("Scheduler started.")


async def stop() -> None:
    """Cancel every loop and wait for the runs in flight to finish writing.

    The wait is the point. `task.cancel()` alone returns before the coroutine
    has unwound, and a run cancelled between placing an order and committing
    its ledger row is a claim that happened with no record of it.
    """
    global _supervisor, _housekeeper
    tasks = list(_tasks.values())
    if _supervisor is not None:
        tasks.append(_supervisor)
    if _housekeeper is not None:
        tasks.append(_housekeeper)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _supervisor = None
    _housekeeper = None
    logger.info("Scheduler stopped.")


def status() -> dict:
    return {
        "running": _supervisor is not None and not _supervisor.done(),
        "watching": sorted(_tasks.keys()),
    }
