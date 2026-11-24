"""Does Epic's checkout raise a captcha from *this* machine's browser?

A measurement, not part of the app. It opens real Chrome on a profile with a
DevTools port and nothing else attached (no Playwright - the warm session's
launch), drives one free checkout with trusted clicks, and reports whether
Talon put up an interactive hCaptcha. It never answers one.

    python tools/epic_checkout_probe.py --profile DIR fingerprint
    python tools/epic_checkout_probe.py --profile DIR status
    python tools/epic_checkout_probe.py --profile DIR signin        # a person signs in, then closes
    python tools/epic_checkout_probe.py --profile DIR offer SLUG    # p/<slug> -> namespace, offer id, owned?
    python tools/epic_checkout_probe.py --profile DIR checkout NAMESPACE OFFER_ID

`--software-gl` forces Chrome onto SwiftShader, which is the one-variable test
this exists for: the same account, address and browser with and without a real
GPU. The container has only a software renderer (llvmpipe), and whether that is
what makes Epic ask is the open question.

`offer` presses the product page's own "Get", and for a permanently free game
that is the whole claim (Epic's quick purchase). So `offer` and `checkout` both
really add the free item to the account's library when they go through.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/opt/google/chrome/chrome",
    "/usr/bin/google-chrome",
]


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("chrome")
    if found:
        return found
    raise SystemExit("Real Chrome was not found; set CHROME_PATH.")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def targets(port: int) -> list[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as response:
            return [t for t in json.load(response) if t.get("type") == "page"]
    except Exception:
        return []


def launch(profile: Path, software_gl: bool, attach: bool, url: str = "about:blank",
           extra: list[str] | None = None) -> tuple[subprocess.Popen, int]:
    port = free_port()
    args = [
        find_chrome(),
        f"--user-data-dir={profile.resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,800",
        "--window-position=0,0",
    ]
    if attach:
        args.append(f"--remote-debugging-port={port}")
    if os.environ.get("IN_CONTAINER"):
        args += ["--ignore-gpu-blocklist", "--enable-unsafe-webgpu", "--password-store=basic",
                 "--use-mock-keychain", "--disable-dev-shm-usage"]
        if os.environ.get("PROBE_NO_SANDBOX"):
            args.append("--no-sandbox")
    if os.name == "nt":
        args += ["--disable-backgrounding-occluded-windows",
                 "--disable-features=CalculateNativeWinOcclusion"]
    if software_gl:
        args += ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    args += extra or []
    args.append(url)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not attach:
        return proc, 0
    deadline = time.time() + 25
    while time.time() < deadline:
        if targets(port):
            return proc, port
        time.sleep(0.3)
    proc.terminate()
    raise SystemExit("Chrome did not open its DevTools port.")


INTERESTING = ("talon", "hcaptcha.com", "confirm-order", "order-preview", "/purchase/", "quick-purchase", "graphql")
BODY_OF = ("offer-entitlements", "getcaptcha", "confirm-order", "talon", "checksiteconfig", "order-preview", "quick-purchase")


class Cdp:
    """One page over the browser's socket. Page.navigate, Runtime.evaluate, Input.*.

    With `net`, it also attaches to the page's child frames and enables Network
    on each, to log what Talon and hCaptcha answered. That is a bigger footprint
    than a run has, so it is off unless asked for.
    """

    def __init__(self, ws, net: bool = False) -> None:
        self.ws = ws
        self.net = net
        self.next_id = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.session: str | None = None
        self.urls: dict[tuple, str] = {}
        self.reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                if "id" in message:
                    future = self.pending.pop(message["id"], None)
                    if future and not future.done():
                        future.set_result(message.get("result", {}))
                    continue
                asyncio.ensure_future(self._event(message))
        except Exception:
            pass

    async def _event(self, message: dict) -> None:
        method, params, session = message.get("method"), message.get("params", {}), message.get("sessionId")
        try:
            if method == "Target.attachedToTarget" and self.net:
                child = params["sessionId"]
                await self.raw("Network.enable", {}, child)
                await self.raw("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}, child)
            elif method == "Network.requestWillBeSent":
                request = params["request"]
                if request.get("postData") and any(word in request["url"] for word in ("/purchase/", "quick-purchase", "talon", "confirm-order", "order-preview")):
                    print(f"    post {request['url'][:110]} {request['postData'][:500]}")
                    print("      headers:", {k: (v[:12] + "...") for k, v in request.get("headers", {}).items() if "csrf" in k.lower() or "xsrf" in k.lower() or k.lower().startswith("x-")})
            elif method == "Network.responseReceived":
                url = params["response"]["url"]
                if any(word in url for word in INTERESTING) and params.get("type") in ("XHR", "Fetch", "Document", "Other"):
                    from urllib.parse import unquote
                    shown = unquote(url)[:420] if "graphql" in url else url[:150]
                    print(f"    net {params['response']['status']} {shown}")
                    self.urls[(session, params["requestId"])] = url
            elif method == "Network.loadingFinished":
                url = self.urls.pop((session, params["requestId"]), None)
                if url and any(word in url for word in BODY_OF):
                    body = await self.raw("Network.getResponseBody", {"requestId": params["requestId"]}, session)
                    text = body.get("body", "")
                    if body.get("base64Encoded"):
                        text = "<binary %d>" % len(text)
                    print(f"      body {url.split('?')[0][-60:]}: {text[:700]}")
        except Exception as exc:
            print("    (event error: %s)" % exc)

    async def raw(self, method: str, params: dict | None, session: str | None) -> dict:
        self.next_id += 1
        mine = self.next_id
        future = asyncio.get_event_loop().create_future()
        self.pending[mine] = future
        message = {"id": mine, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        await self.ws.send(json.dumps(message))
        return await asyncio.wait_for(future, 60)

    async def call(self, method: str, params: dict | None = None) -> dict:
        return await self.raw(method, params, self.session)

    async def evaluate(self, expression: str):
        result = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        return result.get("result", {}).get("value")

    async def click(self, x: float, y: float) -> None:
        base = {"x": x, "y": y, "pointerType": "mouse"}
        await self.call("Input.dispatchMouseEvent", {**base, "type": "mouseMoved", "button": "none", "buttons": 0})
        await asyncio.sleep(0.25)
        await self.call("Input.dispatchMouseEvent", {**base, "type": "mousePressed", "button": "left", "buttons": 1, "clickCount": 1})
        await asyncio.sleep(0.08)
        await self.call("Input.dispatchMouseEvent", {**base, "type": "mouseReleased", "button": "left", "buttons": 0, "clickCount": 1})

    async def screenshot(self, path: Path) -> None:
        shot = await self.call("Page.captureScreenshot", {"format": "png"})
        if shot.get("data"):
            path.write_bytes(base64.b64decode(shot["data"]))
            print(f"  screenshot: {path}")


FINGERPRINT_JS = """(async () => {
  const gl = document.createElement('canvas').getContext('webgl');
  const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  let gpu = null;
  try { const a = navigator.gpu && await navigator.gpu.requestAdapter();
        gpu = a ? {vendor: a.info && a.info.vendor, architecture: a.info && a.info.architecture} : null; } catch (e) { gpu = String(e); }
  let devices = null;
  try { devices = (await navigator.mediaDevices.enumerateDevices()).map(d => d.kind); } catch (e) { devices = String(e); }
  return {
    ua: navigator.userAgent, platform: navigator.platform, webdriver: navigator.webdriver,
    renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null, webgpu: gpu,
    cores: navigator.hardwareConcurrency, memory: navigator.deviceMemory,
    screen: [screen.width, screen.height, devicePixelRatio], media_devices: devices,
    voices: speechSynthesis.getVoices().length, visibility: document.visibilityState,
  };
})()"""

# What is on the checkout page right now. Reads only.
STATE_JS = r"""(() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const box = el => { const r = el.getBoundingClientRect(); return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2), Math.round(r.width), Math.round(r.height)]; };
  const buttons = [...document.querySelectorAll('button')].filter(vis)
    .map(b => ({text: (b.innerText || '').trim().slice(0, 40), disabled: b.disabled, box: box(b)}))
    .filter(b => b.text);
  const frames = [...document.querySelectorAll('iframe')].map(f => ({
    src: (f.src || '').slice(0, 90), title: f.title, visible: vis(f), box: box(f)}));
  const captcha = frames.filter(f => /hcaptcha|captcha|arkose/i.test(f.src + ' ' + f.title) && f.visible && f.box[2] > 200 && f.box[3] > 200);
  const text = (document.body.innerText || '').replace(/\s+/g, ' ');
  return {url: location.href, title: document.title, buttons, captcha, frames: frames.length,
          text: text.slice(0, 400),
          thanks: /thank(s| you) for (your order|buying|your purchase)|order (complete|confirmed)/i.test(text),
          error: /error occurred|check your network|unable to|not available|already own/i.exec(text)?.[0] || null};
})()"""


async def connect(port: int, net: bool = False) -> Cdp:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
        browser_ws = json.load(response)["webSocketDebuggerUrl"]
    ws = await websockets.connect(browser_ws, max_size=None)
    cdp = Cdp(ws, net)
    attached = await cdp.raw("Target.attachToTarget", {"targetId": targets(port)[0]["id"], "flatten": True}, None)
    cdp.session = attached["sessionId"]
    if net:
        await cdp.call("Network.enable")
        await cdp.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
    return cdp


async def goto(cdp: Cdp, url: str, settle: float = 6.0) -> None:
    await cdp.call("Page.navigate", {"url": url})
    await asyncio.sleep(settle)


async def wait_out_check(cdp: Cdp, seconds: float = 40.0) -> None:
    started = time.time()
    while time.time() - started < seconds:
        title = await cdp.evaluate("document.title") or ""
        if not any(word in title.lower() for word in ("just a moment", "one more step", "security check", "attention required")):
            return
        await asyncio.sleep(2)
    print("  still on a security check after %.0fs" % seconds)


async def cmd_fingerprint(cdp: Cdp, args) -> None:
    await goto(cdp, "https://store.epicgames.com/en-US/", 5)
    await wait_out_check(cdp)
    print(json.dumps(await cdp.evaluate(FINGERPRINT_JS), indent=2))


async def account(cdp: Cdp):
    await goto(cdp, "https://www.epicgames.com/id/api/account", 4)
    await wait_out_check(cdp)
    body = await cdp.evaluate("document.body.innerText") or ""
    try:
        data = json.loads(body)
    except ValueError:
        return {"raw": body[:200]}
    return {"signed_in": bool(data.get("id")), "country": data.get("country"),
            "error": data.get("errorCode"), "mfa": data.get("tfaEnabled")}


async def cmd_status(cdp: Cdp, args) -> None:
    print(json.dumps(await account(cdp)))


async def cmd_offer(cdp: Cdp, args) -> None:
    """Find a product page's purchase link the way a person would: press Get."""
    await goto(cdp, f"https://store.epicgames.com/en-US/p/{args.slug}", 8)
    await wait_out_check(cdp)
    state = await cdp.evaluate(STATE_JS)
    print("  page:", state["title"])
    print("  buttons:", [b["text"] for b in state["buttons"]][:14])
    ids = await cdp.evaluate(r"""(() => {
      const html = document.documentElement.innerHTML;
      const found = new Set();
      for (const m of html.matchAll(/offers=1-([0-9a-z]+)-([0-9a-f]{32})/g)) found.add(m[1] + ' ' + m[2]);
      return [...found];
    })()""")
    print("  purchase links in the markup:", ids)
    get = next((b for b in state["buttons"] if b["text"].lower() == "get" and not b["disabled"]), None)
    if not get:
        print("  no Get button (owned, paid, or signed out)")
        return
    await cdp.evaluate("[...document.querySelectorAll('button')].find(b => b.innerText.trim().toLowerCase() === 'get' && b.getBoundingClientRect().width > 2).scrollIntoView({block: 'center'})")
    await asyncio.sleep(1)
    state = await cdp.evaluate(STATE_JS)
    get = next(b for b in state["buttons"] if b["text"].lower() == "get")
    await cdp.click(get["box"][0], get["box"][1])
    for _ in range(12):
        await asyncio.sleep(1.5)
        found = await cdp.evaluate(r"""(() => {
          const urls = [location.href, ...[...document.querySelectorAll('iframe')].map(f => f.src)];
          for (const u of urls) { const m = /offers=1-([0-9a-z]+)-([0-9a-f]{32})/.exec(u || ''); if (m) return m[1] + ' ' + m[2]; }
          return null; })()""")
        if found:
            print("  OFFER:", found)
            return
    after = await cdp.evaluate(STATE_JS)
    print("  pressed Get, no purchase link appeared; buttons now:", [b["text"] for b in after["buttons"]][:14])
    print("  frames:", await cdp.evaluate("[...document.querySelectorAll('iframe')].map(f => [f.src.slice(0,160), f.title, f.getBoundingClientRect().width])"))
    print("  dialogs:", await cdp.evaluate("[...document.querySelectorAll('[role=dialog]')].map(d => d.innerText.slice(0,300))"))
    Path(args.out).mkdir(parents=True, exist_ok=True)
    await cdp.screenshot(Path(args.out) / f"offer-{args.slug[:30]}.png")


async def cmd_eval(cdp: Cdp, args) -> None:
    await goto(cdp, args.url, args.settle)
    await wait_out_check(cdp)
    print(json.dumps(await cdp.evaluate(args.js), indent=1))


async def cmd_checkout(cdp: Cdp, args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{'swgl' if args.software_gl else 'gpu'}-{int(time.time())}"
    who = await account(cdp)
    print("account:", json.dumps(who))
    if not who.get("signed_in"):
        print("RESULT: not signed in")
        return
    await goto(cdp, "https://store.epicgames.com/en-US/", 6)
    await wait_out_check(cdp)
    print("fingerprint:", json.dumps(await cdp.evaluate(FINGERPRINT_JS)))
    url = f"https://store.epicgames.com/purchase?offers=1-{args.namespace}-{args.offer_id}"
    await goto(cdp, url, 8)
    await wait_out_check(cdp)
    ordered = False
    accepted = 0
    started = time.time()
    last = None
    result = "timeout"
    while time.time() - started < args.seconds:
        state = await cdp.evaluate(STATE_JS)
        if not state:
            await asyncio.sleep(1.5)
            continue
        summary = (state["url"][:70], tuple(b["text"] for b in state["buttons"]), len(state["captcha"]), state["thanks"], state["error"])
        if summary != last:
            last = summary
            print(f"  +{time.time() - started:4.0f}s buttons={[b['text'] for b in state['buttons']]} "
                  f"captcha={state['captcha']} thanks={state['thanks']} error={state['error']}")
        if state["captcha"]:
            result = "CAPTCHA (interactive challenge shown%s)" % (" after the order click" if ordered else " before any click")
            break
        if state["thanks"] or (ordered and "/purchase" not in state["url"]):
            result = "CLAIMED with no captcha"
            break
        if ordered and state["error"]:
            result = f"error: {state['error']}"
            break

        def named(*labels):
            for button in state["buttons"]:
                if not button["disabled"] and any(button["text"].lower().startswith(l) for l in labels):
                    return button
            return None

        consent = named("i accept", "i agree", "accept")
        if consent and accepted < 3:
            print(f"  clicking consent {consent['text']!r}")
            await cdp.click(consent["box"][0], consent["box"][1])
            accepted += 1
            await asyncio.sleep(3)
            continue
        order = named("add to library", "place order")
        if order and not ordered:
            print(f"  clicking {order['text']!r} once")
            await cdp.click(order["box"][0], order["box"][1])
            ordered = True
            await asyncio.sleep(3)
            continue
        await asyncio.sleep(1.5)
    final = await cdp.evaluate(STATE_JS)
    print("  final text:", (final or {}).get("text"))
    await cdp.screenshot(out / f"probe-{tag}.png")
    print("RESULT:", result)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--software-gl", action="store_true")
    parser.add_argument("--net", action="store_true", help="log Talon / hCaptcha / order responses")
    parser.add_argument("--out", default="probe-out")
    parser.add_argument("--chrome-arg", action="append", default=[])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fingerprint")
    sub.add_parser("status")
    sub.add_parser("signin")
    offer = sub.add_parser("offer")
    offer.add_argument("slug")
    ev = sub.add_parser("eval")
    ev.add_argument("url")
    ev.add_argument("js")
    ev.add_argument("--settle", type=float, default=7)
    checkout = sub.add_parser("checkout")
    checkout.add_argument("namespace")
    checkout.add_argument("offer_id")
    checkout.add_argument("--seconds", type=float, default=90)
    args = parser.parse_args()
    profile = Path(args.profile)
    profile.mkdir(parents=True, exist_ok=True)

    if args.cmd == "signin":
        # Nothing attached at all: no port. A person signs in and closes it.
        proc, _ = launch(profile, False, attach=False, url="https://store.epicgames.com/login?state=%2Fen-US%2F")
        print("A plain Chrome window is open on Epic's sign-in. Sign in, tick Remember me, then close the window.")
        proc.wait()
        return

    proc, port = launch(profile, args.software_gl, attach=True, extra=args.chrome_arg)
    try:
        cdp = await connect(port, args.net)
        await {"fingerprint": cmd_fingerprint, "status": cmd_status, "offer": cmd_offer, "eval": cmd_eval,
               "checkout": cmd_checkout}[args.cmd](cdp, args)
    finally:
        # Chrome's own close, so cookies are flushed (SIGTERM loses them).
        try:
            await cdp.raw("Browser.close", {}, None)
        except Exception:
            pass
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.terminate()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
