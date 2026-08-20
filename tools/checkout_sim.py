"""Drive the Epic checkout loop without a browser, and guard two things.

This runs in CI, in a second, with `_first_visible` and navigation stubbed.

1. **Selector priority.** A speed change once unioned the selectors and returned
   the first match in DOM order, which dropped the list priority and made the
   checkout click the wrong element. `_priority_check` asserts `_first_visible`
   still returns the first *visible* selector in list order.

2. **The checkout, two ways.**
   - *No captcha:* Add to library, then the "I accept" dialog, then confirmed -
     and the add-to-library button is clicked **exactly once** even though it
     lingers on the page behind the dialog while the order processes. A second
     click there races the first order and Epic answers "An error occurred".
   - *A captcha:* Epic raises a Talon captcha at "Add to library". Trove does
     not try to solve it in the driven browser - a human solve there is rejected
     at the order step (`epic.error.captcha.challenge.failed`, proven Aug 2026).
     It raises `CheckoutBlocked`, naming the offer, so the claim is finished in
     the un-driven window instead. This asserts it stops that way rather than
     hammering the button or reporting a false claim.

    python tools/checkout_sim.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import os

os.environ.setdefault("DATA_DIR", "./data")

import app.adapters.epic as epic  # noqa: E402
from app.browser import CheckoutBlocked  # noqa: E402
from app.adapters.base import FreeOffer  # noqa: E402
from playwright.async_api import TimeoutError as PWTimeout  # noqa: E402

# Map each selector-list to a name by identity.
_NAMES = {
    id(epic.ACCEPT): "ACCEPT",
    id(epic.COMPAT_CONTINUE): "COMPAT",
    id(epic.PLACE_ORDER): "ORDER",
    id(epic.CONFIRMED): "CONFIRMED",
    id(epic.OWNED): "OWNED",
    id(epic.NOT_ELIGIBLE): "NOTELIG",
    id(epic.ERROR): "ERROR",
    id(epic.CHALLENGE): "CHALLENGE",
    id(epic.SIGNED_OUT): "SIGNEDOUT",
}


async def _priority_check() -> bool:
    """_first_visible must return the *first visible selector in list order*."""

    class FakeLoc:
        def __init__(self, sel, visible):
            self.sel = sel
            self._v = visible

        @property
        def first(self):
            return self

        async def is_visible(self):
            return self._v

    class FakePage:
        def __init__(self, visible):
            self.visible = visible

        def locator(self, sel):
            return FakeLoc(sel, sel in self.visible)

    # The dialog-scoped "I accept" (first in ACCEPT) and a loose "Accept" (last)
    # are both visible; priority must return the first.
    page = FakePage({epic.ACCEPT[0], epic.ACCEPT[-1]})
    got = await epic._first_visible(page, epic.ACCEPT, 200)
    if got is None or got.sel != epic.ACCEPT[0]:
        print("FAIL: _first_visible lost selector priority (would click the wrong element)")
        print("  returned:", None if got is None else got.sel)
        return False
    # With only the loose one visible, it returns that.
    page = FakePage({epic.ACCEPT[-1]})
    got = await epic._first_visible(page, epic.ACCEPT, 200)
    if got is None or got.sel != epic.ACCEPT[-1]:
        print("FAIL: _first_visible did not fall through to a lower-priority match")
        return False
    print("priority OK")
    return True


class FakePage:
    async def wait_for_timeout(self, ms):
        pass

    def on(self, *a, **k):
        pass

    def remove_listener(self, *a, **k):
        pass


def _install_stubs(state, with_captcha: bool) -> None:
    """Point the adapter's page helpers at an in-memory checkout state machine."""

    class FakeLoc:
        def __init__(self, name):
            self.name = name

        async def inner_text(self):
            return self.name

        async def click(self, timeout=0):
            state["clicks"].append(self.name)
            if self.name == "ORDER":
                if with_captcha and state["stage"] == "order":
                    state["stage"] = "captcha"  # a Talon captcha intercepts
                    raise PWTimeout("intercepted")
                # No captcha: the order is placed and the "I accept" dialog opens
                # in front of the (still-present) add-to-library button.
                state["stage"] = "accept"
            elif self.name == "ACCEPT" and state["stage"] == "accept":
                state["stage"] = "processing"  # order placed; button lingers

    async def fake_first_visible(page, selectors, timeout_ms=0):
        name = _NAMES.get(id(selectors))
        if name == "CHALLENGE":
            return FakeLoc("CHALLENGE") if state["stage"] == "captcha" else None
        if name == "CONFIRMED":
            if state["stage"] == "processing":
                state["stage"] = "confirmed"  # goes through on the next look
                return None
            return FakeLoc("CONFIRMED") if state["stage"] == "confirmed" else None
        if name == "ACCEPT":
            return FakeLoc("ACCEPT") if state["stage"] == "accept" else None
        if name == "ORDER":
            return FakeLoc("ORDER") if state["stage"] == "order" else None
        # OWNED, NOT_ELIGIBLE, ERROR, COMPAT, SIGNED_OUT never show here.
        return None

    epic._first_visible = fake_first_visible

    async def _nogoto(page, url):
        pass

    epic._goto = _nogoto


def _adapter() -> epic.EpicAdapter:
    adapter = epic.EpicAdapter()

    async def _noshot(page, offer, tag):
        return f"{tag}.png"

    adapter._shot = _noshot
    return adapter


def _offer() -> FreeOffer:
    return FreeOffer(
        external_id="ns:oid",
        title="Caravan SandWitch",
        url="https://store.epicgames.com/p/caravan",
        extra={"namespace": "ns", "offer_id": "oid", "account_id": 1},
    )


async def _happy_path() -> bool:
    """No captcha: ORDER then ACCEPT then claimed, order clicked exactly once."""
    state = {"stage": "order", "clicks": []}
    _install_stubs(state, with_captcha=False)
    result = await _adapter().claim(FakePage(), _offer(), waiter=None)
    ok = True
    print("no-captcha outcome:", result.outcome, "| clicks:", state["clicks"])
    if result.outcome != "claimed":
        print("FAIL: expected claimed"); ok = False
    if state["clicks"] != ["ORDER", "ACCEPT"]:
        print("FAIL: expected exactly ORDER then ACCEPT - a second ORDER click",
              "means the loop re-submitted the order behind the dialog."); ok = False
    return ok


async def _captcha_path() -> bool:
    """A captcha: stops with CheckoutBlocked, names the offer, no false claim."""
    state = {"stage": "order", "clicks": []}
    _install_stubs(state, with_captcha=True)
    offer = _offer()
    try:
        result = await _adapter().claim(FakePage(), offer, waiter=None)
    except CheckoutBlocked as exc:
        print("captcha outcome: CheckoutBlocked | offer_id:", exc.offer_id,
              "| clicks:", state["clicks"])
        ok = True
        if exc.offer_id != offer.external_id:
            print("FAIL: CheckoutBlocked did not name the offer it blocked on"); ok = False
        if state["clicks"] != ["ORDER"]:
            print("FAIL: expected a single ORDER click before stopping, got",
                  state["clicks"]); ok = False
        return ok
    print("FAIL: a checkout captcha must raise CheckoutBlocked, got", result.outcome)
    return False


async def main() -> int:
    ok = await _priority_check()
    ok = await _happy_path() and ok
    ok = await _captcha_path() and ok
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
