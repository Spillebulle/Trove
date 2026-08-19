"""Settings, sign-in, and the notification test.

Settings apply live and there is no Save button (STYLE-GUIDE 9), so this is a
PATCH of whatever changed rather than a submit of the whole page.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import notify, scheduler, settings_store
from ..auth import (
    SESSION_USER_KEY,
    authenticate,
    current_user,
    hash_password,
    verify_password,
)
from ..db import get_db
from ..models import AuthUser
from ..schemas import (
    AuthStatus,
    LoginRequest,
    NotificationTest,
    PasswordChange,
    SettingsUpdate,
    TestResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])


# ── Sign in ─────────────────────────────────────────────────────────────────


@router.get("/auth/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    user = request.session.get(SESSION_USER_KEY)
    return AuthStatus(authenticated=bool(user), username=user)


@router.post("/auth/login", response_model=AuthStatus)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> AuthStatus:
    user = authenticate(db, body.username, body.password)
    if user is None:
        # One message for both a wrong name and a wrong password. There is
        # exactly one account, so naming which half was wrong tells an attacker
        # the username and nothing else.
        raise HTTPException(401, "That username and password do not match.")
    request.session[SESSION_USER_KEY] = user.username
    return AuthStatus(authenticated=True, username=user.username)


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=204)


@router.post("/auth/password", status_code=204)
def change_password(
    body: PasswordChange,
    username: str = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    user = db.query(AuthUser).filter(AuthUser.username == username).first()
    if user is None:
        raise HTTPException(401, "Not signed in.")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Your current password is not right.")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return Response(status_code=204)


# ── Settings ────────────────────────────────────────────────────────────────


@router.get("/settings", dependencies=[Depends(current_user)])
def read_settings(db: Session = Depends(get_db)) -> dict:
    values = settings_store.get_all(db)
    return {"values": values, "scheduler": scheduler.status()}


@router.patch("/settings", dependencies=[Depends(current_user)])
def write_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    unknown = set(body.values) - set(settings_store.DEFAULTS)
    if unknown:
        raise HTTPException(400, f"Unknown setting(s): {', '.join(sorted(unknown))}.")
    settings_store.set_many(db, body.values)
    return {"values": settings_store.get_all(db), "scheduler": scheduler.status()}


@router.post("/settings/notify/test", response_model=TestResult,
             dependencies=[Depends(current_user)])
async def test_notification(
    body: NotificationTest, db: Session = Depends(get_db)
) -> TestResult:
    """Send one test message.

    Takes the webhook in the request rather than reading the saved one, so the
    user can check a URL before committing it. An empty URL falls back to the
    saved one, which is what the button does once a webhook is already set up.
    """
    url = body.webhook_url
    if not url or url == settings_store.REDACTED:
        url = settings_store.get(db, "notify.webhook_url")
    if not url:
        return TestResult(ok=False, message="There is no webhook URL to send to.")

    ok, message = await notify.post(
        body.channel,
        url,
        notify.Notification(
            title="Trove is wired up",
            detail=(
                "This is a test message. Claims, accounts needing a hand and "
                "failures will arrive here."
            ),
            severity="good",
        ),
    )
    return TestResult(ok=ok, message=message)
