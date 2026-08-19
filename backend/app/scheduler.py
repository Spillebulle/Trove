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
from .config import get_settings
from .db import SessionLocal
from .models import Account
from .runner import run_account
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


def start() -> None:
    global _supervisor
    if _supervisor is not None:
        return
    _supervisor = asyncio.create_task(_supervise(), name="trove-scheduler")
    logger.info("Scheduler started.")


async def stop() -> None:
    """Cancel every loop and wait for the runs in flight to finish writing.

    The wait is the point. `task.cancel()` alone returns before the coroutine
    has unwound, and a run cancelled between placing an order and committing
    its ledger row is a claim that happened with no record of it.
    """
    global _supervisor
    tasks = list(_tasks.values())
    if _supervisor is not None:
        tasks.append(_supervisor)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _supervisor = None
    logger.info("Scheduler stopped.")


def status() -> dict:
    return {
        "running": _supervisor is not None and not _supervisor.done(),
        "watching": sorted(_tasks.keys()),
    }
