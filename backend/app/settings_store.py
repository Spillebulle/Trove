"""The global settings, read and written through one place.

`models.Setting` is a key/value table. This module owns the keys, their
defaults and which of them are secret, so no caller has to remember that
`notify.webhook_url` is encrypted and `notify.on_claimed` is not.

Every key is namespaced by the page it appears on, so the settings page can be
rendered from this table without a second list to keep in step.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .crypto import decrypt, encrypt
from .models import Setting

logger = logging.getLogger(__name__)

# The settings, their defaults, and whether the value is a secret at rest.
#
# A secret is stored through `crypto.encrypt`, so the database never holds it
# in the clear, and it is never sent back to the browser: the API answers with
# a redacted placeholder and the UI shows "configured" rather than the value.
# A webhook URL is a write capability to somebody's Discord channel, which is
# why it counts.
DEFAULTS: dict[str, tuple[Any, bool]] = {
    # --- Notifications ---------------------------------------------------
    # "discord" posts a Discord embed; "webhook" posts a flat JSON body to
    # anything else (ntfy, Gotify, a home automation hook). "off" is the
    # default: an app that starts by asking for a webhook is an app that
    # cannot be tried out.
    "notify.channel": ("off", False),
    "notify.webhook_url": ("", True),
    # Which events are worth a message. A claim is the good news, attention is
    # the news that needs a person, and a failure is the news that something
    # broke. Run summaries are off by default: one message per account per run
    # is how a notification channel gets muted.
    "notify.on_claimed": (True, False),
    "notify.on_attention": (True, False),
    "notify.on_failed": (True, False),
    "notify.on_run_summary": (False, False),
    # --- Schedule --------------------------------------------------------
    # The scheduler is off until the user turns it on. The first thing a new
    # install needs is a hand-driven sign-in, and a background loop opening
    # browsers while somebody is still reading the page is a bad first minute.
    "schedule.enabled": (False, False),
    # --- Discovery -------------------------------------------------------
    # Ask a public giveaway feed what is free, so the store adapters only wake
    # up when there is something to claim. Off by default: it is a third-party
    # service, and CLAUDE.md asks for its terms to be checked before the app
    # depends on it. Every adapter can discover its own offers without it.
    "discovery.feed_enabled": (False, False),
}

REDACTED = "__set__"


def _coerce(raw: str | None, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Setting value is not valid JSON; using the default.")
        return fallback


def get(db: Session, key: str) -> Any:
    """The stored value, or the default. Secrets come back decrypted."""
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting {key!r}")
    fallback, secret = DEFAULTS[key]
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None or row.value is None:
        return fallback
    if secret:
        value = decrypt(row.value)
        return fallback if value is None else value
    return _coerce(row.value, fallback)


def get_all(db: Session) -> dict[str, Any]:
    """Every setting, with secrets redacted rather than returned.

    The UI needs to know whether a webhook is configured; it never needs the
    URL back. Sending it would put a live capability into every browser tab and
    into any log that captures a response body.
    """
    out: dict[str, Any] = {}
    for key, (fallback, secret) in DEFAULTS.items():
        value = get(db, key)
        if secret:
            out[key] = REDACTED if value else ""
        else:
            out[key] = value
    return out


def set(db: Session, key: str, value: Any) -> None:
    """Write one setting. Commits.

    Passing the redaction placeholder back is how the UI says "leave this as it
    is", which is what a form does when it never held the real value to begin
    with.
    """
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting {key!r}")
    _, secret = DEFAULTS[key]
    if secret and value == REDACTED:
        return
    stored = encrypt(value) if secret else json.dumps(value)
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        row = Setting(key=key, value=stored)
        db.add(row)
    else:
        row.value = stored
    db.commit()


def set_many(db: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key in DEFAULTS:
            set(db, key, value)
