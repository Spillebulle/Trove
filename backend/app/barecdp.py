"""One browser tab over a bare DevTools socket, with nothing else attached.

This exists because of one measurement (24 Nov 2026, in the container, on a
copy of the Epic account's profile, same item, minutes apart): **Epic's
checkout passes its hCaptcha check silently when Chrome is driven over a bare
DevTools connection, and puts up an interactive puzzle when Playwright is
attached.** Three bare checkouts completed (`confirm-order` -> `COMPLETED`);
the same checkout through the run's Playwright session got a puzzle twice.
It is the Cloudflare finding again (`browser._pass_check_unattached`), one
step further down the page.

So a store that needs it drives its order through this instead of a Playwright
page. What is used here is deliberately small - `Page.navigate`,
`Runtime.evaluate`, `Input.dispatchMouseEvent`, `Page.captureScreenshot` - and
no domain is ever enabled: the three passing runs enabled none, and nothing
measured says what would happen with more.

It answers nothing. A challenge that appears is reported by the caller and
left for a person, exactly as before.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from contextlib import asynccontextmanager

from .browser import _page_targets
from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BareTab:
    """Commands in, results out. No events are subscribed to."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.next_id = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        mine = self.next_id
        await self.ws.send(json.dumps({"id": mine, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=45))
            if message.get("id") == mine:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error'].get('message')}")
                return message.get("result") or {}

    async def evaluate(self, expression: str):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return (result.get("result") or {}).get("value")

    async def navigate(self, url: str) -> None:
        await self.call("Page.navigate", {"url": url})

    async def click(self, x: float, y: float) -> None:
        """One trusted left click at a point in the viewport.

        The move carries no button and the press and release carry `buttons`
        explicitly - the live view's lesson: a move that arrives with a button
        held reads as a drag, which is the shape of a bot.
        """
        point = {"x": x, "y": y, "pointerType": "mouse"}
        await self.call(
            "Input.dispatchMouseEvent",
            {**point, "type": "mouseMoved", "button": "none", "buttons": 0},
        )
        await asyncio.sleep(random.uniform(0.2, 0.45))
        await self.call(
            "Input.dispatchMouseEvent",
            {**point, "type": "mousePressed", "button": "left", "buttons": 1, "clickCount": 1},
        )
        await asyncio.sleep(random.uniform(0.06, 0.12))
        await self.call(
            "Input.dispatchMouseEvent",
            {**point, "type": "mouseReleased", "button": "left", "buttons": 0, "clickCount": 1},
        )

    async def screenshot(self, name: str) -> str | None:
        """Save what the tab shows under the screenshots directory. Best effort."""
        try:
            shot = await self.call("Page.captureScreenshot", {"format": "png"})
            path = settings.screenshots_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(shot["data"]))
            return name
        except Exception as exc:
            logger.warning("Could not save the screenshot %s: %s", name, exc)
            return None


@asynccontextmanager
async def open_tab(port: int):
    """The first tab of the Chrome listening on `port`."""
    import websockets

    targets = _page_targets(port)
    if not targets:
        raise RuntimeError("The browser has no tab to drive.")
    async with websockets.connect(targets[0]["webSocketDebuggerUrl"], max_size=None) as ws:
        yield BareTab(ws)
