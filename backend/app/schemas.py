"""What crosses the wire.

Read models are hand-written serialisers rather than `from_attributes`, for one
reason: several of them deliberately do *not* carry a column. A claim's key is
encrypted at rest and is fetched by its own endpoint when the user asks to see
it, so it must not ride along in every list response and end up in a log or a
browser cache.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Read(BaseModel):
    """The base every read model inherits, for one reason: time zones.

    SQLite has no datetime type. SQLAlchemy writes an ISO string and reads it
    back with no offset, so a value stored as aware UTC arrives here naive.
    Pydantic then serialises it without an offset, and `new Date(...)` in the
    browser reads a string with no offset as **local time**. On a machine two
    hours ahead of UTC that made a run which had just finished read as "2 hours
    ago", and a run scheduled in six hours read as four.

    It is fixed here rather than in each field or on the client because it is
    one rule about the boundary: everything Trove stores is UTC, so everything
    that leaves without an offset needs one put back. A value that already has
    one is left alone, so an aware column stays exactly as it was.
    """

    @model_validator(mode="after")
    def _stamp_utc(self):
        for name, value in self:
            if isinstance(value, datetime) and value.tzinfo is None:
                object.__setattr__(self, name, value.replace(tzinfo=timezone.utc))
        return self


# ── Auth ────────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class AuthStatus(BaseModel):
    authenticated: bool
    username: str | None = None


# ── Accounts ────────────────────────────────────────────────────────────────


class AccountCreate(BaseModel):
    store: str
    label: str = Field(min_length=1, max_length=120)
    interval_hours: int | None = None
    totp_secret: str | None = None
    notes: str | None = None

    @field_validator("label")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class AccountUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    interval_hours: int | None = None
    totp_secret: str | None = None
    # Write-only. An empty string clears; a missing key leaves it alone.
    login_email: str | None = None
    login_password: str | None = None
    notes: str | None = None


class TypeRequest(BaseModel):
    """What to type into the sign-in window on the container's screen."""

    what: Literal["email", "password", "code", "enter", "tab"]


class AccountRead(Read):
    id: int
    store: str
    label: str
    status: str
    status_reason: str | None
    status_at: datetime | None
    status_screenshot: str | None
    enabled: bool
    interval_hours: int | None
    effective_interval_hours: int
    last_run_at: datetime | None
    next_run_at: datetime | None
    # Whether a TOTP secret is stored, never the secret itself.
    has_totp: bool
    # A watched run is paused on a captcha waiting for the person on the screen.
    waiting_for_captcha: bool = False
    # The stored sign-in email, so the person can see which account it is; the
    # password only as a yes/no.
    login_email: str | None = None
    has_login_password: bool = False
    notes: str | None
    created_at: datetime | None
    # Counts for the account card, so the list page does not need one request
    # per account to say anything useful.
    claimed_count: int
    # Who currently has the browser profile open, if anybody.
    busy_with: str | None


# ── Offers ──────────────────────────────────────────────────────────────────


class OfferRead(Read):
    id: int
    store: str
    external_id: str
    title: str
    url: str | None
    image_url: str | None
    kind: str
    starts_at: datetime | None
    ends_at: datetime | None
    source: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    # Which of the user's accounts already has this, so the offer list can say
    # "claimed by two of three" without a second request.
    claimed_by: list[str] = []


# ── Claims ──────────────────────────────────────────────────────────────────


class ClaimRead(Read):
    id: int
    account_id: int | None
    account_label: str | None
    offer_id: int | None
    run_id: int | None
    store: str
    title: str
    outcome: str
    detail: str | None
    # Whether there is a key to reveal, never the key.
    has_key: bool
    key_store: str | None
    screenshot: str | None
    created_at: datetime | None


class ClaimKey(BaseModel):
    """The one response that carries a secret. Fetched only when asked for."""

    key_code: str
    key_store: str | None


# ── Runs ────────────────────────────────────────────────────────────────────


class RunRead(Read):
    id: int
    account_id: int | None
    account_label: str | None
    store: str
    status: str
    trigger: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    offers_seen: int
    claimed: int
    already_owned: int
    message: str | None


# ── Settings ────────────────────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    """A partial write. Only the keys present are changed.

    A secret sent back as the redaction placeholder means "leave it alone",
    which is what a form does when it never held the real value.
    """

    values: dict[str, object]


class NotificationTest(BaseModel):
    """Test an unsaved webhook, so the user can get it right before saving."""

    channel: str
    webhook_url: str | None = None


class TestResult(BaseModel):
    ok: bool
    message: str


# ── Dashboard ───────────────────────────────────────────────────────────────


class Summary(Read):
    """The figures on the dashboard tiles.

    Every one of them is a count of rows, not a derived rate. STYLE-GUIDE 7.14
    wants a tile to be one figure and what it is, and a figure nobody can
    reconcile against a list is a figure nobody trusts.
    """

    accounts: int
    accounts_needing_attention: int
    free_now: int
    claimed_total: int
    claimed_7d: int
    last_run_at: datetime | None
    scheduler_enabled: bool
    scheduler_running: bool
