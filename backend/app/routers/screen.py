"""The container's screen, and the browser check.

Two things that only matter where Trove has a display nobody is standing in
front of - which is to say, in the container.

**The screen.** `WS /api/screen` is Trove's own authenticated WebSocket bridged
byte-for-byte to the VNC server the entrypoint runs against the Xvfb display.
The frontend drives it with noVNC's RFB client. It is what makes "sign in here"
possible in a container: the account's Chrome opens *un-driven* on that display
- no DevTools protocol, no automation flags, the exact launch a desktop gets -
and the person watches and works the screen through here. The live view cannot
do that job, because it needs CDP attached and a page can tell. This reads
pixels off the X server instead; the browser has nothing attached to it.

Why bridge rather than expose 5900 or run websockify on a second port: one
port, one login, and a store session that is never reachable by anybody who
has not signed in to Trove. The VNC server itself listens on localhost only
and has no password, because the only thing that can reach it is this process.

**The browser check.** `GET /api/diagnostics/browser` runs `diagnose.probe`,
which launches a throwaway profile exactly the way a run does and reports what
the page sees: brands, codecs, WebGL, WebGPU. Every challenge that has beaten
this app was explained by one of those values, and every one was invisible
until printed.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from ..auth import SESSION_USER_KEY, current_user
from ..browser import manager
from ..config import get_settings
from ..diagnose import probe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["screen"])

settings = get_settings()

CLOSE_UNAUTHENTICATED = 1008
CLOSE_UNAVAILABLE = 1011

# One probe at a time. It opens a browser; two at once on the same machine
# would only make both slower and the second would purge the first's profile.
_probe_lock = asyncio.Lock()


@router.get("/screen/available", dependencies=[Depends(current_user)])
def screen_available() -> dict:
    """Whether there is a screen to show, and why not if there is not."""
    if settings.headless:
        return {"ok": False, "reason": "Trove is running headless; there is no screen."}
    if settings.vnc_endpoint is None:
        return {
            "ok": False,
            "reason": "No VNC server is configured for Trove's display (VNC_ADDRESS is unset).",
        }
    return {"ok": True, "reason": None, "holders": manager.holders()}


@router.websocket("/screen")
async def screen(websocket: WebSocket) -> None:
    """Bridge the socket to the VNC server. Bytes in, bytes out, nothing parsed."""
    if not websocket.session.get(SESSION_USER_KEY):
        await websocket.close(code=CLOSE_UNAUTHENTICATED)
        return
    endpoint = settings.vnc_endpoint
    if endpoint is None or settings.headless:
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    await websocket.accept()

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(*endpoint), timeout=5)
    except (OSError, asyncio.TimeoutError) as exc:
        logger.warning("Cannot reach the VNC server at %s:%s: %s", *endpoint, exc)
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    async def to_vnc() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is None:
                text = message.get("text")
                if text is None:
                    continue
                data = text.encode("latin-1", errors="ignore")
            writer.write(data)
            await writer.drain()

    async def to_ws() -> None:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            await websocket.send_bytes(chunk)

    tasks = [asyncio.create_task(to_vnc()), asyncio.create_task(to_ws())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, RuntimeError, ConnectionError)):
                logger.debug("Screen bridge ended: %s", exc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/diagnostics/browser", dependencies=[Depends(current_user)])
async def browser_diagnostics() -> dict:
    """Launch the browser the way a run does and say what it is. Slow: seconds."""
    if _probe_lock.locked():
        raise HTTPException(409, "A browser check is already running.")
    async with _probe_lock:
        return await probe()
