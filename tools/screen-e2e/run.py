"""The screen view, end to end, on a development machine.

Runs Trove as if it were in a container (`IN_CONTAINER=true`, a `VNC_ADDRESS`,
a `DISPLAY`), puts the fake RFB server beside it, and drives the built
interface with a real browser: sign in, add an account, press "Sign in here",
watch the screen dialog open and draw, click and type into it, press "Done,
close the window", and check the sign-in window really closed. Then it opens
Settings and presses "Check the browser".

What it proves is the plumbing - bridge, noVNC, the dialogs, the endpoints -
not that a store lets anybody in. It prints the fake server's log, which is
where the pointer and key events show up, and leaves two screenshots beside
this file.

    cd <repo>; frontend: npm run build
    backend/.venv/Scripts/python tools/screen-e2e/run.py

Needs the frontend built (it copies `frontend/dist` into `backend/app/static`)
and real Chrome installed. Uses port 8123 and 5999. A note for the reader of
its log: Playwright's `keyboard.type` delivers non-ASCII through `insertText`,
which has no keydown, so an umlaut typed by it never reaches noVNC; that is
the tool, not the screen view.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PY = sys.executable
PORT = 8123
VNC_PORT = 5999
PASSWORD = "screen-e2e-password"

static = ROOT / "backend/app/static"
dist = ROOT / "frontend/dist"
if not (dist / "index.html").is_file():
    raise SystemExit("Build the frontend first: cd frontend; npm run build")
shutil.copytree(dist, static, dirs_exist_ok=True)

data_dir = HERE / ".data"
vnc_log = HERE / "fake_vnc.log"
api_log = HERE / "api.log"

vnc = subprocess.Popen([PY, str(HERE / "fake_vnc.py"), str(VNC_PORT)],
                       stdout=open(vnc_log, "w"), stderr=subprocess.STDOUT)
env = dict(
    os.environ,
    DATA_DIR=str(data_dir),
    IN_CONTAINER="true",
    VNC_ADDRESS=f"127.0.0.1:{VNC_PORT}",
    DISPLAY=os.environ.get("DISPLAY", ":0"),
    ADMIN_PASSWORD=PASSWORD,
    LOG_LEVEL="INFO",
)
api = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--port", str(PORT)],
    cwd=str(ROOT), env=env, stdout=open(api_log, "w"), stderr=subprocess.STDOUT,
)

try:
    import httpx

    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/api/health").status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        raise SystemExit("The API did not come up; see api.log")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.fill("input[autocomplete=username]", "admin")
        page.fill("input[type=password]", PASSWORD)
        with page.expect_response(lambda r: "/api/auth/login" in r.url):
            page.click("button[type=submit]")

        acct = page.evaluate(
            """async () => {
              const r = await fetch('/api/accounts', {method:'POST',
                headers:{'content-type':'application/json'},
                body: JSON.stringify({store:'epic', label:'screen e2e'})});
              return await r.json(); }"""
        )
        aid = acct["id"]
        can = page.evaluate(f"async () => (await fetch('/api/accounts/{aid}/can-sign-in-here')).json()")
        print("can-sign-in-here:", can)
        assert can.get("via") == "screen", can

        page.goto(f"http://127.0.0.1:{PORT}/accounts/{aid}")
        page.get_by_role("button", name="Sign in here").click()
        page.wait_for_selector("text=on Trove's screen", timeout=15000)
        canvas = page.wait_for_selector("canvas", timeout=15000)
        time.sleep(2)
        page.screenshot(path=str(HERE / "screen-view.png"))
        box = canvas.bounding_box()
        print("canvas:", box)
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.up()
        page.keyboard.type("a")
        page.keyboard.press("Enter")
        time.sleep(1)

        a = page.evaluate(f"async () => (await fetch('/api/accounts/{aid}')).json()")
        print("busy_with while open:", a.get("busy_with"))
        assert a.get("busy_with") == "a sign-in window", a
        page.get_by_role("button", name="Done, close the window").click()
        time.sleep(5)
        a = page.evaluate(f"async () => (await fetch('/api/accounts/{aid}')).json()")
        print("busy_with after close:", a.get("busy_with"), "| status:", a.get("status"))
        assert a.get("busy_with") != "a sign-in window", a

        page.goto(f"http://127.0.0.1:{PORT}/settings")
        page.get_by_role("button", name="Check the browser").click()
        page.wait_for_selector("text=Launch flags", timeout=90000)
        page.screenshot(path=str(HERE / "browser-check.png"), full_page=True)
        print("browser check rendered")
        browser.close()
finally:
    api.terminate()
    vnc.terminate()
    time.sleep(1)
    print("---- fake vnc log ----")
    print(vnc_log.read_text()[-2000:])
    shutil.rmtree(data_dir, ignore_errors=True)
