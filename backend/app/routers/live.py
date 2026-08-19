"""The live view's WebSocket.

One socket per open window. It holds the account's browser profile for as long
as it is open, which is why the frame pump also watches for idleness: a user
who signs in and wanders off must not leave a Chromium holding a profile lock
that every scheduled run then fails against.

Authentication is the same session cookie the rest of the API uses. Starlette's
SessionMiddleware runs for WebSocket connections too, so `websocket.session`
is populated before `accept`, and an unauthenticated socket is closed before
a browser is ever opened.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..adapters import get_adapter
from ..auth import SESSION_USER_KEY, current_user
from ..browser import VIEWPORT, ProfileBusy, manager
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..live import (
    MIN_FRAME_INTERVAL_S,
    LiveTarget,
    frame_bytes,
    hosts_for,
    open_session,
    prepare,
)
from ..models import Account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

settings = get_settings()

# Close codes. 1008 is "policy violation", which is what a WebSocket has
# instead of a 401 and a 409.
CLOSE_UNAUTHENTICATED = 1008
CLOSE_BUSY = 1008
CLOSE_IDLE = 1000


@router.get("/{account_id}/can-open", dependencies=[Depends(current_user)])
def can_open(account_id: int, db: Session = Depends(get_db)) -> dict:
    """Whether the live view can open, and why not if it cannot.

    A pre-flight, so the interface can disable the button with a tooltip
    saying why rather than opening a window that immediately closes. A control
    that lies is worse than none.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")
    holder = manager.who_holds(account_id)
    if holder:
        return {"ok": False, "reason": f"The browser profile is in use by {holder}."}
    return {"ok": True, "reason": None}


def _target(db: Session, account_id: int) -> LiveTarget:
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise LookupError("No such account.")
    adapter = get_adapter(account.store)
    return LiveTarget(
        account_id=account.id,
        store=account.store,
        profile_path=settings.profiles_path / account.profile_path,
        start_url=adapter.login_url,
        allowed_hosts=hosts_for(adapter.login_url),
    )


@router.websocket("/{account_id}")
async def live(websocket: WebSocket, account_id: int) -> None:
    if not websocket.session.get(SESSION_USER_KEY):
        await websocket.close(code=CLOSE_UNAUTHENTICATED)
        return

    db = SessionLocal()
    try:
        try:
            target = _target(db, account_id)
        except LookupError:
            await websocket.close(code=CLOSE_UNAUTHENTICATED)
            return
    finally:
        db.close()

    await websocket.accept()

    try:
        session_cm = await open_session(target)
    except ProfileBusy as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=CLOSE_BUSY)
        return

    try:
        async with session_cm as context:
            try:
                session = await prepare(context, target)
            except ProfileBusy as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
                return

            # The viewport is fixed and stated once, so the client can map a
            # click by the ratio of its canvas to these numbers without asking
            # anything per frame.
            await websocket.send_json(
                {
                    "type": "ready",
                    "width": VIEWPORT["width"],
                    "height": VIEWPORT["height"],
                    "url": session.page.url,
                }
            )

            pump = asyncio.create_task(_pump_frames(websocket, session))
            try:
                await _read_input(websocket, session)
            finally:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
                await session.stop()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("The live view for account %s ended badly.", account_id)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


async def _pump_frames(websocket: WebSocket, session) -> None:
    """Frames out, acknowledged once they have been sent and the rate allows.

    The acknowledgement is what asks Chromium for the next frame, so doing it
    here rather than in the CDP callback is what makes the stream self-pacing:
    a slow socket simply receives fewer frames instead of building a backlog.

    **And it is what caps the frame rate.** Acknowledging the instant a frame
    went out asks Chromium to render again immediately, so a page with any
    animation on it renders flat out: measured at 113 frames a second and
    7.7 MB/s on the Epic store, which is 62 Mbit/s and further behind every
    second over anything but a LAN. Holding the acknowledgement back to
    `MIN_FRAME_INTERVAL_S` bounds the whole pipeline at its source, before a
    frame is ever encoded.

    The frame itself is still sent the moment it arrives; only the request for
    the *next* one waits. Delaying the send instead would add latency to the
    picture the user is looking at now, which is the opposite of the point.
    """
    loop = asyncio.get_event_loop()
    last_ack = 0.0
    while True:
        params = await session.frames.get()
        try:
            # Sent as a **binary** message, not base64 inside JSON. Chromium
            # hands the frame over base64-encoded, so re-encoding it into JSON
            # kept the 33 % inflation and added a large string allocation per
            # frame at both ends. The client decodes the bytes with
            # `createImageBitmap`, which is cheaper than a data URL as well.
            #
            # Control messages stay JSON; the client tells them apart by type,
            # which is what `binaryType = 'arraybuffer'` makes unambiguous.
            await websocket.send_bytes(frame_bytes(params))
        except (WebSocketDisconnect, RuntimeError):
            return
        except ValueError:
            # A frame that will not decode is one frame, not a reason to end
            # the session. Acknowledge it so Chromium sends the next.
            logger.debug("Dropped a screencast frame that would not decode.")
        session_id = params.get("sessionId")
        if session_id is not None:
            wait = MIN_FRAME_INTERVAL_S - (loop.time() - last_ack)
            if wait > 0:
                await asyncio.sleep(wait)
            last_ack = loop.time()
            await session.ack(session_id)


async def _read_input(websocket: WebSocket, session) -> None:
    """Input in, until the window closes or goes quiet.

    The idle timeout is on the *receive* rather than on a separate timer, so
    the one clock is the one thing that matters: whether a person is still
    there. A page that animates on its own does not count as company.
    """
    loop = asyncio.get_event_loop()
    while True:
        remaining = settings.live_idle_timeout_s - (loop.time() - session.last_input)
        if remaining <= 0:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": (
                        "The live view closed after sitting idle. Nothing was "
                        "lost; open it again when you are ready."
                    ),
                }
            )
            await websocket.close(code=CLOSE_IDLE)
            return
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=remaining)
        except asyncio.TimeoutError:
            continue
        except (WebSocketDisconnect, RuntimeError):
            return

        kind = message.get("type")
        if kind == "mouse":
            await session.mouse(message)
        elif kind == "key":
            await session.key(message)
        elif kind == "text":
            await session.text(message)
        elif kind == "navigate":
            await session.navigate(message)
        elif kind == "reload":
            session.touch()
            try:
                await session.page.reload(wait_until="domcontentloaded")
            except Exception as exc:
                logger.debug("Live reload failed: %s", exc)
        elif kind == "ping":
            # A keepalive that is deliberately *not* activity: a window left
            # open in a background tab should still time out.
            await websocket.send_json({"type": "pong", "url": session.page.url})
        elif kind == "done":
            return
