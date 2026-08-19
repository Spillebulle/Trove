"""The live view: the account's own browser, in the page.

This is the answer to the two problems CLAUDE.md says everything else depends
on - the one-time interactive sign-in, and what to do when a run meets a
captcha. Both are the same problem, so both get the same window: the user opens
the account's real browser profile inside the interface, does whatever the
store asked, and closes it. The session that results is the session every later
run reuses.

**How it works.** Chromium's DevTools protocol can stream a page as JPEG frames
(`Page.startScreencast`) and can be sent synthetic input (`Input.dispatch*`).
Trove opens the account's persistent profile, attaches a CDP session to its
page, pumps frames down a WebSocket and pumps clicks and keystrokes back up.
It is a small remote desktop for exactly one tab.

**Why not noVNC.** The alternative in CLAUDE.md was a VNC server beside the app
with a viewer in the page. That works, and it means a second process, a second
port, an X server in the image whether or not anybody is signing in, and a
password on the VNC socket that becomes a second thing to get wrong. CDP is
already there: Playwright is already speaking it to drive the browser, the
frames arrive on the connection that exists, and the whole feature is this one
file. The cost is that it is Chromium-only, which the app already was.

**What it is not.** It is not a general browser. It refuses to navigate
anywhere but the store the account belongs to, because a window that renders
arbitrary pages inside an authenticated app is a window somebody will point at
something else. And the frames are not recorded: they are forwarded and
dropped.

Two hard-won details are commented where they happen: frames must be
acknowledged or the stream stops after one, and typing has to go through
`Input.insertText` rather than synthesised key events or half the world's
keyboard layouts produce nothing.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import CDPSession, Page

from .browser import VIEWPORT, first_page, manager
from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Screencast settings. 60 is a deliberate quality: a store page is text and
# flat colour, which JPEG handles well, and the difference between 60 and 90 is
# about 2.5x the bytes for a picture nobody is inspecting. The frame size is
# the viewport's, so the client can map a click without asking anything.
SCREENCAST = {
    "format": "jpeg",
    "quality": 60,
    "maxWidth": VIEWPORT["width"],
    "maxHeight": VIEWPORT["height"],
    # Drop frames rather than queue them when the socket is slower than the
    # page. A live view that is three seconds behind is worse than one that
    # skipped the animation.
    "everyNthFrame": 1,
}

# The keys that are not text. Everything else goes through `insertText`, so
# this list is short by design and is the whole of the keyboard mapping.
#
# The virtual key codes are the Windows ones CDP expects. They are the same
# numbers on every platform as far as this protocol is concerned.
CONTROL_KEYS: dict[str, tuple[int, str]] = {
    "Backspace": (8, "Backspace"),
    "Tab": (9, "Tab"),
    "Enter": (13, "Enter"),
    "Escape": (27, "Escape"),
    "PageUp": (33, "PageUp"),
    "PageDown": (34, "PageDown"),
    "End": (35, "End"),
    "Home": (36, "Home"),
    "ArrowLeft": (37, "ArrowLeft"),
    "ArrowUp": (38, "ArrowUp"),
    "ArrowRight": (39, "ArrowRight"),
    "ArrowDown": (40, "ArrowDown"),
    "Delete": (46, "Delete"),
}

# DOM `MouseEvent.button` to CDP's name. `-1` is what a pointer *move* carries
# when no button changed, and it is deliberately absent: a move is resolved
# through `_button_for` below rather than through this table.
async def enable_focus(cdp: CDPSession) -> None:
    """Tell the renderer the page is focused, whatever the window manager says.

    The browser Trove drives is never the foreground window: the user is looking
    at Trove in their own browser, and in a container there is no foreground at
    all. A page that reports `document.hasFocus() === false` is one a challenge
    widget treats with suspicion, so this removes that artefact - which is a
    removal rather than a forgery, the distinction CLAUDE.md draws. A real
    person really is looking at this page and really did click it; the only
    reason it might believe otherwise is that it is drawn somewhere they cannot
    see.

    **Measured, and it was not the problem.** On headed Chromium on Windows,
    `document.hasFocus()` stayed `true` with the window backgrounded and even
    minimised, with and without this call. So it fixes nothing that has been
    observed, and it is kept only because it costs one command and the
    container case (Xvfb, no window manager at all) has not been measured. Do
    not cite it as the reason a challenge started passing: the mouse-button fix
    below is the one with evidence behind it.

    Best effort: an older Chromium without the command should cost the live
    view nothing.
    """
    try:
        await cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
    except Exception as exc:  # pragma: no cover - depends on the build
        logger.debug("Focus emulation is unavailable: %s", exc)


_MOUSE_BUTTONS = {0: "left", 1: "middle", 2: "right", 3: "back", 4: "forward"}


def _button_for(event_type: str, button: int, buttons: int) -> str:
    """The CDP button name for one event.

    This is the fix for a real defect rather than a tidy-up. `MouseEvent.button`
    is `-1` on a move where no button changed, and looking that up in the table
    above with a `left` default meant **every pointer movement was dispatched as
    a left-button drag**. Chromium then saw one continuous press-move-release
    gesture instead of a hover followed by a click, which is both wrong and a
    poor thing to show a challenge that watches how the pointer behaves.

    A move with nothing held is `none`. A move with something held is a genuine
    drag and keeps its button.
    """
    if event_type == "mouseWheel":
        return "none"
    if event_type == "mouseMoved":
        if buttons == 0:
            return "none"
        # The lowest held button, in CDP's own order: 1 left, 2 right, 4 middle.
        if buttons & 1:
            return "left"
        if buttons & 2:
            return "right"
        if buttons & 4:
            return "middle"
        return "none"
    return _MOUSE_BUTTONS.get(button, "left")


@dataclass(slots=True)
class LiveTarget:
    """Where a live session is allowed to be."""

    account_id: int
    store: str
    profile_path: object
    start_url: str
    # Hosts this session may navigate to. Anything else is refused: the window
    # exists to sign in to one store, not to browse.
    allowed_hosts: tuple[str, ...]


def hosts_for(login_url: str) -> tuple[str, ...]:
    """The hosts a store's sign-in legitimately crosses.

    A sign-in is rarely one host: Epic's store is `store.epicgames.com`, its
    account pages are `www.epicgames.com`, and its captcha is served by
    hCaptcha. So the rule is the registrable domain of the store plus the
    challenge providers, rather than an exact hostname that would break the
    moment a login redirects.
    """
    host = urlparse(login_url).hostname or ""
    parts = host.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else host
    return (root, "hcaptcha.com", "arkoselabs.com", "recaptcha.net", "google.com")


def _allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == item or host.endswith("." + item) for item in allowed_hosts)


class LiveSession:
    """One open live view. Owns the CDP session and the frame queue."""

    def __init__(self, page: Page, target: LiveTarget) -> None:
        self.page = page
        self.target = target
        self.cdp: CDPSession | None = None
        # Bounded, and small. If the socket cannot keep up, the right thing is
        # to drop the oldest frame and show the newest: an unbounded queue in
        # front of a slow client turns into minutes of latency and then memory.
        self.frames: asyncio.Queue[dict] = asyncio.Queue(maxsize=2)
        self.last_input = asyncio.get_event_loop().time()

    async def start(self) -> None:
        self.cdp = await self.page.context.new_cdp_session(self.page)
        await enable_focus(self.cdp)
        self.cdp.on("Page.screencastFrame", self._on_frame)
        await self.cdp.send("Page.startScreencast", SCREENCAST)

    def _on_frame(self, params: dict) -> None:
        """Take a frame off the CDP connection.

        Synchronous, because that is what Playwright calls, and it runs on the
        event loop so `put_nowait` is safe. The acknowledgement happens in the
        consumer: Chromium sends exactly one more frame after each ack, so
        acking here rather than after the frame is delivered would let the page
        outrun a slow socket by an unbounded amount.
        """
        if self.frames.full():
            try:
                self.frames.get_nowait()  # drop the stale frame
            except asyncio.QueueEmpty:  # pragma: no cover - race with consumer
                pass
        self.frames.put_nowait(params)

    async def ack(self, session_id: int) -> None:
        """Tell Chromium the frame is dealt with and another may come.

        Without this the stream stops after the first frame, which presents as
        a live view that shows one still image and never updates. It is the
        single easiest thing to leave out of a screencast implementation.
        """
        if self.cdp is None:
            return
        try:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception as exc:
            logger.debug("Frame acknowledgement failed: %s", exc)

    async def stop(self) -> None:
        if self.cdp is None:
            return
        try:
            await self.cdp.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            await self.cdp.detach()
        except Exception:
            pass
        self.cdp = None

    # ── Input ────────────────────────────────────────────────────────────

    def touch(self) -> None:
        self.last_input = asyncio.get_event_loop().time()

    async def mouse(self, message: dict) -> None:
        if self.cdp is None:
            return
        event_type = message.get("event")
        if event_type not in ("mousePressed", "mouseReleased", "mouseMoved", "mouseWheel"):
            return
        button = int(message.get("button", -1))
        # The bitmask of what is held *now*, straight from the DOM event, where
        # it already means the same thing CDP means by it: 1 left, 2 right,
        # 4 middle. Sending it is what makes a press, a drag and a release read
        # as one coherent gesture rather than three events that disagree about
        # the state of the mouse.
        buttons = int(message.get("buttons", 0))

        payload = {
            "type": event_type,
            "x": float(message.get("x", 0)),
            "y": float(message.get("y", 0)),
            "modifiers": int(message.get("modifiers", 0)),
            "button": _button_for(event_type, button, buttons),
            "buttons": buttons,
        }
        if event_type == "mouseWheel":
            payload["deltaX"] = float(message.get("deltaX", 0))
            payload["deltaY"] = float(message.get("deltaY", 0))
        else:
            # A move carries no click count. Sending 1 on a move tells the
            # renderer a click is in progress at every pixel of the journey.
            payload["clickCount"] = 0 if event_type == "mouseMoved" else int(
                message.get("clickCount", 1)
            )
        await self.cdp.send("Input.dispatchMouseEvent", payload)
        self.touch()

    async def key(self, message: dict) -> None:
        """A control key. Text does not come through here.

        Synthesising key events for printable characters means reproducing the
        browser's own keyboard layout handling, and it goes wrong for every
        layout where a character is not on the key that appears to make it:
        a dead key, an AltGr combination, anything typed on a phone keyboard,
        anything pasted. `Input.insertText` sidesteps all of it, so this only
        handles the keys that have no text at all.
        """
        if self.cdp is None:
            return
        name = message.get("key")
        mapped = CONTROL_KEYS.get(name)
        if mapped is None:
            return
        code, key_name = mapped
        for event_type in ("rawKeyDown", "keyUp"):
            await self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "key": key_name,
                    "code": key_name,
                    "windowsVirtualKeyCode": code,
                    "nativeVirtualKeyCode": code,
                    "modifiers": int(message.get("modifiers", 0)),
                },
            )
        self.touch()

    async def text(self, message: dict) -> None:
        if self.cdp is None:
            return
        value = message.get("text")
        if not isinstance(value, str) or not value:
            return
        # A cap, because this arrives from a browser and a WebSocket message is
        # whatever somebody sends. A password is not 4 kB.
        await self.cdp.send("Input.insertText", {"text": value[:4096]})
        self.touch()

    async def navigate(self, message: dict) -> None:
        url = message.get("url")
        if not isinstance(url, str):
            return
        if not _allowed(url, self.target.allowed_hosts):
            logger.info("Refused a live-view navigation to %s.", url)
            return
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            logger.debug("Live navigation to %s failed: %s", url, exc)
        self.touch()


def frame_message(params: dict) -> dict:
    """One screencast frame, as the client wants it.

    The metadata Chromium sends alongside is dropped except for the size. The
    viewport is fixed (`browser.VIEWPORT`) so the client can scale a click by
    the ratio of its canvas to that, and the per-frame device metrics would
    only give it a second, sometimes-disagreeing answer.
    """
    metadata = params.get("metadata") or {}
    return {
        "type": "frame",
        "data": params.get("data", ""),
        "width": VIEWPORT["width"],
        "height": VIEWPORT["height"],
        "scroll": metadata.get("scrollOffsetY", 0),
    }


async def open_session(target: LiveTarget):
    """Open the account's profile for a live view.

    Returns the async context manager, so the caller owns the lifetime: a live
    view holds the profile for as long as the window is open, and the run
    scheduler is told the profile is busy by the same lock every run uses.
    """
    return manager.session(
        target.account_id,
        target.profile_path,
        holder="the live view",
        wait_s=1.0,
    )


async def prepare(context, target: LiveTarget) -> LiveSession:
    page = await first_page(context)
    try:
        await page.goto(target.start_url, wait_until="domcontentloaded")
    except Exception as exc:
        # A store that is slow or down is not a reason to refuse the window:
        # the user can still see what happened and try the reload button.
        logger.info("Live view could not load %s: %s", target.start_url, exc)
    session = LiveSession(page, target)
    await session.start()
    return session


def status_message(page: Page) -> dict:
    return {"type": "status", "url": page.url, "title": ""}


def decode_frame(data: str) -> bytes:  # pragma: no cover - used by tests only
    return base64.b64decode(data)
