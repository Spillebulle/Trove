"""Symmetric encryption of secrets at rest.

Three things in this database are secrets and none of them is a password:

  - a TOTP secret, if the user chose to supply one;
  - a claimed game key, which Prime Gaming hands out instead of a library add
    and which is worth money to whoever reads it;
  - a Discord webhook URL, which is a write capability to somebody's channel.

CLAUDE.md is explicit that TOTP secrets are treated as secrets at rest like
everything else, and a key sitting in plaintext in a ledger row would be the
one place this app stored something a thief actually wants.

The store *passwords* are deliberately absent. The app never has them: the
user signs in by hand, once, and what is kept afterwards is the browser
profile. See `browser.py`.

Encrypted values carry an `enc:` prefix so tooling can tell them from a legacy
plaintext row without guessing. Fernet tokens have their own structure, so the
prefix is disambiguation for us and not a security signal.

Rotating the key invalidates every existing encrypted value. There is no
rotation pipeline; a decrypt failure is logged loudly and returns None, so the
app keeps running and the affected field reads as empty rather than crashing a
page.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_PREFIX = "enc:"
_cached: Fernet | None = None


def _fernet() -> Fernet:
    global _cached
    if _cached is None:
        from .config import get_settings

        key = get_settings().credential_key.encode("ascii")
        try:
            _cached = Fernet(key)
        except Exception as exc:  # pragma: no cover - configuration error
            raise ValueError(
                "CREDENTIAL_KEY is not a valid Fernet key (32 url-safe "
                "base64-encoded bytes). Generate one with: python -c "
                '"from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc
    return _cached


def encrypt(value: Any) -> str | None:
    """JSON-serialise and encrypt. None passes through so a null stays null."""
    if value is None:
        return None
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return _PREFIX + _fernet().encrypt(payload).decode("ascii")


def decrypt(stored: str | None) -> Any:
    """Inverse of `encrypt`, tolerant of a value written before encryption."""
    if stored is None:
        return None
    if stored.startswith(_PREFIX):
        try:
            payload = _fernet().decrypt(stored[len(_PREFIX):].encode("ascii"))
        except InvalidToken:
            logger.error(
                "Could not decrypt a stored secret: the Fernet key does not "
                "match the one it was written with. Was CREDENTIAL_KEY changed, "
                "or .credential_key lost and regenerated? The field reads as "
                "empty until it is entered again."
            )
            return None
        return json.loads(payload.decode("utf-8"))
    try:
        return json.loads(stored)
    except json.JSONDecodeError:
        logger.error("A stored secret is neither encrypted nor valid JSON.")
        return None


def is_encrypted(stored: str | None) -> bool:
    return stored is not None and stored.startswith(_PREFIX)
