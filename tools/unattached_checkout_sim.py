"""Pin Epic's checkout-with-nothing-attached, without a browser or a store.

Measured 24 Nov 2026 in the container (see `backend/app/barecdp.py`): with
Playwright attached Epic's checkout raises an interactive captcha; over a bare
DevTools socket the same checkout completes silently. So Epic's order is
placed by `EpicAdapter.claim_unattached`, after the run's Playwright session
has closed. Two halves are pinned here.

The adapter, against a scripted tab:

  1. *The happy path.* The order button is pressed exactly once even while it
     lingers behind the consent dialog, the consent is accepted, and the
     verdict is the store's entitlement answer - `claimed`.
  2. *Already owned.* The checkout page offers "Add to library" for a game the
     account has (measured), so the store is asked first: nothing is pressed.
  3. *A captcha after all* raises `CheckoutBlocked` naming the offer, and
     nothing is pressed after it appears.
  4. *Ordered, never confirmed* is `NeedsAttention`, never a claimed row.
  5. *A hidden hCaptcha frame* - the check that passes by itself loads them -
     is not a captcha.

The runner, against a real SQLite database and fakes:

  6. Health and ownership happen in the session; `claim` (the attached path)
     is never called; `claim_unattached` runs only after the session closed,
     in a bare session; the ledger gets its `claimed` row.
  7. Without real Chrome the run falls back to the attached `claim`.
  8. A free base game is ordered before its add-on, and an add-on whose base
     game did not land is recorded `not_eligible` and not attempted.

    python tools/unattached_checkout_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="trove-unattached-sim-")

from app import runner  # noqa: E402
import app.adapters.epic as epic  # noqa: E402
from app.adapters.base import BaseAdapter, BaseGame, ClaimResult, FreeOffer  # noqa: E402
from app.browser import CheckoutBlocked, NeedsAttention  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Claim, Run  # noqa: E402

Base.metadata.create_all(engine)

# The real pacing is seconds; the sim has nothing to wait for.
epic.BARE_READ_EVERY_S = 0.0
epic.BARE_BEFORE_CLICK_S = 0.0
epic.UNATTACHED_CONFIRM_S = 0.05
runner.PAUSE_BETWEEN_CLAIMS_S = 0.0


# ── The adapter half ────────────────────────────────────────────────────────

ORDER = {"text": "Add to library", "disabled": False, "at": [640, 600], "dialog": False}
CANCEL = {"text": "Cancel", "disabled": False, "at": [560, 500], "dialog": True}
ACCEPT = {"text": "I accept", "disabled": False, "at": [720, 500], "dialog": True}


def _state(buttons, captcha=False, text="This is free. Add it to your library."):
    return {"url": "https://store.epicgames.com/purchase?offers=1-ns-id", "title": "Checkout",
            "buttons": buttons, "captcha": captcha, "text": text}


class ScriptedTab:
    """A checkout page that moves on when it is clicked, like Epic's."""

    def __init__(self, entitlement, captcha_after_order=False, finishes=True):
        self.clicks: list[tuple] = []
        self.entitlement = list(entitlement)  # answers, the last one repeating
        self.captcha_after_order = captcha_after_order
        self.finishes = finishes
        self.phase = "start"
        self.reads_since_accept = 0
        self.shots: list[str] = []

    async def navigate(self, url):
        self.navigated = url

    async def click(self, x, y):
        self.clicks.append((x, y))
        if (x, y) == tuple(ORDER["at"]) and self.phase == "start":
            self.phase = "captcha" if self.captcha_after_order else "consent"
        elif (x, y) == tuple(ACCEPT["at"]) and self.phase == "consent":
            self.phase = "processing"

    async def evaluate(self, expression):
        if "offer-entitlements" in expression:
            return self.entitlement.pop(0) if len(self.entitlement) > 1 else self.entitlement[0]
        if self.phase == "start":
            return _state([ORDER])
        if self.phase == "captcha":
            return _state([ORDER], captcha=True)
        if self.phase == "consent":
            # The order button is still there behind the dialog - the trap.
            return _state([ORDER, CANCEL, ACCEPT])
        self.reads_since_accept += 1
        if self.reads_since_accept <= 2 or not self.finishes:
            return _state([ORDER])  # lingering while the order is processed
        return _state([], text="")

    async def screenshot(self, name):
        self.shots.append(name)
        return name


def _offer():
    return FreeOffer(external_id="ns:id", title="Sim Game",
                     extra={"namespace": "ns", "offer_id": "id", "account_id": 7})


async def _adapter_half() -> bool:
    ok = True
    adapter = epic.EpicAdapter()

    tab = ScriptedTab(entitlement=["AVAILABLE", "OWNED"])
    result = await adapter._claim_bare(tab, _offer(), "ns", "id")
    orders = tab.clicks.count(tuple(ORDER["at"]))
    print("happy path:", result.outcome, "| clicks:", tab.clicks)
    if result.outcome != "claimed":
        print("FAIL: expected claimed"); ok = False
    if orders != 1:
        print("FAIL: the order button was pressed", orders, "times"); ok = False
    if tuple(ACCEPT["at"]) not in tab.clicks or tuple(CANCEL["at"]) in tab.clicks:
        print("FAIL: the consent was not accepted, or Cancel was pressed"); ok = False

    tab = ScriptedTab(entitlement=["OWNED"])
    result = await adapter._claim_bare(tab, _offer(), "ns", "id")
    print("already owned:", result.outcome, "| clicks:", tab.clicks)
    if result.outcome != "already_owned" or tab.clicks:
        print("FAIL: an owned game must be recognised before anything is pressed"); ok = False

    tab = ScriptedTab(entitlement=["AVAILABLE"], captcha_after_order=True)
    try:
        await adapter._claim_bare(tab, _offer(), "ns", "id")
        print("FAIL: a captcha did not stop the checkout"); ok = False
    except CheckoutBlocked as exc:
        print("captcha:", type(exc).__name__, "| offer:", exc.offer_id, "| clicks:", tab.clicks)
        if exc.offer_id != "ns:id" or len(tab.clicks) != 1 or not tab.shots:
            print("FAIL: wrong offer, a click after the captcha, or no screenshot"); ok = False

    tab = ScriptedTab(entitlement=["AVAILABLE"])
    try:
        result = await adapter._claim_bare(tab, _offer(), "ns", "id")
        print("FAIL: an unconfirmed order was reported as", result.outcome); ok = False
    except CheckoutBlocked:
        print("FAIL: an unconfirmed order is not a captcha"); ok = False
    except NeedsAttention as exc:
        print("unconfirmed:", exc.reason[:60])

    # The frame test itself, on the page script's own rule: a hidden or tiny
    # hCaptcha frame is the silent check, not a puzzle.
    if "r.width > 200 && r.height > 200" not in epic._BARE_STATE_JS:
        print("FAIL: the captcha test no longer requires a visible, puzzle-sized frame"); ok = False
    return ok


# ── The runner half ─────────────────────────────────────────────────────────


class FakeManager:
    def __init__(self):
        self.events: list[str] = []

    @asynccontextmanager
    async def session(self, account_id, profile_path, holder, wait_s=0.0, warm_url=None):
        self.events.append("session:open")
        try:
            yield object()
        finally:
            self.events.append("session:closed")

    @asynccontextmanager
    async def bare_session(self, account_id, profile_path, holder, wait_s=5.0):
        self.events.append("bare:open")
        try:
            yield 4242
        finally:
            self.events.append("bare:closed")


class FakePage:
    pass


async def _fake_first_page(context):
    return FakePage()


runner.first_page = _fake_first_page


class FakeEpic(BaseAdapter):
    store = "epic"
    display_name = "Epic Games Store"
    login_url = "https://example.com"
    unattached_checkout = True

    def __init__(self, manager, offers, base=None, base_outcome="claimed"):
        self.manager = manager
        self.offers = offers
        self.base = base
        self.base_outcome = base_outcome

    async def list_free_offers(self):
        return list(self.offers)

    async def health(self, page):
        self.manager.events.append("health")
        return True, "Signed in."

    async def is_owned(self, page, offer):
        self.manager.events.append(f"is_owned:{offer.title}")
        return False

    async def inspect_base_game(self, page, offer):
        return self.base

    async def claim(self, page, offer, waiter=None):
        self.manager.events.append(f"claim:{offer.title}")
        return ClaimResult(outcome="claimed", detail="Added to your library.")

    async def claim_unattached(self, port, offer):
        self.manager.events.append(f"unattached:{offer.title}:{port}")
        if self.base is not None and offer is self.base.offer:
            return ClaimResult(outcome=self.base_outcome, detail="x")
        return ClaimResult(outcome="claimed", detail="Added to your library.")


def _account(db, label):
    account = Account(store="epic", label=label, status="ok", profile_path=f"0-{label}")
    db.add(account)
    db.commit()
    return account.id


async def _run(label, adapter_factory, real_chrome=True):
    db = SessionLocal()
    account_id = _account(db, label)
    manager = FakeManager()
    adapter = adapter_factory(manager)
    runner.manager = manager
    runner.get_adapter = lambda store: adapter
    runner.can_drive_unattached = lambda: real_chrome
    run_id = await runner.run_account(account_id, trigger="manual")
    run = db.query(Run).filter(Run.id == run_id).first()
    claims = [(c.title, c.outcome, c.reason_code) for c in
              db.query(Claim).filter(Claim.account_id == account_id).order_by(Claim.id)]
    db.close()
    return manager.events, run, claims


async def _runner_half() -> bool:
    ok = True

    game = lambda eid, title, kind="game": FreeOffer(external_id=eid, title=title, kind=kind)  # noqa: E731

    events, run, claims = await _run("plain", lambda m: FakeEpic(m, [game("a:1", "Week Game")]))
    print("runner:", run.status, "|", events, "|", claims)
    if any(e.startswith("claim:") for e in events):
        print("FAIL: the attached claim was used"); ok = False
    want = ["session:open", "health", "is_owned:Week Game", "session:closed",
            "bare:open", "unattached:Week Game:4242", "bare:closed"]
    if events != want:
        print("FAIL: expected", want); ok = False
    if claims != [("Week Game", "claimed", None)] or run.status != "ok" or run.claimed != 1:
        print("FAIL: ledger or run bookkeeping wrong"); ok = False

    events, run, claims = await _run(
        "nochrome", lambda m: FakeEpic(m, [game("a:2", "Fallback Game")]), real_chrome=False)
    print("no real Chrome:", run.status, "|", events)
    if "claim:Fallback Game" not in events or any(e.startswith("bare:") for e in events):
        print("FAIL: without real Chrome the run must use the attached claim"); ok = False

    def with_base(outcome):
        def make(m):
            base_offer = FreeOffer(external_id="b:1", title="Base Game")
            base = BaseGame(title="Base Game", owned=False, free=True, offer=base_offer)
            return FakeEpic(m, [game(f"a:{outcome}", "Add-on", kind="dlc")], base=base, base_outcome=outcome)
        return make

    events, run, claims = await _run("dlc-ok", with_base("claimed"))
    print("add-on, base lands:", events[-4:], "|", claims)
    order = [e for e in events if e.startswith("unattached:")]
    if order != ["unattached:Base Game:4242", "unattached:Add-on:4242"]:
        print("FAIL: the base game must be ordered before its add-on"); ok = False
    if [c[:2] for c in claims] != [("Base Game", "claimed"), ("Add-on", "claimed")] or run.claimed != 2:
        print("FAIL: both should be claimed"); ok = False

    events, run, claims = await _run("dlc-fail", with_base("failed"))
    print("add-on, base fails:", [e for e in events if e.startswith("unattached:")], "|", claims)
    if "unattached:Add-on:4242" in events:
        print("FAIL: the add-on was attempted without its game"); ok = False
    if ("Add-on", "not_eligible", runner.REASON_BASE_FAILED) not in claims:
        print("FAIL: the add-on has no row saying why"); ok = False
    return ok


async def main() -> int:
    ok = await _adapter_half()
    ok = await _runner_half() and ok
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
