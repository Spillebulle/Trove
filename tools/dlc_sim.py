"""Drive the DLC prerequisite logic without a browser or a store.

A free add-on is only worth claiming if the account owns the game it extends,
and the branch that decides is the kind of thing that breaks quietly: it either
claims something unusable, or skips something it should have taken. So it is
exercised here, in CI, with a fake adapter.

The four cases, which are the four a person would recognise:

  owned         -> claim the add-on, and nothing else
  free base     -> claim the game first, then the add-on, two ledger rows
  paid base     -> claim nothing, and write down why, naming game and price
  cannot tell   -> claim nothing, and say that it could not tell

    python tools/dlc_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", "./data")

from app import runner  # noqa: E402
from app.adapters.base import BaseGame, ClaimResult, FreeOffer  # noqa: E402


class FakeRow:
    """Stands in for an `offers` row."""

    def __init__(self, title, kind="dlc"):
        self.id = 1
        self.title = title
        self.kind = kind
        self.url = None
        self.image_url = "poster.png"
        self.external_id = "ns:dlc"


class FakeAdapter:
    display_name = "Epic Games Store"

    def __init__(self, base: BaseGame | None, base_result="claimed"):
        self._base = base
        self._base_result = base_result
        self.claimed: list[str] = []

    async def inspect_base_game(self, page, offer):
        return self._base

    async def claim(self, page, offer, waiter=None):
        self.claimed.append(offer.title)
        return ClaimResult(outcome=self._base_result, detail="Added to your library.")


class FakeDB:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        pass


class FakeRun:
    claimed = 0
    already_owned = 0
    id = 1


class FakeAccount:
    id = 1
    store = "epic"
    label = "spillebulle"
    checkout_offer = None


def _offer():
    return FreeOffer(
        external_id="ns:dlc",
        title="Epic Mage Bundle",
        kind="dlc",
        extra={"namespace": "ns", "offer_id": "dlc", "account_id": 1,
               "base_url": "https://store.epicgames.com/en-US/p/albion-online-7eb24d"},
    )


async def _case(name, base, *, expect_go, expect_claims, expect_words=(), base_result="claimed"):
    db, run, account = FakeDB(), FakeRun(), FakeAccount()
    adapter = FakeAdapter(base, base_result)
    row = FakeRow("Epic Mage Bundle")
    go = await runner._satisfy_base_game(
        db, account, run, adapter, object(), _offer(), row, None
    )
    details = " ".join((r.detail or "") for r in db.rows)
    ok = True
    if go is not expect_go:
        print(f"FAIL [{name}]: expected go={expect_go}, got {go}"); ok = False
    if adapter.claimed != expect_claims:
        print(f"FAIL [{name}]: expected claims {expect_claims}, got {adapter.claimed}"); ok = False
    for word in expect_words:
        if word.lower() not in details.lower():
            print(f"FAIL [{name}]: the reason never mentions {word!r}: {details!r}"); ok = False
    print(f"{'ok  ' if ok else 'FAIL'} {name}: go={go} claims={adapter.claimed} rows={len(db.rows)}")
    if ok and db.rows:
        print(f"       reason: {details[:150]}")
    return ok


async def main() -> int:
    albion = "Albion Online"
    base_offer = FreeOffer(external_id="ns:base", title=albion, kind="game",
                           extra={"namespace": "ns", "offer_id": "base"})
    ok = True
    # The game is owned: claim the add-on, touch nothing else.
    ok &= await _case("base owned", BaseGame(title=albion, owned=True),
                      expect_go=True, expect_claims=[])
    # Free game, claimable: take the game first, then let the add-on through.
    ok &= await _case("base free", BaseGame(title=albion, owned=False, free=True, offer=base_offer),
                      expect_go=True, expect_claims=[albion], expect_words=("Epic Mage Bundle",))
    # Paid game: claim nothing, and say what is needed and what it costs.
    ok &= await _case("base paid",
                      BaseGame(title=albion, owned=False, free=False, price_note="$29.99"),
                      expect_go=False, expect_claims=[], expect_words=(albion, "$29.99", "not free"))
    # Could not tell: claim nothing, and be honest that it is uncertainty.
    ok &= await _case("cannot tell", BaseGame(title=albion),
                      expect_go=False, expect_claims=[], expect_words=(albion, "could not tell"))
    # Free but the game itself failed to claim: the add-on must not go ahead.
    ok &= await _case("base free but failed",
                      BaseGame(title=albion, owned=False, free=True, offer=base_offer),
                      expect_go=False, expect_claims=[albion],
                      expect_words=("could not be claimed",), base_result="failed")
    # No opinion from the adapter: the add-on stands alone, as it always did.
    ok &= await _case("adapter says nothing", None, expect_go=True, expect_claims=[])
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
