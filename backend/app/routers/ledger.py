"""Offers, claims, runs and the dashboard summary.

The read side of the ledger. One router because the three tables are one story
and the UI reads them together.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import scheduler, settings_store
from ..adapters import ADAPTER_MAP
from ..auth import current_user
from ..config import get_settings
from ..crypto import decrypt
from ..db import get_db
from ..models import Account, Claim, Offer, Run
from ..runner import discover
from ..schemas import ClaimKey, ClaimRead, OfferRead, RunRead, Summary
from ..timeutil import utcnow

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
        .filter((Offer.ends_at.is_(None)) | (Offer.ends_at > now))
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
    """Ask every store what is free right now.

    Safe to press: this is the half of the app that touches no account and
    opens no browser, so it costs one HTTP request per store and cannot get
    anybody flagged. That is exactly why discovery is separate from claiming.
    """
    for store in ADAPTER_MAP:
        try:
            await discover(db, store)
        except Exception as exc:
            logger.warning("Could not read the offers for %s: %s", store, exc)
    return list_offers(current=True, db=db)


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
