"""The tables.

Four things are stored and the shape of the app follows from them:

  Account   a store login the user owns, and the browser profile that keeps it
            signed in. Its `status` is what the attention queue is a view of.
  Offer     something that is free right now, discovered without touching any
            account. Shared across accounts: one Epic giveaway is one row.
  Claim     one attempt on one offer by one account. The ledger. Every attempt
            is a row, including the boring ones, because CLAUDE.md's rule is
            that the app never claims a game it cannot show a row for.
  Run       one visit to one store by one account. Groups the claims it made
            and holds the failure if the visit itself failed.

`Setting` is a small key/value table for the things that are global rather than
per-account (the webhook, which events notify). `AuthUser` is the single user.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base
from .timeutil import utcnow

# The vocabularies. Stored words, chosen once. They reach the API and the UI,
# so renaming one is a migration and not a rewording. STYLE-GUIDE 3.2 makes
# the same point about theme ids.

# What an account is: healthy, waiting for a person, or switched off.
ACCOUNT_STATUSES = ("ok", "needs_attention", "never_signed_in", "disabled")

# What one attempt came to. The five CLAUDE.md names, and nothing else.
#
# `already_owned` is deliberately not a failure: it is the normal steady state
# of a claimer that is working, and the UI says so quietly rather than in
# `critical`.
CLAIM_OUTCOMES = (
    "claimed",
    "already_owned",
    "not_eligible",
    "needs_attention",
    "failed",
)

# How a run ended. `attention` means the run stopped and filed the account,
# which is a different thing from the run erroring.
RUN_STATUSES = ("running", "ok", "attention", "failed", "cancelled")


class AuthUser(Base):
    """The single user. Cookie session, bcrypt password, no registration."""

    __tablename__ = "auth_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Account(Base):
    """One store login, and the browser profile that keeps it signed in.

    There is no password column and there never will be. The app's answer to a
    hostile login is a session it was handed once by a person, so what is
    stored is `profile_path`, a directory of cookies, local storage and a
    device fingerprint, and never a credential it could replay.
    """

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    # Which adapter drives this account. A key in ADAPTER_MAP.
    store = Column(String(40), nullable=False, index=True)
    # What the user calls it. Not the store username: the app never asks for
    # one, and a person with two Epic accounts needs to tell them apart.
    label = Column(String(120), nullable=False)

    status = Column(String(30), nullable=False, default="never_signed_in")
    # Why it needs attention, in a sentence, shown verbatim in the UI.
    status_reason = Column(Text, nullable=True)
    status_at = Column(DateTime, nullable=True)
    # The screenshot taken when the run stopped, relative to the screenshots
    # directory. The evidence for `status_reason`.
    status_screenshot = Column(String(300), nullable=True)

    # The persistent browser profile directory, relative to the profiles path.
    # One per account, never shared: CLAUDE.md forbids credential sharing
    # between accounts and a shared profile is exactly that.
    profile_path = Column(String(200), nullable=False)

    enabled = Column(Boolean, nullable=False, default=True)
    # Hours between runs. Null takes the global default.
    interval_hours = Column(Integer, nullable=True)

    last_run_at = Column(DateTime, nullable=True)
    # When the scheduler intends to run this next, jitter already applied.
    # Persisted rather than computed so a restart does not re-roll the jitter
    # and bunch every account onto the same minute.
    next_run_at = Column(DateTime, nullable=True)

    # An optional user-supplied TOTP secret, encrypted. CLAUDE.md allows this
    # one concession and no more: it is not a "solve 2FA for me" flow, it is
    # the user choosing to let the app type a code it was given.
    totp_secret = Column(Text, nullable=True)
    # The store sign-in details, encrypted, and **only ever typed into the
    # sign-in window on the container's screen at the user's request** - never
    # used to log in unattended. CLAUDE.md's rule stands: a claim run signs in
    # with nothing, and a login with a stored password on a schedule is exactly
    # what bot detection looks for. These exist so that a person who has to
    # answer a captcha in the screen view does not then also have to type an
    # email and a generated password key by key through a remote picture.
    login_email = Column(Text, nullable=True)
    login_password = Column(Text, nullable=True)

    # The external id of an offer whose checkout stopped on a captcha the driven
    # browser cannot pass, so it must be finished in the un-driven sign-in
    # window. Set when a run raises `CheckoutBlocked`, cleared the moment the
    # account is confirmed to own that offer. It is what lights the "Finish the
    # claim here" button, so it names the exact offer that window should open on.
    checkout_offer = Column(String(200), nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    claims = relationship("Claim", back_populates="account", cascade="all, delete-orphan")
    runs = relationship("Run", back_populates="account", cascade="all, delete-orphan")


class Offer(Base):
    """Something that is free right now.

    Discovery is separate from claiming (CLAUDE.md): a public feed or a store's
    own public promotions endpoint can fill this table without touching an
    account, which is what keeps the noisy, session-consuming half from running
    when there is nothing to claim.

    One row per offer per store, shared by every account on that store.
    """

    __tablename__ = "offers"
    __table_args__ = (UniqueConstraint("store", "external_id", name="uq_offer_store_id"),)

    id = Column(Integer, primary_key=True)
    store = Column(String(40), nullable=False, index=True)
    # The store's own id for the offer. Epic's is namespace:offerId.
    external_id = Column(String(200), nullable=False)
    title = Column(String(300), nullable=False)
    url = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    # What claiming it actually gets you: a game, a piece of downloadable
    # content, or a key. A Prime offer that is a key for another store is not
    # a library add and the ledger has to be able to say so.
    kind = Column(String(20), nullable=False, default="game")

    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)

    # Where this row came from: a public aggregator feed, or the store's own
    # public endpoint. Kept so a wrong offer can be traced to its source.
    source = Column(String(40), nullable=False, default="store")

    first_seen_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)

    claims = relationship("Claim", back_populates="offer")


class Run(Base):
    """One visit to one store by one account."""

    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    store = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False, default="running")

    # Was this the scheduler's idea or the user's? A manual run ignores the
    # interval, so the distinction is worth keeping in the log.
    trigger = Column(String(20), nullable=False, default="schedule")

    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    # Seconds. Stored rather than derived, so a run that never finished because
    # the process was killed does not read as instantaneous.
    duration_s = Column(Float, nullable=True)

    offers_seen = Column(Integer, nullable=False, default=0)
    claimed = Column(Integer, nullable=False, default=0)
    already_owned = Column(Integer, nullable=False, default=0)

    # The sentence shown against a failed or attention run.
    message = Column(Text, nullable=True)

    account = relationship("Account", back_populates="runs")
    claims = relationship("Claim", back_populates="run")


class Claim(Base):
    """One attempt on one offer by one account. The ledger row.

    The UI is a view of this table. Every attempt lands here, including
    `already_owned`, which is most of them once the app has been running a
    week.
    """

    __tablename__ = "claims"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    # Nullable: a run can fail before it knows which offer it was working on,
    # and that attempt still deserves a row.
    offer_id = Column(Integer, ForeignKey("offers.id"), nullable=True, index=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=True, index=True)

    store = Column(String(40), nullable=False, index=True)
    # Copied from the offer rather than joined. An offer row can be pruned when
    # the promotion is long over; the ledger is permanent and has to keep
    # reading properly on its own.
    title = Column(String(300), nullable=False)

    outcome = Column(String(30), nullable=False, index=True)
    detail = Column(Text, nullable=True)

    # A key handed out instead of a library add, which Prime Gaming does often.
    # Encrypted: it is worth money to whoever reads the database.
    key_code = Column(Text, nullable=True)
    # Which store the key is redeemed on, when that is not the claiming store.
    key_store = Column(String(40), nullable=True)

    # Relative to the screenshots directory. Present on `needs_attention` and
    # usually on `failed`: the evidence for what the page was doing.
    screenshot = Column(String(300), nullable=True)

    created_at = Column(DateTime, default=utcnow, index=True)

    account = relationship("Account", back_populates="claims")
    offer = relationship("Offer", back_populates="claims")
    run = relationship("Run", back_populates="claims")


class Setting(Base):
    """Global settings, as a key/value table.

    A table rather than columns on a singleton row: the settings page grows a
    key at a time and a migration per toggle is a migration nobody writes.
    Values are JSON text. Secrets among them are encrypted by the caller, not
    by this table, so it is obvious at the call site which ones are secret.
    """

    __tablename__ = "settings"

    key = Column(String(80), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
