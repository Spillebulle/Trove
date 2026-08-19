"""Does a cookie written in one browser launch survive the next?

The sign-in loop was a cookie-store mismatch: the un-driven sign-in window
encrypted cookies with a keyring key, and the driven session-check - using
Playwright's mock keychain - could not read them, so every check reported the
account signed out. The fix gave both browsers the same deterministic store
(`--password-store=basic --use-mock-keychain`, now in `browser.CONTAINER_ARGS`).

This proves the property that had to hold: a cookie set with the launch args a
run uses is still there after closing and reopening the profile. It runs inside
the container in the smoke workflow, where there is no keyring - which is the
environment the fix has to work in. It cannot reproduce the *original* failure
there (no keyring means even the buggy "detect" falls back to basic), but it is
the regression guard that the persistent session does persist.

    python tools/cookie_persist.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from playwright.async_api import async_playwright  # noqa: E402

from app.browser import LAUNCH_ARGS, resolve_channel  # noqa: E402


async def main() -> int:
    profile = Path(tempfile.mkdtemp(prefix="cookie-persist-"))
    try:
        async with async_playwright() as pw:
            channel = await resolve_channel(pw, profile)

            async def launch():
                return await pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=True,
                    channel=channel,
                    args=LAUNCH_ARGS,
                    chromium_sandbox="--no-sandbox" not in LAUNCH_ARGS,
                )

            # First launch: land on a real origin and set a persistent cookie.
            ctx = await launch()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://example.com/", wait_until="domcontentloaded")
            await ctx.add_cookies(
                [
                    {
                        "name": "trove_persist_probe",
                        "value": "survived",
                        "url": "https://example.com/",
                        "expires": 4102444800,  # year 2100: a persistent cookie
                    }
                ]
            )
            await ctx.close()

            # Second launch of the same profile: is it still there?
            ctx = await launch()
            cookies = await ctx.cookies("https://example.com/")
            await ctx.close()

        probe = next((c for c in cookies if c["name"] == "trove_persist_probe"), None)
        if probe and probe["value"] == "survived":
            print("cookie persisted across launches:", probe["value"])
            print("store flags:", [a for a in LAUNCH_ARGS if "password-store" in a or "keychain" in a])
            return 0
        print("COOKIE DID NOT PERSIST. Cookies seen:", [c["name"] for c in cookies])
        return 1
    finally:
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
