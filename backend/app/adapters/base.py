"""The store adapter contract.

One adapter per store, behind a small interface, registered in one place
(`adapters/__init__.py`). HomeLab Manager's device adapters are the model,
including the idea that an adapter *declares what it needs* so the add-account
page can explain what it is asking for rather than presenting a form of
mysteries.

The contract is four methods and they are deliberately separate:

  `list_free_offers()`   what is free right now. Touches no account and no
                         session: it is a public endpoint or a public feed.
                         This is the boundary CLAUDE.md asks for, and it is
                         what lets the noisy half of the app run only when
                         there is something to claim.
  `health(page)`         is the stored session still signed in? Cheap, and it
                         is what turns "the claim failed" into the more useful
                         "this account is signed out".
  `is_owned(page, offer)` does the account already have it? The commonest
                         answer, and it must not read as a failure.
  `claim(page, offer)`   one attempt. Returns a result or raises
                         `NeedsAttention`. Never retries, never loops.

An adapter may not import the database, the scheduler or FastAPI. It is handed
a page and an offer and it answers about a store. That keeps the store-specific
knowledge - which is the part that rots as stores change - in one file per
store with nothing else tangled into it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from playwright.async_api import Page


@dataclass(slots=True)
class FreeOffer:
    """Something that is free right now, as the adapter sees it.

    `external_id` must be stable for the life of the promotion, because it is
    what stops the app claiming the same thing twice. Epic's namespace and
    offer id together are stable; a title is not, and a URL slug is not.
    """

    external_id: str
    title: str
    url: str | None = None
    image_url: str | None = None
    kind: str = "game"
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source: str = "store"
    # Anything the adapter needs at claim time that is not worth a column.
    # Epic puts its namespace and offer id here so `claim` does not have to
    # parse them back out of `external_id`.
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class ClaimResult:
    """What one attempt came to.

    `outcome` is one of `models.CLAIM_OUTCOMES`. `detail` is the sentence the
    ledger row shows, written for a person: "Already in your library." rather
    than "ownership_check returned true".
    """

    outcome: str
    detail: str | None = None
    key_code: str | None = None
    key_store: str | None = None
    screenshot: str | None = None


@dataclass(slots=True)
class Requirement:
    """Something an account needs before this adapter can work.

    Rendered on the add-account page, so it is written as a sentence to a
    person rather than as a field name. HomeLab's adapters declare their
    service requirements the same way and for the same reason.
    """

    name: str
    description: str
    required: bool = True


class BaseAdapter(ABC):
    """One store.

    Subclasses are stateless: everything they need arrives as an argument. That
    is what allows one instance per run without a lifecycle to get wrong.
    """

    # The key this adapter is registered under, and the word stored in
    # `Account.store`. Never reworded.
    store: str = ""
    # What the store is called in the interface. Sentence case (STYLE-GUIDE
    # 12), because it is a label and not a title.
    display_name: str = ""
    # Where a person goes to sign in by hand, opened by the live view. The
    # first thing a new account does.
    login_url: str = ""
    # The store's actual sign-in page, opened by the sign-in window. Separate
    # from `login_url` (the store front, which `health` reads) because a person
    # who pressed "Sign in here" wants the login form, not the shop - and the
    # assisted sign-in has to start there. Falls back to `login_url`.
    signin_url: str = ""

    @property
    def sign_in_page(self) -> str:
        return self.signin_url or self.login_url
    # A sentence for the add-account page saying what this adapter does and
    # does not do.
    blurb: str = ""

    REQUIREMENTS: list[Requirement] = []

    def requirements(self) -> list[Requirement]:
        return list(self.REQUIREMENTS)

    @abstractmethod
    async def list_free_offers(self) -> list[FreeOffer]:
        """What is free right now. No account, no session, no browser."""

    @abstractmethod
    async def health(self, page: Page) -> tuple[bool, str]:
        """Is the stored session still signed in? Returns (ok, a sentence)."""

    @abstractmethod
    async def is_owned(self, page: Page, offer: FreeOffer) -> bool | None:
        """Does this account already have it?

        None means the adapter could not tell, which is a different answer from
        False and must not be turned into one: claiming something the account
        already owns is harmless, but recording "not owned" when the check
        failed teaches the ledger a lie.
        """

    @abstractmethod
    async def claim(self, page: Page, offer: FreeOffer) -> ClaimResult:
        """One attempt. Raises `NeedsAttention` when only a person can go on."""
