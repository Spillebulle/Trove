"""Closing a sign-in window keeps the cookies the person just got.

Measured in the container (CLAUDE.md, "Closing a window must not lose the
sign-in"): SIGTERM drops every cookie Chrome set in its last ~30 s, SIGINT
keeps them. A sign-in followed straight away by Done is that window exactly.
This opens an un-driven window (`browser.launch_detached`) on a local page
that sets one session and one persistent cookie, closes it 5 s later the way
Trove does (`browser._quit_soon`), and reads the cookie database.

Needs real Chrome and a display; runs in container-smoke.

    python tools/close_keeps_cookies_sim.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trove-close-sim-"))

from app import browser  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Set-Cookie", "sid=signed-in; Path=/")
        self.send_header("Set-Cookie", "keep=1; Path=/; Max-Age=86400")
        self.end_headers()
        self.wfile.write(b"<h1>Signed in</h1>")

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()



async def _resolve() -> None:
    # What `open_local` does first: decide the sandbox, so the window gets
    # `--no-sandbox` where Chrome needs it (a CI runner) and dies otherwise.
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        await browser.resolve_channel(p, Path(tempfile.mkdtemp()))


asyncio.run(_resolve())
profile = Path(tempfile.mkdtemp()) / "profile"
profile.mkdir()
browser.quiet_profile(profile)
launched = browser.launch_detached(profile, f"http://127.0.0.1:{server.server_address[1]}/")
proc = launched[0] if isinstance(launched, tuple) else launched
time.sleep(5)
if proc.poll() is not None:
    print(f"FAILED: Chrome exited on its own (code {proc.returncode}) before the close")
    sys.exit(1)
browser._quit_soon(proc)
proc.wait(timeout=browser.QUIT_GRACE_S + 10)
time.sleep(2)

db = profile / "Default" / "Cookies"
names: list[str] = []
if db.exists():
    copy = Path(tempfile.mkdtemp()) / "Cookies"
    shutil.copy(db, copy)
    names = sorted(r[0] for r in sqlite3.connect(copy).execute("select name from cookies"))
print(f"cookies after the close: {names}")
server.shutdown()
ok = "sid" in names and "keep" in names
print("PASS" if ok else "FAILED: a cookie set 5 s before the close was lost")
sys.exit(0 if ok else 1)
