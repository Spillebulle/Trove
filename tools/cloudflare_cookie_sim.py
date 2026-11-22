"""Dropping a stale Cloudflare verdict, and only that.

Measured on the live install (22 Nov 2026): the Epic account's profile held a
`cf_clearance` that no longer matched the browser, and every run met "Just a
moment..."; the same profile with only Cloudflare's cookies deleted loaded the
store signed in. This pins:

  1. `browser.drop_cloudflare_cookies` removes Cloudflare's cookies for the
     site and its subdomains, and nothing else - not the store's own sign-in
     cookies, and not a Cloudflare cookie belonging to another site.
  2. Epic's `health` drops them and looks exactly once more when the check
     does not clear: a stale verdict gets one retry, a real challenge is
     still reported as `StoreChallenged`, never looped on.

Part 1 uses a real browser when one is installed (skipped with a note when
not, as in CI); part 2 is a fake page and needs nothing.

    backend/.venv/Scripts/python tools/cloudflare_cookie_sim.py
"""
from __future__ import annotations

import asyncio
import os
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


async def real_browser() -> None:
    print("Only Cloudflare's cookies for the site go:")
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context()
            jar = [
                ("cf_clearance", ".epicgames.com"),
                ("__cf_bm", ".store.epicgames.com"),
                ("cf_chl_rc_m", "store.epicgames.com"),
                ("EPIC_SSO", ".epicgames.com"),
                ("EPIC_BEARER_TOKEN", ".store.epicgames.com"),
                ("cf_clearance", ".gog.com"),
                ("cf_clearance", ".notepicgames.com"),
            ]
            await ctx.add_cookies([
                {"name": n, "value": "x", "domain": d, "path": "/"} for n, d in jar
            ])
            await browser.drop_cloudflare_cookies(ctx, "epicgames.com")
            left = sorted((c["name"], c["domain"]) for c in await ctx.cookies())
            await b.close()
    except Exception as exc:  # no browser installed, as in CI
        print(f"  skip  no browser to test against ({type(exc).__name__})")
        return
    names = {n for n, _ in left}
    check("the Epic sign-in cookies stay", {"EPIC_SSO", "EPIC_BEARER_TOKEN"} <= names, str(left))
    check("Epic's Cloudflare cookies go, subdomains included",
          not any(n in ("cf_clearance", "__cf_bm", "cf_chl_rc_m") and "epicgames" in d
                  and "notepic" not in d for n, d in left), str(left))
    check("another site's clearance stays", ("cf_clearance", ".gog.com") in left, str(left))
    check("a look-alike domain is not matched", ("cf_clearance", ".notepicgames.com") in left, str(left))


class _FakeContext:
    def __init__(self) -> None:
        self.dropped = 0

    async def clear_cookies(self, **kwargs) -> None:
        self.dropped += 1


class _FakePage:
    def __init__(self, challenged_loads: int) -> None:
        self.context = _FakeContext()
        self.loads = 0
        self.challenged_loads = challenged_loads
        self.url = "https://store.epicgames.com/en-US/"

    async def title(self) -> str:
        return "Just a moment..."


async def health_retries_once() -> None:
    print("Epic's health retries once, then reports:")
    originals = (epic._goto, epic._first_visible, epic._wait_out_challenge)

    async def goto(page, url):
        page.loads += 1
        return None

    async def first_visible(page, selectors, timeout_ms=1500):
        challenged = page.loads <= page.challenged_loads
        if selectors is epic.CHALLENGE:
            return challenged or None
        if selectors is epic.SIGNED_IN:
            return (not challenged) or None
        return None

    async def wait_out(page, seconds=0):
        return False  # the check never clears on its own in this sim

    epic._goto, epic._first_visible, epic._wait_out_challenge = goto, first_visible, wait_out
    try:
        adapter = epic.EpicAdapter()
        stale = _FakePage(challenged_loads=1)
        healthy, _ = await adapter.health(stale)
        check("a stale verdict: cookies dropped once, store reloaded, signed in",
              healthy and stale.context.dropped == 1 and stale.loads == 2,
              f"healthy={healthy} dropped={stale.context.dropped} loads={stale.loads}")

        real = _FakePage(challenged_loads=99)
        try:
            await adapter.health(real)
            raised = False
        except browser.StoreChallenged:
            raised = True
        check("a real challenge: still StoreChallenged after one retry, no loop",
              raised and real.context.dropped == 1 and real.loads == 2,
              f"raised={raised} dropped={real.context.dropped} loads={real.loads}")
    finally:
        epic._goto, epic._first_visible, epic._wait_out_challenge = originals


async def main() -> int:
    await real_browser()
    await health_retries_once()
    print("PASS" if not failures else f"FAILED ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
