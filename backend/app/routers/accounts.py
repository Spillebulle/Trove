"""Accounts, and the runs a person starts by hand."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from .. import notify, scheduler
from ..adapters import ADAPTER_MAP, get_adapter, known_stores
from ..auth import current_user
from ..browser import (
    NoLocalBrowser,
    ProfileBusy,
    find_chrome_executable,
    manager,
    profile_dir_for,
    purge_profile,
)
from ..config import get_settings
from ..crypto import encrypt
from ..db import get_db
from ..models import Account, Claim
from ..runner import check_session, run_account
from ..schemas import AccountCreate, AccountRead, AccountUpdate
from ..timeutil import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(current_user)])

settings = get_settings()


def serialise(db: Session, account: Account) -> AccountRead:
    claimed = (
        db.query(Claim.id)
        .filter(Claim.account_id == account.id, Claim.outcome == "claimed")
        .count()
    )
    return AccountRead(
        id=account.id,
        store=account.store,
        label=account.label,
        status=account.status,
        status_reason=account.status_reason,
        status_at=account.status_at,
        status_screenshot=account.status_screenshot,
        enabled=account.enabled,
        interval_hours=account.interval_hours,
        effective_interval_hours=scheduler.effective_interval_hours(account),
        last_run_at=account.last_run_at,
        next_run_at=account.next_run_at,
        has_totp=bool(account.totp_secret),
        notes=account.notes,
        created_at=account.created_at,
        claimed_count=claimed,
        busy_with=manager.who_holds(account.id),
    )


@router.get("/stores")
def list_stores() -> list[dict]:
    """Every store Trove can drive, and what each one needs.

    Read off the adapter registry rather than a list here, so adding an adapter
    adds it to the add-account page with no second edit.
    """
    return known_stores()


@router.get("", response_model=list[AccountRead])
def list_accounts(db: Session = Depends(get_db)) -> list[AccountRead]:
    accounts = db.query(Account).order_by(Account.created_at.asc()).all()
    return [serialise(db, account) for account in accounts]


@router.post("", response_model=AccountRead, status_code=201)
def create_account(body: AccountCreate, db: Session = Depends(get_db)) -> AccountRead:
    if body.store not in ADAPTER_MAP:
        raise HTTPException(400, f"Trove has no adapter for {body.store!r}.")
    if not body.label:
        raise HTTPException(400, "Give the account a name so you can tell it apart.")

    account = Account(
        store=body.store,
        label=body.label,
        # An account starts having never signed in, which is the truth and is
        # also what the interface uses to point the user at the one thing they
        # have to do next.
        status="never_signed_in",
        profile_path="pending",
        interval_hours=body.interval_hours,
        totp_secret=encrypt(body.totp_secret) if body.totp_secret else None,
        notes=body.notes,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    # The profile directory carries the id, so it can only be named once the
    # row exists. A second commit is cheaper than a UUID nobody can read.
    account.profile_path = profile_dir_for(account.id, account.label)
    db.commit()
    db.refresh(account)
    return serialise(db, account)


@router.get("/{account_id}", response_model=AccountRead)
def get_account(account_id: int, db: Session = Depends(get_db)) -> AccountRead:
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")
    return serialise(db, account)


@router.patch("/{account_id}", response_model=AccountRead)
def update_account(
    account_id: int, body: AccountUpdate, db: Session = Depends(get_db)
) -> AccountRead:
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")

    data = body.model_dump(exclude_unset=True)
    if "totp_secret" in data:
        value = data.pop("totp_secret")
        # An empty string clears it; a missing key leaves it alone.
        account.totp_secret = encrypt(value) if value else None
    if "interval_hours" in data and data["interval_hours"] is not None:
        if data["interval_hours"] < settings.min_interval_hours:
            raise HTTPException(
                400,
                f"The shortest interval Trove will run is "
                f"{settings.min_interval_hours} hour(s). Checking more often "
                "does not find giveaways sooner and does get accounts flagged.",
            )
    for field, value in data.items():
        setattr(account, field, value)

    if "interval_hours" in data:
        scheduler.schedule_next(account)
    db.commit()
    db.refresh(account)
    return serialise(db, account)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)) -> Response:
    """Delete the account, its ledger rows and its browser profile.

    The profile goes too, and that is the point of the confirmation the UI
    puts in front of this: the profile is the signed-in session, and deleting
    it is the only irreversible part. Everything else is a row.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")
    if manager.who_holds(account.id):
        raise HTTPException(
            409,
            "The browser profile for this account is open. Close the live view "
            "and try again.",
        )

    profile = settings.profiles_path / account.profile_path
    db.delete(account)
    db.commit()

    failed = purge_profile(profile)
    if failed:
        # The row is gone and that is what the user asked for. Files left behind
        # are disk, not a broken state, so log it and do not fail a request the
        # user already saw succeed. They are orphaned: nothing points at this
        # directory any more, and a new account gets a new name.
        logger.warning(
            "Deleted the account but could not fully remove %s: %s",
            profile,
            ", ".join(failed[:5]),
        )

    return Response(status_code=204)


@router.post("/{account_id}/run", status_code=202)
async def run_now(account_id: int, db: Session = Depends(get_db)) -> dict:
    """Run this account now, whatever the schedule says.

    Returns as soon as the run has started. A claim run takes minutes, and a
    request that waits for it is a request that times out somewhere between the
    browser and a reverse proxy.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")
    if account.status == "never_signed_in":
        raise HTTPException(
            409,
            "This account has never signed in. Open the live view and sign in "
            "first; Trove has no password to sign in with.",
        )
    holder = manager.who_holds(account_id)
    if holder:
        raise HTTPException(409, f"The browser profile is in use by {holder}.")

    task = asyncio.create_task(run_account(account_id, trigger="manual"))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return {"started": True, "account_id": account_id, "at": utcnow()}


@router.post("/{account_id}/sign-in-here", response_model=AccountRead)
async def sign_in_here(account_id: int, db: Session = Depends(get_db)) -> AccountRead:
    """Open the account's profile in an ordinary browser window on this machine.

    The recommended way to sign in, and the answer to a challenge the live view
    cannot pass. The live view streams over the DevTools protocol, a page can
    tell when that is attached, and a challenge that has decided a browser is
    automated will not take an answer from it however honestly a person clicks.
    So this window has no automation on it at all: Trove starts Chrome as a
    plain subprocess and then has no connection to it.

    Only useful where Trove and the person are at the same machine. In a
    container there is no screen to put a window on, and the live view remains
    the only option there.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")

    if not (settings.has_visible_desktop or settings.has_screen_view):
        raise HTTPException(
            409,
            "Trove has no screen to open a browser window on, so you would "
            "never see it. Sign in on a desktop and copy the profile across, "
            "or use the live view.",
        )

    adapter = get_adapter(account.store)
    profile = settings.profiles_path / account.profile_path
    # Read off the row now: the closure runs long after this request, and the
    # session it came from will be closed by then.
    label, store = account.label, account.store

    async def _verify() -> None:
        """When the window closes, go and look whether it worked.

        Trove has no connection to that window, so this is the only way it can
        know - and it must check rather than assume, because "signed in" is the
        flag that lets the scheduler start opening browsers on its own.
        """
        healthy, sentence = await check_session(account_id)
        await notify.send(
            "attention" if not healthy else "claimed",
            notify.Notification(
                title=f"{label} is signed in" if healthy else f"{label} is still signed out",
                detail=sentence,
                severity="good" if healthy else "caution",
                context=store,
            ),
        )

    try:
        await manager.open_local(account.id, profile, adapter.login_url, on_closed=_verify)
    except ProfileBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except NoLocalBrowser as exc:
        raise HTTPException(503, str(exc)) from exc

    db.refresh(account)
    return serialise(db, account)


@router.post("/{account_id}/check-session", response_model=AccountRead)
async def check_account_session(
    account_id: int, db: Session = Depends(get_db)
) -> AccountRead:
    """Ask the store whether this account is signed in, now.

    Normally the sign-in window closing triggers this on its own. It is also a
    button, for the cases where nothing was watching: Trove was restarted while
    the window was open, or the profile was signed in some other way. It costs
    one page load and claims nothing.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")

    healthy, sentence = await check_session(account_id)
    db.expire_all()
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:  # pragma: no cover - deleted mid-check
        raise HTTPException(404, "No such account.")
    if not healthy and "in use by" in sentence:
        raise HTTPException(409, sentence)
    return serialise(db, account)


@router.get("/{account_id}/can-sign-in-here")
def can_sign_in_here(account_id: int) -> dict:
    """Whether a normal browser window is possible here, and why not if it is not.

    Lets the interface disable the button with a sentence rather than offering
    something that cannot work: a control that lies is worse than none.
    """
    # Two ways it can be possible, and the interface needs to know which:
    # "desktop" means a window opens on the screen in front of the person;
    # "screen" means it opens on Trove's own display (the container's Xvfb)
    # and the person watches it through the screen view. The second is the
    # container's answer to a challenge the live view cannot pass, because the
    # window has nothing attached to it.
    via = (
        "desktop"
        if settings.has_visible_desktop
        else "screen"
        if settings.has_screen_view
        else None
    )
    if via is None:
        return {
            "ok": False,
            "via": None,
            "reason": (
                "Trove is running somewhere with no screen you could see a "
                "browser window on, usually a container. Sign in on a desktop "
                "and copy the profile across, or use the live view."
            ),
        }
    if find_chrome_executable() is None:
        return {
            "ok": False,
            "via": None,
            "reason": (
                "No Google Chrome on this machine. Install it, or use the live "
                "view."
            ),
        }
    return {"ok": True, "via": via, "reason": None}


@router.post("/{account_id}/close-sign-in", status_code=204)
def close_sign_in(account_id: int) -> Response:
    """Close the account's sign-in window from here.

    On a desktop the person closes the window themselves. On the container's
    screen there is no window manager, so there may be nothing to click, and
    this is the button instead. A polite terminate: Chrome writes the profile
    out on it, and the waiter that opened the window releases the lock and
    checks the session exactly as if it had been closed by hand.
    """
    if not manager.close_local(account_id):
        raise HTTPException(409, "There is no sign-in window open for this account.")
    return Response(status_code=204)


@router.post("/{account_id}/reset-profile", response_model=AccountRead)
def reset_profile(account_id: int, db: Session = Depends(get_db)) -> AccountRead:
    """Throw away the browser profile and start a fresh one.

    A profile accumulates a reputation as well as a session. Once a store's
    bot detection has decided it does not like one, it keeps not liking it: the
    challenge comes back however many times a person answers it, because what
    is being refused is the profile rather than the answer. A fresh profile from
    the same machine and the same address is usually waved straight through.

    So this is the escape hatch, and it is separate from deleting the account
    because it keeps the ledger, the interval and the name. What it costs is the
    signed-in session, which has to be done again by hand in the live view -
    which is why the interface puts a sentence saying so in front of it.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")
    holder = manager.who_holds(account_id)
    if holder:
        raise HTTPException(
            409,
            f"The browser profile is in use by {holder}. Close it and try again.",
        )

    failed = purge_profile(settings.profiles_path / account.profile_path)
    if failed:
        # Something still has a handle on part of the profile, and a half-emptied
        # one is worse than an intact one: Chromium may open it and find a
        # cookie store without the keys that decrypt it. Say what is stuck and
        # change nothing, so the account is still in a state the user knows.
        raise HTTPException(
            409,
            "Part of the browser profile could not be removed, so nothing was "
            f"changed ({', '.join(failed[:3])}). A browser window for this "
            "account may still be closing. Wait a moment and try again.",
        )

    account.status = "never_signed_in"
    account.status_reason = None
    account.status_screenshot = None
    account.status_at = utcnow()
    # A profile that has never signed in has nothing to run against, so the
    # next scheduled run would only fail. It is re-armed when the user signs in.
    account.next_run_at = None
    db.commit()
    db.refresh(account)
    return serialise(db, account)


@router.post("/{account_id}/clear-attention", response_model=AccountRead)
def clear_attention(account_id: int, db: Session = Depends(get_db)) -> AccountRead:
    """Say the account is fine again.

    The user is the authority here, not the app: they have just been in the
    live view and know whether the question was answered. Trove marks it `ok`
    and lets the next run be the real test.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(404, "No such account.")
    account.status = "ok"
    account.status_reason = None
    account.status_screenshot = None
    account.status_at = utcnow()
    scheduler.schedule_next(account)
    db.commit()
    db.refresh(account)
    return serialise(db, account)


# Tasks started by `run_now`, held so asyncio does not collect one mid-run.
_running: set[asyncio.Task] = set()
