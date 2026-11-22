"""API keys: issue, list, revoke.

Cookie-only (`session_user`), so a key can never mint another key or keep
itself alive - issuing access is something a person does while looking.

The plaintext is returned once, from the create call. Only its hash is stored,
so it cannot be shown again; losing it means issuing a new one. Revoking keeps
the row so its last use stays visible.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..auth import generate_api_key, session_user
from ..db import get_db
from ..models import ApiKey
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from ..timeutil import utcnow

router = APIRouter(prefix="/api/keys", tags=["api-keys"], dependencies=[Depends(session_user)])

# Enough to integrate with; few enough that a runaway script is obvious.
MAX_ACTIVE_KEYS = 20


def _read(key: ApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=key.id,
        name=key.name,
        scope=key.scope,
        prefix=key.prefix,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


@router.get("", response_model=list[ApiKeyRead])
def list_keys(db: Session = Depends(get_db)) -> list[ApiKeyRead]:
    """Every key issued, live ones first, newest first within each."""
    keys = db.query(ApiKey).order_by(ApiKey.revoked_at.is_not(None), ApiKey.id.desc()).all()
    return [_read(key) for key in keys]


@router.post("", response_model=ApiKeyCreated, status_code=201)
def create_key(body: ApiKeyCreate, db: Session = Depends(get_db)) -> ApiKeyCreated:
    live = db.query(ApiKey).filter(ApiKey.revoked_at.is_(None)).count()
    if live >= MAX_ACTIVE_KEYS:
        raise HTTPException(
            400,
            f"There are already {MAX_ACTIVE_KEYS} active keys. Revoke one before making another.",
        )
    raw, prefix, key_hash = generate_api_key()
    key = ApiKey(
        name=body.name.strip() or "API key",
        scope=body.scope,
        prefix=prefix,
        key_hash=key_hash,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return ApiKeyCreated(**_read(key).model_dump(), key=raw)


@router.delete("/{key_id}", status_code=204)
def revoke_key(key_id: int, db: Session = Depends(get_db)) -> Response:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if key is None:
        raise HTTPException(404, "No such key.")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        db.commit()
    return Response(status_code=204)
