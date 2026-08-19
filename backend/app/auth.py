"""One user, a cookie, a bcrypt password.

Trove is a single-user self-hosted app, so there is no registration, no roles
and no password reset by email. The user is bootstrapped on first start and can
change the password from Settings.

The session is a signed cookie via Starlette's SessionMiddleware, keyed off
`secret_key` from config. Losing that secret logs the user out and nothing
worse, which is why it can be generated on first boot without ceremony.
"""
from __future__ import annotations

import logging
import os

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .models import AuthUser

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


def current_user(request: Request) -> str:
    """Dependency: the signed-in username, or a 401."""
    user = request.session.get(SESSION_USER_KEY)
    if user:
        return user
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
