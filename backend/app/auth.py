"""One user, a cookie, a bcrypt password - and API keys for scripts.

Trove is a single-user self-hosted app, so there is no registration, no roles
and no password reset by email. The user is bootstrapped on first start and can
change the password from Settings.

The session is a signed cookie via Starlette's SessionMiddleware, keyed off
`secret_key` from config. Losing that secret logs the user out and nothing
worse, which is why it can be generated on first boot without ceremony.

API keys (`trv_...`, sent as `X-API-Key` or `Authorization: Bearer`) are the
second way in, for scripts and agents, in the shape Tally and HomeLab Manager
already use. They act as the single user, limited by their scope, and they
never reach what `session_user` guards: minting keys, the password, revealing
a stored game key, and typing stored sign-in details into a window. Those are
things a person does while looking, which is the rule CLAUDE.md sets for the
stored credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .models import ApiKey, AuthUser
from .timeutil import utcnow

logger = logging.getLogger(__name__)

SESSION_USER_KEY = "user"

# bcrypt has a hard 72-byte input limit and raises above it in 4.x. Truncate at
# the byte level rather than by characters, so a multi-byte password is not cut
# mid-codepoint into something that will not verify next time.
_BCRYPT_MAX_BYTES = 72


def _encode(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_encode(plain), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def bootstrap_admin() -> None:
    """Create the single user if there is not one yet.

    A failure here would otherwise crash the lifespan with an opaque traceback
    and leave no user row, so the operator could not log in to investigate.
    Roll back, log loudly, and let the app start: the login endpoint then
    returns a clean 401 rather than a 500.
    """
    from .config import get_settings

    settings = get_settings()
    db: Session = SessionLocal()
    try:
        if db.query(AuthUser).count() > 0:
            return
        initial = settings.admin_password or os.environ.get("ADMIN_PASSWORD") or "changeme"
        user = AuthUser(
            username=settings.admin_username,
            password_hash=hash_password(initial),
        )
        try:
            db.add(user)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error(
                "Could not create the user %r: %s. Signing in is impossible "
                "until the database is repaired or auth_users is seeded by "
                "hand.",
                settings.admin_username,
                exc,
            )
            return
        if initial == "changeme":
            logger.warning(
                "Created the user %r with the password 'changeme'. Sign in and "
                "change it now, or set ADMIN_PASSWORD before the first start. "
                "This app holds live store sessions.",
                settings.admin_username,
            )
        else:
            logger.info("Created the user %r from ADMIN_PASSWORD.", settings.admin_username)
    finally:
        db.close()


API_KEY_PREFIX = "trv_"
# Enough to identify a key on screen and to narrow the lookup; the rest is the
# secret. "trv_" plus eight characters.
_PREFIX_LENGTH = 12
SCOPES = ("full", "read_only")
# Everything that cannot change state.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# A write per request just to stamp a time would be silly; once a minute
# answers "is this key still in use".
_LAST_USED_RESOLUTION = timedelta(minutes=1)


def generate_api_key() -> tuple[str, str, str]:
    """(plaintext, prefix, sha256 hex). The plaintext is never stored."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:_PREFIX_LENGTH], hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _presented_key(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    key = request.headers.get("X-API-Key")
    return key.strip() if key else None


def _user_for_key(db: Session, presented: str, request: Request) -> str:
    candidates = (
        db.query(ApiKey)
        .filter(ApiKey.prefix == presented[:_PREFIX_LENGTH], ApiKey.revoked_at.is_(None))
        .all()
    )
    for key in candidates:
        # Constant time: the comparison must not leak how much matched.
        if not hmac.compare_digest(hash_api_key(presented), key.key_hash):
            continue
        if key.scope not in SCOPES:
            # A hand-edited row or a newer build's scope after a downgrade:
            # refuse rather than guess at the nearest thing.
            raise HTTPException(403, "This API key has a scope Trove does not recognise.")
        if key.scope == "read_only" and request.method.upper() not in _READ_METHODS:
            raise HTTPException(403, "This API key is read-only.")
        now = utcnow()
        last = key.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        if last is None or now - last > _LAST_USED_RESOLUTION:
            key.last_used_at = now
            try:
                db.commit()
            except Exception:
                db.rollback()  # best effort; never fail a request over a timestamp
        request.state.api_key_id = key.id
        user = db.query(AuthUser).first()
        return user.username if user else "api"
    raise HTTPException(401, "That API key is not valid, or it has been revoked.")


def current_user(request: Request, db: Session = Depends(get_db)) -> str:
    """Dependency: the signed-in username, by cookie or API key, or a 401."""
    user = request.session.get(SESSION_USER_KEY)
    if user:
        return user
    presented = _presented_key(request)
    if presented:
        return _user_for_key(db, presented, request)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in."
    )


def session_user(request: Request) -> str:
    """Dependency: the signed-in username by cookie only.

    For what a person must be present for. An API key - however much access
    it has - is refused here with a sentence saying why, so a script that
    reaches for one of these learns the rule rather than a bare 401.
    """
    user = request.session.get(SESSION_USER_KEY)
    if user:
        return user
    if _presented_key(request):
        raise HTTPException(403, "This needs you signed in to Trove; an API key cannot do it.")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in."
    )


def authenticate(db: Session, username: str, password: str) -> AuthUser | None:
    user = db.query(AuthUser).filter(AuthUser.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def get_user(db: Session = Depends(get_db), username: str = Depends(current_user)) -> AuthUser:
    user = db.query(AuthUser).filter(AuthUser.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user
