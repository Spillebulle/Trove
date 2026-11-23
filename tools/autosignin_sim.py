"""The automatic sign-in reads the form, types into the right fields, and stops.

Drives `autosignin.attempt` in real Chrome (a throwaway profile) against five
fake login pages served from 127.0.0.1, and checks what the server received:

  a. email first, then a password page with "Remember me" and a footer
     newsletter box: both details go in, the box is ticked, the newsletter
     is never typed into;
  b. a visible reCAPTCHA: nothing is typed or submitted;
  c. a refused password: submitted exactly once, then a pause;
  d. a code after the password: a TOTP code goes in when a secret exists,
     and with no secret it stops and pauses;
  e. no sign-in form, only a newsletter box: gives up, types nothing.

Needs real Chrome; no network beyond localhost.

    backend/.venv/Scripts/python tools/autosignin_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trove-autosignin-sim-"))

from app import autosignin, browser  # noqa: E402

autosignin.STEP_WAIT_S = 1.0
autosignin.ATTEMPT_S = 60.0

received: list[tuple[str, dict]] = []

FOOTER = """<footer><form action="/newsletter"><p>Get deals in your inbox</p>
<input type="email" name="newsletter_email" placeholder="Email"></form></footer>"""


def page(body: str, title: str = "Sign in") -> bytes:
    return f"<!doctype html><title>{title}</title><body>{body}</body>".encode()


PAGES = {
    "/a/login": lambda q: page(f"""<form action="/a/password"><label>Email <input type="email" name="email"></label>
        <button>Continue</button></form>{FOOTER}"""),
    "/a/password": lambda q: page(f"""<form action="/a/done"><input type="hidden" name="email" value="{q.get('email', [''])[0]}">
        <label>Password <input type="password" name="password"></label>
        <label><input type="checkbox" name="remember"> Remember me</label>
        <label><input type="checkbox" name="news"> Send me offers</label>
        <button>Sign in</button></form>{FOOTER}"""),
    "/b/login": lambda q: page("""<form action="/b/done"><input type="email" name="email"><input type="password" name="password">
        <div class="g-recaptcha" data-sitekey="x" style="width:300px;height:78px;background:#ccc"></div>
        <button>Sign in</button></form>"""),
    "/c/login": lambda q: page(("<div role=alert>Incorrect password. Try again.</div>" if "error" in q else "") + """
        <form action="/c/check"><input name="username" placeholder="Username"><input type="password" name="password">
        <button>Sign in</button></form>"""),
    "/d/login": lambda q: page("""<form action="/d/code"><input type="email" name="email"><input type="password" name="password">
        <button>Sign in</button></form>"""),
    "/d/code": lambda q: page("""<form action="/d/done"><label>Security code <input name="otp" autocomplete="one-time-code"></label>
        <button>Verify</button></form>"""),
    "/e/login": lambda q: page(f"<h1>Welcome</h1>{FOOTER}"),
    "/f/login": lambda q: page("""<nav><button id=opener>Sign in</button></nav><div id=modal hidden>
        <form action="/f/done"><input type="email" name="email"><input type="password" name="password">
        <button>Sign in</button></form></div>
        <script>const b = document.getElementById('opener'); b.onclick = () => { document.getElementById('modal').hidden = false; b.remove(); }</script>"""),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        received.append((url.path, query))
        if url.path == "/c/check":
            self.send_response(302)
            self.send_header("Location", "/c/login?error=1")
            self.end_headers()
            return
        body = PAGES.get(url.path, lambda q: page("<h1>Signed in</h1>", "Home"))(query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label, flush=True)
    if not ok:
        failures.append(label)


def hits(path: str) -> list[dict]:
    return [q for p, q in received if p == path]


async def run(name: str, totp=None, opener: str = "") -> autosignin.Outcome:
    received.clear()
    profile = Path(tempfile.mkdtemp()) / "profile"
    outcome = await autosignin.attempt(
        900, profile, f"{BASE}/{name}/login", "person@example.com", "s3cret-pass", totp=totp, store_name="Fake",
        opener=opener,
    )
    print(f"{name}: submitted={outcome.submitted} pause={outcome.pause} | {outcome.sentence}", flush=True)
    return outcome


async def main() -> None:
    o = await run("a")
    done = hits("/a/done")
    check("a: email went in on the first page", hits("/a/password") and hits("/a/password")[0].get("email") == ["person@example.com"])
    check("a: password went in", bool(done) and done[0].get("password") == ["s3cret-pass"])
    check("a: remember me ticked, offers box not", bool(done) and "remember" in done[0] and "news" not in done[0])
    check("a: newsletter never typed into", not hits("/newsletter"))
    check("a: reported submitted", o.submitted)

    o = await run("b")
    check("b: captcha stops it before submitting", not hits("/b/done") and not o.submitted and "captcha" in o.sentence)

    o = await run("c")
    check("c: refused password submitted exactly once", len(hits("/c/check")) == 1)
    check("c: and paused", not o.submitted and o.pause and "Incorrect" in o.sentence)

    o = await run("d", totp=lambda: "123456")
    check("d: TOTP code typed", bool(hits("/d/done")) and hits("/d/done")[0].get("otp") == ["123456"])
    o = await run("d")
    check("d: no secret -> stops and pauses", not hits("/d/done") and o.pause and not o.submitted)

    o = await run("e")
    check("e: no form -> gives up", not o.submitted and "could not find" in o.sentence)
    check("e: newsletter never typed into", not hits("/newsletter"))

    o = await run("f", opener=r"^\s*sign in\s*$")
    check("f: the store's Sign in button opens the pop-up form", bool(hits("/f/done")) and hits("/f/done")[0].get("password") == ["s3cret-pass"])
    o = await run("f")
    check("f: without an opener nothing is pressed", not hits("/f/done") and not o.submitted)
    await browser.manager.stop()


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
BASE = f"http://127.0.0.1:{server.server_address[1]}"
threading.Thread(target=server.serve_forever, daemon=True).start()
asyncio.run(main())
server.shutdown()
print("FAILED: " + "; ".join(failures) if failures else "PASS")
sys.exit(1 if failures else 0)
