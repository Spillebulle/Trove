"""Offers, claims, runs and the dashboard summary.

The read side of the ledger. One router because the three tables are one story
and the UI reads them together.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import scheduler, settings_store
from ..adapters import ADAPTER_MAP
from ..auth import current_user
from ..config import get_settings
from ..crypto import decrypt
from ..db import get_db
from ..models import Account, Claim, Offer, Run
from ..runner import discover, discover_upcoming
from ..schemas import ClaimKey, ClaimRead, OfferRead, RunRead, Summary
from ..timeutil import as_utc, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ledger"], dependencies=[Depends(current_user)])

settings = get_settings()


# ── Dashboard ───────────────────────────────────────────────────────────────


@router.get("/summary", response_model=Summary)
def summary(db: Session = Depends(get_db)) -> Summary:
    now = utcnow()
    accounts = db.query(Account).all()
    free_now = (
        db.query(Offer.id)
        .filter(
            (Offer.ends_at.is_(None)) | (Offer.ends_at > now),
            # An announced offer is in the table too now, and "free now" must
            # not count next week: a start in the future means not yet free.
            (Offer.starts_at.is_(None)) | (Offer.starts_at <= now),
        )
        .count()
    )
    claimed_total = db.query(Claim.id).filter(Claim.outcome == "claimed").count()
    claimed_7d = (
        db.query(Claim.id)
        .filter(Claim.outcome == "claimed", Claim.created_at > now - timedelta(days=7))
        .count()
    )
    last_run = db.query(func.max(Run.finished_at)).scalar()
    state = scheduler.status()
    return Summary(
        accounts=len(accounts),
        accounts_needing_attention=sum(
            1 for account in accounts if account.status in ("needs_attention", "never_signed_in")
        ),
        free_now=free_now,
        claimed_total=claimed_total,
        claimed_7d=claimed_7d,
        last_run_at=last_run,
        scheduler_enabled=settings_store.get(db, "schedule.enabled"),
        scheduler_running=state["running"],
    )


# ── Offers ──────────────────────────────────────────────────────────────────


def _serialise_offer(db: Session, offer: Offer) -> OfferRead:
    labels = (
        db.query(Account.label)
        .join(Claim, Claim.account_id == Account.id)
        .filter(
            Claim.offer_id == offer.id,
            Claim.outcome.in_(("claimed", "already_owned")),
        )
        .distinct()
        .all()
    )
    # Why it is unclaimed, when there is a reason worth showing. Asked only
    # when nothing has claimed it - an offer one account owns is not blocked -
    # and taken from the most recent tagged row, because an account fixed since
    # (bought the base game, say) should not keep being described by its
    # oldest failure.
    blocked = None
    if not labels:
        blocked = (
            db.query(Claim)
            .filter(
                Claim.offer_id == offer.id,
                Claim.outcome == "not_eligible",
                Claim.reason_code.isnot(None),
            )
            .order_by(Claim.created_at.desc())
            .first()
        )

    return OfferRead(
        id=offer.id,
        store=offer.store,
        external_id=offer.external_id,
        title=offer.title,
        url=offer.url,
        image_url=offer.image_url,
        kind=offer.kind,
        starts_at=offer.starts_at,
        ends_at=offer.ends_at,
        source=offer.source,
        first_seen_at=offer.first_seen_at,
        last_seen_at=offer.last_seen_at,
        claimed_by=[row[0] for row in labels],
        blocked_reason=blocked.reason_code if blocked else None,
        blocked_detail=blocked.detail if blocked else None,
    )


@router.get("/offers", response_model=list[OfferRead])
def list_offers(
    current: bool = Query(True, description="Only offers that have not ended."),
    db: Session = Depends(get_db),
) -> list[OfferRead]:
    query = db.query(Offer)
    if current:
        now = utcnow()
        query = query.filter((Offer.ends_at.is_(None)) | (Offer.ends_at > now))
    offers = query.order_by(Offer.ends_at.asc().nullslast(), Offer.title.asc()).all()
    return [_serialise_offer(db, offer) for offer in offers]


@router.post("/offers/refresh", response_model=list[OfferRead])
async def refresh_offers(db: Session = Depends(get_db)) -> list[OfferRead]:
    """Ask every store what is free right now, and what is announced.

    Safe to press: this is the half of the app that touches no account and
    opens no browser, so it costs a couple of HTTP requests per store and
    cannot get anybody flagged. That is exactly why discovery is separate from
    claiming. The upcoming list rides along so the calendar refreshes with the
    same button; it is recorded only, never claimed.
    """
    for store in ADAPTER_MAP:
        try:
            await discover(db, store)
            await discover_upcoming(db, store)
        except Exception as exc:
            logger.warning("Could not read the offers for %s: %s", store, exc)
    return list_offers(current=True, db=db)


# ── Calendar ────────────────────────────────────────────────────────────────
#
# The offers as an iCalendar file. Hand-written rather than a dependency: it is
# two event shapes and a page of RFC 5545, and a library for that is a library
# to keep current forever.


def _ics_text(value: str) -> str:
    """Escape a text value the way RFC 5545 3.3.11 asks."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ics_time(value: datetime) -> str:
    """A UTC timestamp in the calendar's own shape, `20260828T150000Z`."""
    return (as_utc(value) or value).strftime("%Y%m%dT%H%M%SZ")


def _ics_fold(line: str) -> list[str]:
    """Fold a long content line at 74 octets with a space continuation.

    The RFC caps a line at 75 octets. Folding is done on the UTF-8 bytes -
    a multi-byte character split in half is not a character any more - by
    backing the cut off until it lands on a codepoint boundary.
    """
    data = line.encode("utf-8")
    out: list[str] = []
    prefix = b""
    while len(prefix) + len(data) > 74:
        cut = 74 - len(prefix)
        while cut > 1 and (data[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append((prefix + data[:cut]).decode("utf-8"))
        data = data[cut:]
        prefix = b" "
    out.append((prefix + data).decode("utf-8"))
    return out


def _store_name(store: str) -> str:
    adapter = ADAPTER_MAP.get(store)
    return adapter.display_name if adapter and adapter.display_name else store


def _vevent(uid: str, start: datetime, end: datetime, summary: str, url: str | None) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_ics_time(utcnow())}",
        f"DTSTART:{_ics_time(start)}",
        f"DTEND:{_ics_time(end)}",
        f"SUMMARY:{_ics_text(summary)}",
    ]
    if url:
        lines.append(f"URL:{_ics_text(url)}")
    lines.append("END:VEVENT")
    return lines


@router.get("/offers/calendar.ics")
def offer_calendar(db: Session = Depends(get_db)) -> Response:
    """The announced giveaways, and the deadlines, as a calendar file.

    An upcoming offer becomes an event over its window (a week from the start
    when the store gave no end, which is Epic's cadence); a currently-free
    offer with an end date becomes an event at the deadline, because the
    deadline is the part a calendar is actually for.

    A download behind the session cookie like every other endpoint, on
    purpose: a calendar app *subscribing* to a URL sends no cookie, so a feed
    would have to be unauthenticated or carry its own token, and neither is
    designed. Until a tokened feed exists, this is a file the person saves
    and imports.
    """
    now = utcnow()
    offers = (
        db.query(Offer)
        .filter((Offer.ends_at.is_(None)) | (Offer.ends_at > now))
        .order_by(Offer.starts_at.asc().nullsfirst(), Offer.title.asc())
        .all()
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Trove//Offers//EN",
        "CALSCALE:GREGORIAN",
    ]
    for offer in offers:
        starts = as_utc(offer.starts_at)
        ends = as_utc(offer.ends_at)
        summary = f"{offer.title} free on {_store_name(offer.store)}"
        if starts is not None and starts > now:
            lines += _vevent(
                uid=f"offer-{offer.id}@trove",
                start=starts,
                end=ends or (starts + timedelta(days=7)),
                summary=summary,
                url=offer.url,
            )
        elif ends is not None:
            # Free now, so what is left to put on a calendar is the deadline.
            lines += _vevent(
                uid=f"offer-{offer.id}-ends@trove",
                start=ends,
                end=ends,
                summary=f"{summary} ends",
                url=offer.url,
            )

    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _ics_fold(line)]
    return Response(
        content="\r\n".join(folded) + "\r\n",
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="trove-offers.ics"'},
    )


# ── Claims ──────────────────────────────────────────────────────────────────


def _serialise_claim(claim: Claim, label: str | None) -> ClaimRead:
    return ClaimRead(
        id=claim.id,
        account_id=claim.account_id,
        account_label=label,
        offer_id=claim.offer_id,
        run_id=claim.run_id,
        store=claim.store,
        title=claim.title,
        kind=claim.kind or "game",
        image_url=claim.image_url,
        outcome=claim.outcome,
        detail=claim.detail,
        has_key=bool(claim.key_code),
        key_store=claim.key_store,
        screenshot=claim.screenshot,
        created_at=claim.created_at,
    )


@router.get("/claims", response_model=list[ClaimRead])
def list_claims(
    account_id: int | None = None,
    outcome: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[ClaimRead]:
    query = db.query(Claim, Account.label).outerjoin(Account, Claim.account_id == Account.id)
    if account_id is not None:
        query = query.filter(Claim.account_id == account_id)
    if outcome:
        query = query.filter(Claim.outcome == outcome)
    rows = (
        query.order_by(Claim.created_at.desc(), Claim.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialise_claim(claim, label) for claim, label in rows]


@router.get("/claims/{claim_id}/key", response_model=ClaimKey)
def reveal_key(claim_id: int, db: Session = Depends(get_db)) -> ClaimKey:
    """The one endpoint that returns a secret, and only when asked.

    A key is encrypted at rest and is deliberately absent from the list
    response. It comes out here, one row at a time, on a deliberate action, so
    it is not sitting in every browser cache and every proxy log that ever saw
    the claims page.
    """
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if claim is None:
        raise HTTPException(404, "No such claim.")
    if not claim.key_code:
        raise HTTPException(404, "This claim has no key.")
    value = decrypt(claim.key_code)
    if value is None:
        raise HTTPException(
            500,
            "The key could not be decrypted. The encryption key has changed "
            "since it was stored.",
        )
    return ClaimKey(key_code=value, key_store=claim.key_store)


# ── Runs ────────────────────────────────────────────────────────────────────


@router.get("/runs", response_model=list[RunRead])
def list_runs(
    account_id: int | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
) -> list[RunRead]:
    query = db.query(Run, Account.label).outerjoin(Account, Run.account_id == Account.id)
    if account_id is not None:
        query = query.filter(Run.account_id == account_id)
    rows = query.order_by(Run.started_at.desc(), Run.id.desc()).limit(limit).all()
    return [
        RunRead(
            id=run.id,
            account_id=run.account_id,
            account_label=label,
            store=run.store,
            status=run.status,
            trigger=run.trigger,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_s=run.duration_s,
            offers_seen=run.offers_seen,
            claimed=run.claimed,
            already_owned=run.already_owned,
            message=run.message,
        )
        for run, label in rows
    ]


# ── Screenshots ─────────────────────────────────────────────────────────────


@router.get("/screenshots/{name}")
def screenshot(name: str) -> FileResponse:
    """The evidence for an attention item.

    `name` is user-supplied by the time it reaches here, so it is resolved and
    then checked for containment. Without that check this endpoint reads any
    file the process can, which in this app includes the encryption key that
    decrypts every stored game key and TOTP secret.
    """
    root = settings.screenshots_path.resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise HTTPException(404, "No such screenshot.")
    return FileResponse(candidate, media_type="image/png")
