"""The database: one SQLite file, synchronous SQLAlchemy.

Synchronous rather than async, which is the choice HomeLab Manager made and
Tally did not. The reason here is Playwright: a claim run spends its time
awaiting a browser, not a database, and the queries around it are single-row
reads on a table with tens of rows. An async engine would buy nothing and cost
a greenlet dependency and two ways to write every query.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_engine(
    settings.database_url,
    # The scheduler's per-account tasks and the request handlers share this
    # engine across threads. SQLite's own guard assumes one thread per
    # connection; the pool already hands out one connection at a time.
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_wal_warned = False


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Per-connection tuning, applied as the pool hands out a connection.

    - `journal_mode=WAL` so a claim run writing its ledger row does not block
      the UI polling the run list.
    - `busy_timeout=10000` because a live-view session and a scheduled run can
      contend, and SQLite's 5 s default has been short in exactly that case.
    - `synchronous=NORMAL` is WAL's own default; stated so it is not a mystery.
    """
    cursor = dbapi_connection.cursor()
    row = cursor.execute("PRAGMA journal_mode=WAL").fetchone()
    global _wal_warned
    if row and str(row[0]).lower() != "wal" and not _wal_warned:
        _wal_warned = True
        logger.warning(
            "SQLite journal_mode is %r rather than WAL. Writers will block "
            "readers. Is the database on a filesystem that cannot do WAL?",
            row[0],
        )
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401 - registers the tables

    Base.metadata.create_all(bind=engine)
    _migrate(engine)


def _migrate(engine) -> None:
    """Add columns to tables that already exist.

    `create_all` creates missing *tables* and never alters an existing one, and
    SQLite has no `ADD COLUMN IF NOT EXISTS`. So every column added after a
    release goes in this table, checked against `PRAGMA table_info` first.
    Idempotent, and safe to run on every start.

    The first entries are the sign-in details added in 0.1.7; a 0.1.6 database
    opened by a later build gets them on the first start.
    """
    additions: dict[str, dict[str, str]] = {
        "accounts": {
            "login_email": "TEXT",
            "login_password": "TEXT",
        },
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = [
                r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            ]
            if not existing:
                continue  # the table does not exist yet; create_all made it whole
            for name, decl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {decl}"))
                    logger.warning("%s: added the %s column on startup", table, name)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
