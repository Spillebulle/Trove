"""Drive the session-discovery runner path, and Prime's claim loop, without a
browser or a store.

Prime Gaming has no public offers feed (measured: the signed-out page is an
empty SPA shell, the data endpoint answers 403), so its adapter declares
`discovery_needs_session` and the runner discovers *inside* the account's
browser session. That path reorders the run's steps, and the reorder is the
kind of thing that breaks quietly, so it is pinned here against a real SQLite
database and a fake adapter:

  1. *Session discovery, first run.* The sessionless `discover()` is an honest
     no-op, the browser session opens anyway, the health check runs BEFORE
     `discover_in_session` (a signed-out page renders no offers, and an empty
     page must read as "signed out", never "nothing free"), the found offers
     land as `Offer` rows through the shared persistence, an already-claimed
     offer is skipped, and the new one gets a `claimed` ledger row.
  2. *Session discovery, everything claimed.* The session still opens (it has
     to, to discover), nothing is attempted, the run ends ok.
  3. *A sessionless store with nothing free* never opens a browser at all -
     the boundary the whole app defends, unchanged.
  4. *An unhealthy session* stops before `discover_in_session` is ever called.

Then the Prime claim loop itself, with the page helpers stubbed the way
`checkout_sim.py` stubs Epic's:

  5. *Direct claim:* the claim button is clicked exactly once, then only read.
  6. *Key claim:* the revealed code and its store come back on the result.
  7. *Link wall:* stops before any click, naming the missing store link.

    python tools/prime_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
# A throwaway database per run; the sim must never touch a real DATA_DIR.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="trove-prime-sim-")

from app import runner  # noqa: E402
import app.adapters.prime as prime  # noqa: E402
from app.adapters.base import BaseAdapter, ClaimResult, FreeOffer  # noqa: E402
from app.browser import NeedsAttention  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Claim, Offer, Run  # noqa: E402

Base.metadata.create_all(engine)


# ── Fakes for the runner half ───────────────────────────────────────────────


# Playwright resolves `"<sel> >> visible=true"` to the visible matches of
# `<sel>`, which is what the adapters' `_first_visible` now asks for - a
# locator's *first* match is not necessarily a visible one. These fakes model
# visibility with their own bookkeeping, so the suffix is stripped and the
# answer is unchanged: same selector, same visibility rules.
def _bare(sel):
    return sel.split(" >> visible=true")[0]


class FakePage:
    url = "https://luna.amazon.com/claims/home"

    async def wait_for_timeout(self, ms):
        pass


class FakeManager:
    """Counts session opens; the assertion for 'no browser was opened'."""

    def __init__(self):
        self.opens = 0

    @asynccontextmanager
    async def session(self, account_id, profile_path, holder, wait_s=0.0):
        self.opens += 1
        yield object()


async def _fake_first_page(context):
    return FakePage()


runner.first_page = _fake_first_page


class FakeSessionAdapter(BaseAdapter):
    """A Prime-shaped store: no public feed, offers only in-session."""

    store = "prime"
    display_name = "Prime Gaming"
    login_url = "https://gaming.amazon.com/home"
    discovery_needs_session = True

    def __init__(self, offers, healthy=True):
        self.offers = offers
        self.healthy = healthy
        self.calls: list[str] = []

    async def list_free_offers(self):
        self.calls.append("list_free_offers")
        return []

    async def discover_in_session(self, page):
        self.calls.append("discover_in_session")
        return list(self.offers)

    async def health(self, page):
        self.calls.append("health")
        return (True, "Signed in.") if self.healthy else (False, "Signed out.")

    async def is_owned(self, page, offer):
        self.calls.append(f"is_owned:{offer.title}")
        return None

    async def claim(self, page, offer, waiter=None):
        self.calls.append(f"claim:{offer.title}")
        return ClaimResult(outcome="claimed", detail="Claimed to your library.")


class FakePublicAdapter(BaseAdapter):
    """An Epic-shaped store: public feed, nothing free this week."""

    store = "pubstore"
    display_name = "Public Store"
    login_url = "https://example.com"

    def __init__(self):
        self.calls: list[str] = []

    async def list_free_offers(self):
        self.calls.append("list_free_offers")
        return []

    async def health(self, page):
        self.calls.append("health")
        return True, "Signed in."

    async def is_owned(self, page, offer):
        return None

    async def claim(self, page, offer, waiter=None):
        self.calls.append(f"claim:{offer.title}")
        return ClaimResult(outcome="claimed")


def _offers():
    return [
        FreeOffer(external_id="amzn1.offer.aaaa1111", title="Old Game"),
        FreeOffer(external_id="amzn1.offer.bbbb2222", title="New Game"),
    ]


def _make_account(db, store, label):
    account = Account(store=store, label=label, status="ok", profile_path=f"0-{label}")
    db.add(account)
    db.commit()
    return account.id


async def _first_run() -> bool:
    db = SessionLocal()
    account_id = _make_account(db, "prime", "prime-sim")
    # "Old Game" was claimed on an earlier run; only "New Game" is pending.
    row = Offer(store="prime", external_id="amzn1.offer.aaaa1111", title="Old Game")
    db.add(row)
    db.commit()
    db.add(
        Claim(
            account_id=account_id, offer_id=row.id, store="prime",
            title="Old Game", outcome="claimed",
        )
    )
    db.commit()

    adapter = FakeSessionAdapter(_offers())
    fake_manager = FakeManager()
    runner.manager = fake_manager
    runner.get_adapter = lambda store: {"prime": adapter}[store]
    run_id = await runner.run_account(account_id, trigger="manual")

    run = db.query(Run).filter(Run.id == run_id).first()
    db.refresh(run)
    offers = {o.external_id for o in db.query(Offer).filter(Offer.store == "prime")}
    claims = [
        (c.title, c.outcome)
        for c in db.query(Claim).filter(Claim.account_id == account_id)
    ]
    ok = True
    print("first run:", run.status, "| calls:", adapter.calls)
    if fake_manager.opens != 1:
        print("FAIL: expected exactly one browser session, got", fake_manager.opens); ok = False
    if adapter.calls[:2] != ["list_free_offers", "health"] or "discover_in_session" not in adapter.calls:
        print("FAIL: health must run before in-session discovery"); ok = False
    elif adapter.calls.index("health") > adapter.calls.index("discover_in_session"):
        print("FAIL: discover_in_session ran before the health check"); ok = False
    if "claim:Old Game" in adapter.calls:
        print("FAIL: an already-claimed offer was re-attempted"); ok = False
    if "claim:New Game" not in adapter.calls:
        print("FAIL: the new offer was never claimed"); ok = False
    if "amzn1.offer.bbbb2222" not in offers:
        print("FAIL: the in-session offer was not persisted as an Offer row"); ok = False
    if ("New Game", "claimed") not in claims:
        print("FAIL: no claimed ledger row for New Game:", claims); ok = False
    if run.status != "ok" or run.claimed != 1 or run.already_owned != 1 or run.offers_seen != 2:
        print("FAIL: run bookkeeping wrong:", run.status, run.claimed,
              run.already_owned, run.offers_seen); ok = False
    db.close()
    return ok, account_id, adapter


async def _second_run(account_id) -> bool:
    """Everything claimed: the session still opens, nothing is attempted."""
    adapter = FakeSessionAdapter(_offers())
    fake_manager = FakeManager()
    runner.manager = fake_manager
    runner.get_adapter = lambda store: {"prime": adapter}[store]
    run_id = await runner.run_account(account_id, trigger="manual")
    db = SessionLocal()
    run = db.query(Run).filter(Run.id == run_id).first()
    ok = True
    print("second run:", run.status, "| calls:", adapter.calls)
    if fake_manager.opens != 1:
        print("FAIL: session discovery must still open the session"); ok = False
    if any(c.startswith("claim:") for c in adapter.calls):
        print("FAIL: something was re-claimed"); ok = False
    if run.status != "ok" or run.already_owned != 2:
        print("FAIL: run bookkeeping wrong:", run.status, run.already_owned); ok = False
    db.close()
    return ok


async def _public_store_stays_cheap() -> bool:
    """A sessionless store with nothing free never opens a browser."""
    db = SessionLocal()
    account_id = _make_account(db, "pubstore", "pub-sim")
    db.close()
    adapter = FakePublicAdapter()
    fake_manager = FakeManager()
    runner.manager = fake_manager
    runner.get_adapter = lambda store: {"pubstore": adapter}[store]
    run_id = await runner.run_account(account_id, trigger="manual")
    db = SessionLocal()
    run = db.query(Run).filter(Run.id == run_id).first()
    ok = True
    print("public store run:", run.status, "| sessions opened:", fake_manager.opens)
    if fake_manager.opens != 0:
        print("FAIL: a browser was opened with nothing to claim"); ok = False
    if run.status != "ok":
        print("FAIL: expected ok, got", run.status); ok = False
    db.close()
    return ok


async def _unhealthy_stops_before_discovery() -> bool:
    """A signed-out session must never read as 'nothing free this week'."""
    db = SessionLocal()
    account_id = _make_account(db, "prime", "prime-sick")
    db.close()
    adapter = FakeSessionAdapter(_offers(), healthy=False)
    fake_manager = FakeManager()
    runner.manager = fake_manager
    runner.get_adapter = lambda store: {"prime": adapter}[store]
    run_id = await runner.run_account(account_id, trigger="manual")
    db = SessionLocal()
    run = db.query(Run).filter(Run.id == run_id).first()
    account = db.query(Account).filter(Account.id == account_id).first()
    ok = True
    print("unhealthy run:", run.status, "| calls:", adapter.calls)
    if "discover_in_session" in adapter.calls:
        print("FAIL: discovery ran on a signed-out session"); ok = False
    if run.status != "attention" or account.status != "needs_attention":
        print("FAIL: a bad session must file the account:", run.status, account.status); ok = False
    db.close()
    return ok


# ── Fakes for the Prime claim loop ──────────────────────────────────────────
#
# Same shape as checkout_sim: `_first_visible` and `_goto` are pointed at an
# in-memory state machine, keyed by selector-list identity.

_NAMES = {
    id(prime.CHALLENGE): "CHALLENGE",
    id(prime.SIGNED_OUT): "SIGNEDOUT",
    id(prime.SIGNED_IN): "SIGNEDIN",
    id(prime.CLAIMED): "CLAIMED",
    id(prime.CLAIM): "CLAIM",
    id(prime.LINK_WALL): "LINKWALL",
}


class FakeLoc:
    def __init__(self, name, state):
        self.name = name
        self.state = state

    async def inner_text(self):
        return self.name

    async def click(self, timeout=0):
        self.state["clicks"].append(self.name)
        # A direct claim confirms; a key claim reveals its code.
        self.state["stage"] = "key" if self.state["is_key"] else "claimed"


class FakeCodeLoc:
    """The revealed code's input, for `_read_key`'s direct locator path."""

    def __init__(self, state, sel):
        self.state = state
        self.sel = sel

    @property
    def first(self):
        return self

    async def is_visible(self):
        return self.state["stage"] == "key" and self.sel == prime.KEY_CODE[0]

    async def input_value(self):
        return "ABC12-DEF34-GH567"


class FakeClaimPage:
    url = "https://luna.amazon.com/claims/home"

    def __init__(self, state):
        self.state = state

    def locator(self, sel):
        return FakeCodeLoc(self.state, _bare(sel))

    async def wait_for_timeout(self, ms):
        pass

    async def inner_text(self, sel):
        return self.state.get("body", "")


def _install_claim_stubs(state):
    async def fake_first_visible(page, selectors, timeout_ms=0):
        name = _NAMES.get(id(selectors))
        if name == "CLAIM":
            return FakeLoc("CLAIM", state) if state["stage"] == "start" else None
        if name == "CLAIMED":
            return FakeLoc("CLAIMED", state) if state["stage"] == "claimed" else None
        if name == "LINKWALL":
            return FakeLoc("LINKWALL", state) if state.get("link_wall") else None
        return None  # no challenge, no sign-out in these branches

    prime._first_visible = fake_first_visible

    async def _nogoto(page, url):
        pass

    prime._goto = _nogoto


def _claim_adapter():
    adapter = prime.PrimeAdapter()

    async def _noshot(page, offer, tag):
        return f"{tag}.png"

    adapter._shot = _noshot
    return adapter


def _claim_offer():
    return FreeOffer(
        external_id="amzn1.offer.cccc3333",
        title="Sim Game",
        url="https://gaming.amazon.com/sim-game",
        extra={"account_id": 1},
    )


async def _direct_claim() -> bool:
    state = {"stage": "start", "clicks": [], "is_key": False, "body": ""}
    _install_claim_stubs(state)
    result = await _claim_adapter().claim(FakeClaimPage(state), _claim_offer())
    ok = True
    print("direct claim:", result.outcome, "| clicks:", state["clicks"])
    if result.outcome != "claimed" or result.key_code is not None:
        print("FAIL: expected a plain claimed result"); ok = False
    if state["clicks"] != ["CLAIM"]:
        print("FAIL: the claim button must be clicked exactly once, got",
              state["clicks"]); ok = False
    return ok


async def _key_claim() -> bool:
    state = {
        "stage": "start", "clicks": [], "is_key": True,
        "body": "Success! Redeem your code on GOG.com before the offer ends.",
    }
    _install_claim_stubs(state)
    result = await _claim_adapter().claim(FakeClaimPage(state), _claim_offer())
    ok = True
    print("key claim:", result.outcome, "| key:", result.key_code, "| store:",
          result.key_store, "| clicks:", state["clicks"])
    if result.outcome != "claimed" or result.key_code != "ABC12-DEF34-GH567":
        print("FAIL: the revealed code did not come back on the result"); ok = False
    if result.key_store != "gog":
        print("FAIL: the key's store was not read off the page"); ok = False
    if state["clicks"] != ["CLAIM"]:
        print("FAIL: expected exactly one click, got", state["clicks"]); ok = False
    return ok


async def _link_wall() -> bool:
    state = {
        "stage": "start", "clicks": [], "is_key": False, "link_wall": True,
        "body": "Link your Epic Games account to claim this game.",
    }
    _install_claim_stubs(state)
    try:
        result = await _claim_adapter().claim(FakeClaimPage(state), _claim_offer())
    except NeedsAttention as exc:
        ok = True
        print("link wall: NeedsAttention |", exc.reason[:80])
        if "epic games" not in exc.reason.lower():
            print("FAIL: the sentence does not name the missing link"); ok = False
        if state["clicks"]:
            print("FAIL: nothing may be clicked behind a link wall, got",
                  state["clicks"]); ok = False
        return ok
    print("FAIL: a link wall must raise NeedsAttention, got", result.outcome)
    return False


async def main() -> int:
    ok, account_id, _ = await _first_run()
    ok = await _second_run(account_id) and ok
    ok = await _public_store_stays_cheap() and ok
    ok = await _unhealthy_stops_before_discovery() and ok
    ok = await _direct_claim() and ok
    ok = await _key_claim() and ok
    ok = await _link_wall() and ok
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
