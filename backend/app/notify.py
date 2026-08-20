"""Notifications: a Discord webhook, or a plain one.

Two channels and no plugin system. Discord is what the user asked for and gets
a proper embed with a colour per severity; "webhook" posts a flat JSON body so
ntfy, Gotify, Home Assistant or a script can read it without Trove pretending
to know their formats.

Everything here is best effort. A notification that fails must never break a
claim run: the game is claimed either way, and the ledger row is the record
that matters. Failures are logged and the run carries on.

Discord's own webhook rate limit is 5 requests per 2 seconds per webhook, and
30 per minute for the same channel. Trove sends single-figure numbers of
messages per run, so there is no queue here; the one guard is that a run
summary is a single message rather than one per claim.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import settings_store
from .db import SessionLocal

logger = logging.getLogger(__name__)

# Severity drives the Discord embed colour and the `severity` field on a plain
# webhook. Three ranks, matching the semantic colours in STYLE-GUIDE 2.5:
# good, caution, critical. There is no "accent" severity, because the accent
# never means state.
SEVERITIES = ("good", "info", "caution", "critical")

# Discord embed colours, as decimal RGB. These are the app's own semantic
# colours converted to sRGB, not Discord's defaults, so a message in the
# channel looks like it came from this app.
#
#   good      #4B9E52   the sage green of --good
#   info      #AA85C5   the orchid accent, for a plain statement of fact
#   caution   #D08770   the warm clay of --caution
#   critical  #D95B4A   the muted red of --critical
#
# The accent appears here and nowhere else in the app as a colour that is not
# "selected / in hand / primary". A Discord embed is not the app's interface,
# and the alternative is a message with no identity at all.
_COLOUR = {
    "good": 0x4B9E52,
    "info": 0xAA85C5,
    "caution": 0xD08770,
    "critical": 0xD95B4A,
}

_TIMEOUT = httpx.Timeout(10.0)

# The webhook wears Trove's face. `username` and `avatar_url` override whatever
# the webhook was named when it was made, so a message in the channel is
# unmistakably from this app rather than from "Captain Hook" with a default
# avatar. The avatar is the app icon in the repo, which Discord fetches once and
# caches; a public raw URL because Discord fetches it from its own side and has
# no access to the self-hosted instance.
BRAND_NAME = "Trove"
BRAND_URL = "https://github.com/Spillebulle/Trove"
BRAND_ICON = "https://raw.githubusercontent.com/Spillebulle/Trove/main/docs/brand/avatar.png"


@dataclass(slots=True)
class Notification:
    """One message. `title` is a fragment, `detail` is a sentence."""

    title: str
    detail: str | None = None
    severity: str = "info"
    # The account or store the message is about, shown in the footer so a user
    # with several accounts can tell which one spoke.
    context: str | None = None
    # A link the message should point at, when there is a page worth opening.
    url: str | None = None
    # The game's poster, shown large in a Discord embed - what makes a claim
    # look like a claim rather than a log line. A public URL (the store's own
    # CDN); a local screenshot path would not work, because Discord fetches the
    # image itself and cannot reach this machine.
    image_url: str | None = None
    # A smaller image, shown in the corner, when a big one would be too much.
    thumbnail_url: str | None = None
    # A local image file to *upload* - a screenshot of what stopped the run,
    # which Discord cannot fetch from a URL because it lives on this machine.
    # Attached to the message and shown in the embed. For context, never to be
    # solved: a captcha screenshot is a picture, not a puzzle Trove answers.
    image_path: str | None = None


def _discord_payload(note: Notification) -> dict:
    """A rich embed wearing the app's colours, with the poster when there is one.

    The shape: an author row carrying the mark and the app name, the title
    (linked when there is a page), the sentence as the description, the game's
    poster as the embed image, and a footer naming the account with the time.
    Every message is stamped so the channel reads as a timeline.
    """
    embed: dict = {
        "color": _COLOUR.get(note.severity, _COLOUR["info"]),
        "author": {"name": BRAND_NAME, "url": BRAND_URL, "icon_url": BRAND_ICON},
        "title": note.title,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": note.context or BRAND_NAME, "icon_url": BRAND_ICON},
    }
    if note.detail:
        embed["description"] = note.detail
    if note.url:
        embed["url"] = note.url
    if note.image_url:
        embed["image"] = {"url": note.image_url}
    elif note.thumbnail_url:
        embed["thumbnail"] = {"url": note.thumbnail_url}
    return {
        "username": BRAND_NAME,
        "avatar_url": BRAND_ICON,
        "embeds": [embed],
    }


def _plain_payload(note: Notification) -> dict:
    """A flat body for everything that is not Discord.

    Deliberately flat and deliberately boring: `title`, `message`, `severity`,
    `context`, `url`. Anything receiving this can pick the two fields it wants
    without a schema, and adding a field later cannot break a receiver.
    """
    return {
        "app": "Trove",
        "title": note.title,
        "message": note.detail or "",
        "severity": note.severity,
        "context": note.context,
        "url": note.url,
        "image_url": note.image_url,
        "image_path": note.image_path,
    }


async def post(channel: str, webhook_url: str, note: Notification) -> tuple[bool, str]:
    """Send one message. Returns (ok, a sentence saying what happened).

    Used by the dispatcher and by the settings page's "Send a test message",
    which is why it returns a sentence rather than raising: the test button has
    to show the user what went wrong, and a traceback is not an answer.
    """
    if channel == "off" or not webhook_url:
        return False, "No notification channel is configured."
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            if channel == "discord":
                payload = _discord_payload(note)
                shot = note.image_path
                if shot and Path(shot).is_file():
                    # Multipart: the embed points at the file by name, and the
                    # file rides along in the same request. This is the only way
                    # a local screenshot reaches Discord - it fetches URLs from
                    # its own side and cannot see this machine.
                    name = Path(shot).name
                    payload["embeds"][0]["image"] = {"url": f"attachment://{name}"}
                    response = await client.post(
                        webhook_url,
                        data={"payload_json": json.dumps(payload)},
                        files={"files[0]": (name, Path(shot).read_bytes(), "image/png")},
                    )
                else:
                    response = await client.post(webhook_url, json=payload)
            else:
                response = await client.post(webhook_url, json=_plain_payload(note))
    except httpx.HTTPError as exc:
        # The URL is deliberately absent from this message. It is a secret, and
        # an error string ends up in the log and in the UI.
        return False, f"Could not reach the webhook: {type(exc).__name__}."
    if response.status_code in (200, 202, 204):
        return True, "Delivered."
    if response.status_code == 429:
        return False, "The webhook is rate limiting. Try again in a minute."
    if response.status_code in (401, 403, 404):
        return False, (
            f"The webhook was rejected with HTTP {response.status_code}. "
            "It has probably been deleted or the URL is wrong."
        )
    body = response.text[:200].strip()
    return False, f"The webhook returned HTTP {response.status_code}. {body}"


# Which setting gates which kind of message. A kind with no entry is always
# sent, which is what the test message wants.
_FLAG_FOR_KIND = {
    "claimed": "notify.on_claimed",
    "attention": "notify.on_attention",
    "failed": "notify.on_failed",
    "run_summary": "notify.on_run_summary",
}


async def send(kind: str, note: Notification) -> None:
    """Dispatch a message of `kind`, if the user asked for that kind.

    Opens its own session rather than taking one: this is called from the
    scheduler's tasks, where the caller's session may already be committed and
    closed, and from request handlers, where holding the request's session
    across an await would keep a connection checked out for the length of an
    HTTP round trip to Discord.
    """
    db = SessionLocal()
    try:
        channel = settings_store.get(db, "notify.channel")
        if channel == "off":
            return
        flag = _FLAG_FOR_KIND.get(kind)
        if flag and not settings_store.get(db, flag):
            return
        url = settings_store.get(db, "notify.webhook_url")
    finally:
        db.close()

    ok, message = await post(channel, url, note)
    if not ok:
        logger.warning("Notification not delivered: %s (%s)", message, note.title)


def send_soon(kind: str, note: Notification) -> None:
    """Fire and forget, from inside a running event loop.

    The task is kept in a module-level set until it finishes. Without that,
    asyncio only holds a weak reference and a message can be garbage collected
    mid-flight, which shows up as a notification that arrives most of the time.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("No event loop; dropping the %s notification.", kind)
        return
    task = loop.create_task(send(kind, note))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


_pending: set[asyncio.Task] = set()
