"""What the browser Trove drives actually looks like, measured rather than assumed.

Every hard lesson in CLAUDE.md about challenges was found by reading a value
out of the browser - the codec support, the brand list, the WebGPU adapter -
and every one of them had been invisible until somebody printed it. This module
is that print statement made permanent: it opens a throwaway profile with the
exact launch the runs use, asks the page a fixed list of questions, and returns
the answers as one JSON object.

It is deliberately not a score. "Would Cloudflare let this through" has no
answer short of asking Cloudflare, and a number here would invite tuning to the
number. What it gives is the contradictions to look for: a user-agent that says
desktop Chrome beside a WebGL that does not exist, a brand list without "Google
Chrome", codecs that come back empty. Each of those has cost a session before.

Runs from the interface (`GET /api/diagnostics/browser`) and from a shell:

    python -m app.diagnose            # pretty JSON
    docker compose exec trove python -m app.diagnose

The shell form is what a container smoke test prints, so a broken image says
*how* it is broken rather than that a claim failed later for no reason.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

from playwright.async_api import async_playwright

from . import __version__
from .browser import LAUNCH_ARGS, VIEWPORT, purge_profile, resolve_channel
from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Asked of the page, in the page. Everything is wrapped so one missing API
# reports as such instead of taking the whole probe down with it.
PROBE_JS = """
async () => {
  const out = {};
  const safe = (name, fn) => { try { out[name] = fn(); } catch (e) { out[name] = 'error: ' + e.message; } };

  safe('user_agent', () => navigator.userAgent);
  safe('brands', () => (navigator.userAgentData?.brands ?? []).map(b => `${b.brand} ${b.version}`));
  safe('platform', () => navigator.userAgentData?.platform ?? navigator.platform);
  safe('mobile', () => navigator.userAgentData?.mobile ?? null);
  safe('webdriver', () => navigator.webdriver);
  safe('languages', () => [...navigator.languages]);
  safe('hardware_concurrency', () => navigator.hardwareConcurrency);
  safe('device_memory', () => navigator.deviceMemory ?? null);
  safe('screen', () => ({ width: screen.width, height: screen.height, avail_width: screen.availWidth, avail_height: screen.availHeight, colour_depth: screen.colorDepth, dpr: devicePixelRatio }));
  safe('viewport', () => ({ width: innerWidth, height: innerHeight, outer_width: outerWidth, outer_height: outerHeight }));
  safe('has_focus', () => document.hasFocus());
  safe('timezone', () => Intl.DateTimeFormat().resolvedOptions().timeZone);
  safe('plugins', () => navigator.plugins.length);
  safe('touch_points', () => navigator.maxTouchPoints);

  // The codec probe that gave the bundled-Chromium problem away. Real Chrome
  // answers "probably" to every one of these; the bundle answers "".
  safe('codecs', () => {
    const v = document.createElement('video');
    return {
      h264: v.canPlayType('video/mp4; codecs="avc1.42E01E"'),
      hevc: v.canPlayType('video/mp4; codecs="hev1.1.6.L93.B0"'),
      aac: v.canPlayType('audio/mp4; codecs="mp4a.40.2"'),
      vp9: v.canPlayType('video/webm; codecs="vp9"'),
      av1: v.canPlayType('video/mp4; codecs="av01.0.05M.08"'),
    };
  });

  // WebGL. A headed Chrome under Xvfb with no GPU flags reports nothing here,
  // and "desktop Chrome with no WebGL" is a contradiction a challenge reads.
  safe('webgl', () => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return null;
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      version: gl.getParameter(gl.VERSION),
      vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      webgl2: !!canvas.getContext('webgl2'),
    };
  });

  // WebGPU. "No available adapters." is the line a challenge logged.
  try {
    if (!navigator.gpu) {
      out.webgpu = null;
    } else {
      const adapter = await navigator.gpu.requestAdapter();
      if (!adapter) {
        out.webgpu = { adapter: false };
      } else {
        let info = null;
        try { info = adapter.info ?? (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null); } catch (e) {}
        out.webgpu = {
          adapter: true,
          vendor: info?.vendor ?? null,
          architecture: info?.architecture ?? null,
          description: info?.description ?? null,
          fallback: adapter.isFallbackAdapter ?? null,
        };
      }
    }
  } catch (e) { out.webgpu = 'error: ' + e.message; }

  // The classic "is the DevTools protocol attached" check: when Runtime is
  // enabled the protocol serialises every console argument with a preview,
  // and building the preview of an Error used to read its `stack`, so a
  // getter planted there fired if and only if a debugger was attached. This
  // is what the "stealth" Playwright forks exist to avoid.
  //
  // **Measured on Chrome 151 with `Runtime.enable` sent explicitly: it does
  // not fire** - for console.log, debug, error, dir and table alike, and not
  // for a getter on a plain object either. V8 reads the stack internally now.
  // So a false here does not mean "undetectable"; it means this particular
  // tell is gone from current Chrome. Kept because it costs nothing and will
  // say so if a build brings it back.
  try {
    let touched = false;
    const err = new Error('probe');
    Object.defineProperty(err, 'stack', { get() { touched = true; return ''; }, configurable: true });
    console.debug(err);
    await new Promise((resolve) => setTimeout(resolve, 50));
    out.cdp_runtime_leak = touched;
  } catch (e) { out.cdp_runtime_leak = 'error: ' + e.message; }

  // Permissions API behaving like a real browser's.
  try {
    const p = await navigator.permissions.query({ name: 'notifications' });
    out.notification_permission = { query: p.state, api: Notification.permission };
  } catch (e) { out.notification_permission = 'error: ' + e.message; }

  return out;
}
"""


async def probe() -> dict:
    """Launch the browser the way a run does, and report what it is."""
    started = time.monotonic()
    report: dict = {
        "trove": __version__,
        "headless": settings.headless,
        "in_container": settings.in_container,
        "display": os.environ.get("DISPLAY"),
        "vnc": settings.vnc_address or None,
        "channel_setting": settings.browser_channel,
        "proxy": settings.browser_proxy or None,
        "launch_args": list(LAUNCH_ARGS),
    }
    probe_dir = settings.profiles_path / ".diagnose"
    try:
        async with async_playwright() as playwright:
            channel = await resolve_channel(playwright, probe_dir)
            report["channel"] = channel or "bundled chromium"
            # After the resolve, because that is when --no-sandbox is decided.
            report["launch_args"] = list(LAUNCH_ARGS)
            report["sandbox"] = (
                "off (--no-sandbox)" if "--no-sandbox" in LAUNCH_ARGS else "on"
            )
            probe_dir.mkdir(parents=True, exist_ok=True)
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(probe_dir),
                headless=settings.headless,
                channel=channel,
                args=LAUNCH_ARGS,
                viewport=VIEWPORT,
                locale="en-GB",
                timezone_id="Europe/Oslo",
                timeout=60_000,
                proxy={"server": settings.browser_proxy} if settings.browser_proxy else None,
                chromium_sandbox="--no-sandbox" not in LAUNCH_ARGS,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    # `context.browser` is None for a persistent context, so
                    # ask the protocol. "Chrome/151.0.7444.60" is the answer
                    # that tells the bundle and the real thing apart at a glance.
                    cdp = await context.new_cdp_session(page)
                    version = await cdp.send("Browser.getVersion")
                    report["browser_version"] = version.get("product")
                    await cdp.detach()
                except Exception as exc:  # pragma: no cover
                    report["browser_version"] = f"unknown ({exc})"
                # A real origin rather than about:blank: some of the APIs
                # (permissions, WebGPU) behave differently on an opaque origin.
                await page.goto("https://example.com/", wait_until="domcontentloaded", timeout=30_000)
                report["page"] = await page.evaluate(PROBE_JS)
            finally:
                await context.close()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
    finally:
        purge_profile(probe_dir)

    report["seconds"] = round(time.monotonic() - started, 1)
    report["findings"] = findings(report)
    return report


def findings(report: dict) -> list[dict]:
    """The contradictions worth a sentence. Empty means nothing stood out.

    Each entry names what was seen and what a challenge is likely to make of
    it, in words, so the interface can show them as they are. Nothing here is a
    score and nothing should be turned into one.
    """
    out: list[dict] = []
    page = report.get("page")
    if report.get("error"):
        out.append({"level": "critical", "text": f"The browser did not launch: {report['error']}"})
        return out
    if not isinstance(page, dict):
        return out

    if report.get("channel") == "bundled chromium":
        out.append(
            {
                "level": "critical",
                "text": "Driving Playwright's bundled Chromium, not Google Chrome. "
                "It has no H.264, HEVC or AAC while claiming to be Chrome, and "
                "Cloudflare's challenge probes for exactly those.",
            }
        )
    brands = page.get("brands") or []
    if isinstance(brands, list) and not any("Google Chrome" in b for b in brands):
        out.append(
            {
                "level": "caution",
                "text": f"userAgentData.brands does not include Google Chrome: {brands}.",
            }
        )
    codecs = page.get("codecs")
    if isinstance(codecs, dict):
        missing = [name for name in ("h264", "aac") if not codecs.get(name)]
        if missing:
            out.append(
                {
                    "level": "critical",
                    "text": f"No {' or '.join(missing)} playback. Real Chrome answers "
                    "'probably'; a challenge notices the difference.",
                }
            )
    if page.get("webdriver") is True:
        out.append({"level": "caution", "text": "navigator.webdriver is true."})
    if page.get("webgl") in (None, "null"):
        out.append(
            {
                "level": "caution",
                "text": "WebGL is unavailable. Desktop Chrome always has it; under "
                "Xvfb this needs Mesa and --ignore-gpu-blocklist.",
            }
        )
    elif isinstance(page.get("webgl"), dict):
        renderer = str(page["webgl"].get("renderer") or "")
        if "SwiftShader" in renderer:
            out.append(
                {
                    "level": "info",
                    "text": f"WebGL renderer is SwiftShader ({renderer}), Chrome's own "
                    "software fallback. It works, but it is a well-known headless tell.",
                }
            )
    webgpu = page.get("webgpu")
    if webgpu is None or (isinstance(webgpu, dict) and not webgpu.get("adapter")):
        out.append(
            {
                "level": "caution",
                "text": "No WebGPU adapter. A challenge has been seen to log "
                "'No available adapters.' in exactly this state.",
            }
        )
    if page.get("cdp_runtime_leak") is True:
        out.append(
            {
                "level": "info",
                "text": "The page can tell the DevTools protocol is attached (an "
                "Error's stack getter fires on console.debug). That is the "
                "price of driving a browser, and why the un-driven sign-in "
                "window exists.",
            }
        )
    if page.get("has_focus") is False:
        out.append(
            {
                "level": "info",
                "text": "document.hasFocus() is false: the window is not in front. "
                "Runs enable focus emulation; the probe does not.",
            }
        )
    screen = page.get("screen")
    if isinstance(screen, dict) and (screen.get("width", 0) < VIEWPORT["width"]):
        out.append(
            {
                "level": "caution",
                "text": f"The screen ({screen.get('width')}x{screen.get('height')}) is "
                f"smaller than the viewport ({VIEWPORT['width']}x{VIEWPORT['height']}).",
            }
        )
    return out


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    report = asyncio.run(probe())
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
