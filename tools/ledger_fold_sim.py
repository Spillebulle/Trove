"""The ledger's fold, and what a store's challenge does to an account.

Two things that are database-only, so they can be pinned without a browser, a
store or a network - and both are the kind of quiet mistake that is only
visible after a week of running.

  1. *The fold.* The claims table keeps one row per attempt, and it must: a run
     that could not claim something is a fact. But an add-on nobody can claim
     wrote eight identical "not claimed yet" rows a day into the page a person
     reads, which buries the week's real claims. `/api/claims` now returns the
     latest row per game per account with `attempts` counting the rest. Checked
     here: one row per game, the newest one, the count right, the fold done in
     SQL (so a limit counts games and not attempts), the filter applied before
     the fold, and `every_attempt=true` still handing back every row.

  2. *A challenge does not park an account.* `StoreChallenged` says the store
     refused Trove's browser, which says nothing about the session behind it.
     Filing that as `needs_attention` stops the scheduler until a person clears
     it - and nothing a person does clears a Cloudflare verdict, so the account
     sits parked while the runs that could have found the verdict gone are
     skipped. That was the sign-in / check / challenge / sign-in loop. Checked
     here: the run is recorded `attention` with the reason and the screenshot,
     the account's status is left exactly as it was, and the scheduler's
     one-time recovery frees an account an older build parked.

    python tools/ledger_fold_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
# A throwaway database per run; the sim must never touch a real DATA_DIR.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="trove-ledger-sim-")

from app import runner, scheduler  # noqa: E402
from app.browser import StoreChallenged  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Claim, Offer, Run  # noqa: E402
from app.routers.ledger import list_claims, list_offers, summary  # noqa: E402
from app.timeutil import utcnow  # noqa: E402
from datetime import timedelta  # noqa: E402

Base.metadata.create_all(engine)


def _seed() -> int:
    """One account, two offers, and a history with repeats in it."""
    db = SessionLocal()
    account = Account(
        label="Spillebulle", store="epic", profile_path="0001-spillebulle", status="ok"
    )
    db.add(account)
    db.commit()

    game = Offer(store="epic", external_id="ns:game", title="A Free Game", kind="game")
    dlc = Offer(store="epic", external_id="ns:dlc", title="An Add-on", kind="dlc")
    db.add_all([game, dlc])
    db.commit()

    # The add-on nobody can claim: eight runs, eight identical rows.
    for _ in range(8):
        db.add(
            Claim(
                account_id=account.id,
                offer_id=dlc.id,
                store="epic",
                title="An Add-on",
                kind="dlc",
                outcome="not_eligible",
                detail="The base game is not owned.",
            )
        )
    # The game: attempted, then claimed. The claim is the newest row.
    db.add(
        Claim(
            account_id=account.id,
            offer_id=game.id,
            store="epic",
            title="A Free Game",
            outcome="failed",
            detail="The checkout did not finish.",
        )
    )
    db.add(
        Claim(
            account_id=account.id,
            offer_id=game.id,
            store="epic",
            title="A Free Game",
            outcome="claimed",
            detail="Added to your library.",
        )
    )
    # A claim with no offer of its own - a base game bought so a DLC could be
    # used - which the fold has to group by title instead.
    db.add(
        Claim(
            account_id=account.id,
            offer_id=None,
            store="epic",
            title="The Base Game",
            outcome="claimed",
        )
    )
    db.commit()
    account_id = account.id
    db.close()
    return account_id


def _folded(**kwargs) -> list:
    db = SessionLocal()
    try:
        return list_claims(db=db, **kwargs)
    finally:
        db.close()


def _fold() -> bool:
    ok = True
    rows = _folded()
    titles = [row.title for row in rows]
    print("folded:", [(row.title, row.outcome, row.attempts) for row in rows])
    if len(rows) != 3:
        print("FAIL: 11 attempts over 3 games should fold to 3 rows, got", len(rows))
        ok = False
    if len(set(titles)) != len(titles):
        print("FAIL: a game appears twice:", titles)
        ok = False

    by_title = {row.title: row for row in rows}
    if by_title["An Add-on"].attempts != 8:
        print("FAIL: the add-on stands for 8 attempts, got", by_title["An Add-on"].attempts)
        ok = False
    if by_title["A Free Game"].outcome != "claimed":
        print(
            "FAIL: the newest row for the game is the claim, got",
            by_title["A Free Game"].outcome,
        )
        ok = False
    if by_title["A Free Game"].attempts != 2:
        print("FAIL: the game stands for 2 attempts, got", by_title["A Free Game"].attempts)
        ok = False
    if by_title["The Base Game"].attempts != 1:
        print(
            "FAIL: a row with no offer stands for itself, got",
            by_title["The Base Game"].attempts,
        )
        ok = False

    # The fold happens in SQL: a limit counts games, not attempts. Limit 2 over
    # 11 rows must be 2 games - a client-side fold would have handed back two
    # rows of the same add-on and called it a page.
    limited = _folded(limit=2)
    if len(limited) != 2 or len({row.title for row in limited}) != 2:
        print("FAIL: limit must count games, got", [row.title for row in limited])
        ok = False

    # Filtered, then folded: "Claimed" shows the latest *claimed* row of each
    # game, and a game whose newest row overall is something else is not hidden.
    claimed = _folded(outcome="claimed")
    if sorted(row.title for row in claimed) != ["A Free Game", "The Base Game"]:
        print("FAIL: the claimed filter folded wrong:", [row.title for row in claimed])
        ok = False

    every = _folded(every_attempt=True)
    if len(every) != 11:
        print("FAIL: every_attempt must return all 11 rows, got", len(every))
        ok = False
    if any(row.attempts != 1 for row in every):
        print("FAIL: an unfolded row stands only for itself")
        ok = False
    return ok


class _ChallengedRun:
    """A `_do_run` that meets a challenge, and nothing else."""

    def __init__(self, reason: str):
        self.reason = reason

    async def __call__(self, db, account, run, profile_path, watch=False):
        raise StoreChallenged(self.reason, "0001-epic-challenge.png")


async def _challenge_does_not_park(account_id: int) -> bool:
    ok = True
    reason = "Epic put a security check in front of Trove's own browser."
    original = runner._do_run
    runner._do_run = _ChallengedRun(reason)
    try:
        run_id = await runner.run_account(account_id, trigger="manual")
    finally:
        runner._do_run = original

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).first()
        account = db.query(Account).filter(Account.id == account_id).first()
        print(
            "challenged run:", run.status,
            "| account:", account.status,
            "| shot:", account.status_screenshot,
        )
        if run.status != "attention":
            print("FAIL: a challenged run is 'attention', got", run.status)
            ok = False
        if account.status != "ok":
            print("FAIL: a challenge must not park the account, status is", account.status)
            ok = False
        if account.status_reason != reason:
            print("FAIL: the reason is not recorded")
            ok = False
        if account.status_screenshot != "0001-epic-challenge.png":
            print("FAIL: the screenshot is not recorded")
            ok = False
    finally:
        db.close()
    return ok


def _older_build_is_unparked() -> bool:
    """The one-time recovery, against an account an older build parked."""
    db = SessionLocal()
    account = Account(
        label="Parked",
        store="epic",
        profile_path="0002-parked",
        status="needs_attention",
        status_reason="Epic is showing a challenge that needs a person.",
    )
    db.add(account)
    db.commit()
    account_id = account.id
    db.close()

    scheduler._unpark_challenged()

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        print("unparked:", account.status, "| reason kept:", bool(account.status_reason))
        ok = True
        if account.status != "ok":
            print("FAIL: the parked account was not freed, status is", account.status)
            ok = False
        if not account.status_reason:
            print("FAIL: the reason must be kept; the page is still worth reading")
            ok = False
        return ok
    finally:
        db.close()


def _undated_offers_end() -> bool:
    """An offer with no end date leaves Free now once the store stops listing it.

    Crystal Crisis (Steam, no deadline given) was last listed 14 Sep and still
    showed as free in October, because only `ends_at` could end an offer.
    """
    now = utcnow()
    db = SessionLocal()
    try:
        db.add_all([
            Offer(store="steam", external_id="u:listed", title="Undated, listed",
                  last_seen_at=now - timedelta(hours=3)),
            Offer(store="steam", external_id="u:gone", title="Undated, gone",
                  last_seen_at=now - timedelta(days=27)),
            Offer(store="epic", external_id="d:old-sighting", title="Dated, not over",
                  ends_at=now + timedelta(days=1), last_seen_at=now - timedelta(days=27)),
            Offer(store="epic", external_id="d:over", title="Dated, over",
                  ends_at=now - timedelta(hours=1), last_seen_at=now),
        ])
        db.commit()
        titles = {o.title for o in list_offers(current=True, db=db)}
        free_now = summary(db=db).free_now
    finally:
        db.close()
    expected = {"Undated, listed", "Dated, not over"}
    got = titles & {"Undated, listed", "Undated, gone", "Dated, not over", "Dated, over"}
    print("current offers:", sorted(got))
    ok = got == expected
    if not ok:
        print("FAIL: expected", sorted(expected))
    # The seeded rows from _seed() have no end date and were just seen, so they
    # count too; what matters is that the gone one does not.
    print("summary free_now:", free_now, "| listed now:", len(titles))
    if free_now != len(titles):
        print("FAIL: the dashboard count disagrees with the Free now list")
        ok = False
    return ok


async def main() -> int:
    account_id = _seed()
    ok = _fold()
    ok = _undated_offers_end() and ok
    ok = await _challenge_does_not_park(account_id) and ok
    ok = _older_build_is_unparked() and ok
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
