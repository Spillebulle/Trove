"""Cloudflare on Epic: the warm session, and that health no longer drops cookies.

History, because this file's name is: 0.1.38 answered a stuck "Just a moment"
by deleting the profile's Cloudflare cookies and reloading. That got the store
front to load and broke the sign-in - the store's background session pickup
needs a clearance earned the normal way, so a signed-in account read as signed
out and was parked. 0.1.40 removed it and instead opens the store before
Playwright attaches (`browser._pass_check_unattached`), measured to pass.

What this pins, with no browser and no network:

  1. The challenge-title test recognises Cloudflare's interstitials and not a
     store page.
  2. `_free_local_port` hands back a usable port number - a fixed number,
     because `--remote-debugging-port=0` was measured never to pass.
  3. Epic declares its warm URL, and its health check, faced with a check that
     will not clear, reports `StoreChallenged` after one wait: no cookie drop,
     no reload, no loop.

    backend/.venv/Scripts/python tools/cloudflare_cookie_sim.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trove-cf-sim-"))

from app import browser  # noqa: E402
from app.adapters import epic  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + label + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def titles_and_port() -> None:
    print("The pieces of the warm session:")
    for title, want in (
        ("Just a moment...", True),
        ("One more step", True),
        ("Attention Required! | Cloudflare", True),
        ("Epic Games Store | Download & Play PC Games", False),
        ("My Wishlist", False),
        ("", False),
        (None, False),
    ):
        check(f"is_challenge_title({title!r}) is {want}", browser.is_challenge_title(title) is want)
    port = browser._free_local_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))  # it really was free
    check("a free local port is a real, fixed number", 1024 < port < 65536, str(port))
    check("Epic declares a warm URL, the store front", epic.EpicAdapter().warm_url == epic.EpicAdapter().login_url)
    check("nothing else is dropped any more", not hasattr(browser, "drop_cloudflare_cookies"))


class _FakeContext:
    async def clear_cookies(self, **kwargs) -> None:  # must never be called
        failures.append("health cleared cookies")


class _FakePage:
    def __init__(self) -> None:
        self.context = _FakeContext()
        self.loads = 0
        self.url = "https://store.epicgames.com/en-US/"

    async def title(self) -> str:
        return "Just a moment..."


async def health_reports_once() -> None:
    print("Epic's health, faced with a check that will not clear:")
    originals = (epic._goto, epic._first_visible, epic._wait_out_challenge)

    async def goto(page, url):
        page.loads += 1
        return None

    async def first_visible(page, selectors, timeout_ms=1500):
        return True if selectors is epic.CHALLENGE else None

    async def wait_out(page, seconds=0):
        return False

    epic._goto, epic._first_visible, epic._wait_out_challenge = goto, first_visible, wait_out
    try:
        page = _FakePage()
        try:
            await epic.EpicAdapter().health(page)
            raised = False
        except browser.StoreChallenged:
            raised = True
        check("StoreChallenged, one page load, no cookies touched",
              raised and page.loads == 1, f"raised={raised} loads={page.loads}")
    finally:
        epic._goto, epic._first_visible, epic._wait_out_challenge = originals


async def main() -> int:
    titles_and_port()
    await health_reports_once()
    print("PASS" if not failures else f"FAILED ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
