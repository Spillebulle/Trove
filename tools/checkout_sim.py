"""Drive the Epic checkout loop through a simulated captcha, without a browser.

A guard for the wiring that broke once and quietly: the runner creates a
`_CaptchaWaiter` for every run, but a re-indent left `_run_claims` calling
`adapter.claim(page, offer)` without `waiter=waiter`, so `claim` got `None`,
hit the "Trove never solves these" branch, and the run quit at the captcha
instead of pausing. This exercises the whole path - claim -> _drive_checkout ->
_click -> _handle_challenge -> waiter -> resume -> "I accept" -> claimed - with
`_first_visible` and navigation stubbed, so it runs in a second with no Chrome.

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
from app import runner  # noqa: E402
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

_VISIBLE = {
    "order": {"ORDER"},
    "captcha": {"ORDER", "CHALLENGE"},  # challenge covers the order button
    "accept": {"ACCEPT", "ORDER"},  # withdrawal dialog, order still behind it
    "processing": {"ORDER"},  # dialog gone, order button STILL there, no confirm
    "confirmed": {"CONFIRMED"},
}


async def _priority_check() -> bool:
    """_first_visible must return the *first visible selector in list order*.

    The regression that made the checkout click the wrong thing: a speed change
    unioned the selectors and returned the first match in DOM order, dropping
    the priority that keeps the dialog-scoped "I accept" ahead of a stray
    "Accept" elsewhere. This asserts priority with a fake page - no browser.
    """
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


async def main() -> int:
    ok_priority = await _priority_check()
    state = {"stage": "order", "challenge_seen": 0, "clicks": []}

    class FakeLoc:
        def __init__(self, name):
            self.name = name

        async def click(self, timeout=0):
            state["clicks"].append(self.name)
            if self.name == "ORDER" and state["stage"] == "order":
                state["stage"] = "captcha"  # a captcha pops and intercepts
                raise PWTimeout("intercepted")
            if self.name == "ACCEPT" and state["stage"] == "accept":
                state["stage"] = "processing"  # order placed; button lingers

    async def fake_first_visible(page, selectors, timeout_ms=0):
        name = _NAMES.get(id(selectors))
        if name == "CONFIRMED" and state["stage"] == "processing":
            state["stage"] = "confirmed"  # the order goes through on the next look
            return None
        if name == "CHALLENGE":
            if state["stage"] == "captcha":
                state["challenge_seen"] += 1
                if state["challenge_seen"] == 1:
                    return FakeLoc("CHALLENGE")  # _click sees it, hands to waiter
                state["stage"] = "accept"  # the person solved it
                return None
            return None
        return FakeLoc(name) if name in _VISIBLE[state["stage"]] else None

    class FakePage:
        async def wait_for_timeout(self, ms):
            pass

        def on(self, *a, **k):
            pass

        def remove_listener(self, *a, **k):
            pass

    epic._first_visible = fake_first_visible

    async def _nogoto(page, url):
        pass

    epic._goto = _nogoto
    runner.asyncio.sleep = lambda _s: asyncio.sleep(0)  # don't wait out the poll

    notified: list[tuple[str, str | None]] = []
    runner.notify.send_soon = lambda kind, note: notified.append((kind, note.image_path))

    adapter = epic.EpicAdapter()

    async def _noshot(page, offer, tag):
        return f"{tag}.png"

    adapter._shot = _noshot

    offer = FreeOffer(
        external_id="x",
        title="Caravan SandWitch",
        url=None,
        extra={"namespace": "ns", "offer_id": "oid", "account_id": 1},
    )
    waiter = runner._CaptchaWaiter(1, "spillebulle", "Epic Games Store")
    result = await adapter.claim(FakePage(), offer, waiter=waiter)

    ok = True
    print("outcome:", result.outcome, "| clicks:", state["clicks"])
    print("notified:", notified)
    if result.outcome != "claimed":
        print("FAIL: expected claimed"); ok = False
    if state["clicks"] != ["ORDER", "ACCEPT"]:
        print("FAIL: expected exactly ORDER then ACCEPT - a third ORDER click means the",
              "loop re-submitted the order behind the dialog (the 'error occurred' bug)."); ok = False
    if not any(k == "attention" and img and "captcha" in img for k, img in notified):
        print("FAIL: expected a captcha notification with the screenshot attached"); ok = False
    ok = ok and ok_priority
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
