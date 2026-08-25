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
from typing import Awaitable, Callable, Protocol

from playwright.async_api import Page


class ChallengeWaiter(Protocol):
    """How a watched run waits out a captcha the person solves on the screen.

    `wait` is handed a coroutine that returns True once the challenge is gone;
    it blocks - keeping the browser open on the screen - until that happens,
    the person gives up, or a cap passes, raising `NeedsAttention` in the last
    two cases. The adapter owns *detecting* its own challenge; the runner owns
    the *waiting* and the state a UI reads. This keeps store selectors out of
    the runner and run state out of the adapter.
    """

    async def wait(
        self, is_cleared: Callable[[], Awaitable[bool]], image_name: str | None = None
    ) -> None: ...


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

    `reason_code` is the same answer as a word rather than a sentence, for the
    interface to act on - `not_eligible` covers several situations that read
    identically on a card and are not the same news. Optional: an adapter that
    has nothing particular to say leaves it `None` and the interface falls back
    to the general wording. The codes an adapter may use live beside the
    branches that raise them, and the labels are in the frontend's
    `BLOCKED_LABEL`.
    """

    outcome: str
    detail: str | None = None
    reason_code: str | None = None
    key_code: str | None = None
    key_store: str | None = None
    screenshot: str | None = None


@dataclass(slots=True)
class BaseGame:
    """The game a piece of downloadable content belongs to.

    A DLC is only worth anything if the account owns the game it extends, so a
    free DLC has a prerequisite that a free game does not. This is what an
    adapter reports back about that prerequisite, and every field that could not
    be established is `None` rather than a guess - the runner's rule is that it
    only acts on what is known and explains itself when it is not.

    `owned` and `free` are three-valued on purpose. `None` means "could not
    tell", which must never be collapsed into `False`: skipping a claim because
    the check failed is a different thing from skipping it because the account
    genuinely lacks a paid game, and only the second is worth telling the user
    about as a settled fact.

    `offer` is the base game as a claimable offer, present only when the
    adapter worked out how to claim it. Without it the base game can be
    reported on but not bought, which is still useful: it is the difference
    between "you need X" and "you need X, and Trove is getting it for you".
    """

    title: str
    url: str | None = None
    owned: bool | None = None
    free: bool | None = None
    # What the store says it costs, for the sentence a person reads when a DLC
    # is skipped: "Albion Online (£15.99)" beats "the base game".
    price_note: str | None = None
    offer: "FreeOffer | None" = None


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


# What `POST /accounts/{id}/type` knows how to put into the sign-in window.
# The API declares this again as a Literal for its request body; this copy is
# the adapters' own, so that base.py needs nothing from the HTTP layer.
TYPEABLE = frozenset({"email", "password", "code", "enter", "tab"})


@dataclass(frozen=True)
class SignInStep:
    """One step of the assisted sign-in, as the store's own form wants it.

    The assisted sign-in types into a window with *nothing attached to it* -
    that is the whole point of the un-driven window - so Trove cannot read the
    page to find out what it is looking at. It types blind, waits a beat, and
    types again. That makes the order of the steps a fact about the store's
    login form, and a fact one store's form does not share with another's:
    Epic asks for the email, takes a Continue, and only then shows a password
    field, while Steam puts both on one page where an Enter after the username
    submits the form with no password in it.

    So the steps are declared per adapter, in a `SignInForm`, rather than
    written once against whichever store happened to be built first.

    `action` is one of the things `POST /accounts/{id}/type` can type -
    `email`, `password`, `code`, `enter`, `tab` - `label` is the sentence the
    screen shows while it happens, and `wait_ms` is how long to wait after it
    before the next step. The waits are the only thing standing in for reading
    the page, so a step that navigates needs a generous one.
    """

    action: str
    label: str
    wait_ms: int = 400

    def __post_init__(self) -> None:
        # A misspelled action would not fail here but at the far end, as a 422
        # from the type endpoint in the middle of a half-typed login form -
        # which is the failure this whole change exists to prevent. Fail at
        # import instead, where it is a typo and not an account.
        if self.action not in TYPEABLE:
            raise ValueError(
                f"{self.action!r} is not something Trove can type; "
                f"expected one of {', '.join(sorted(TYPEABLE))}"
            )


@dataclass(frozen=True)
class SignInForm:
    """One shape a store's login form can take, and the steps it wants.

    A store does not have *one* form. GOG shows an email field and a password
    field to somebody it has never seen, and to somebody it remembers it shows
    the account's name, one password field and a "Change account" link -
    measured, 25 Aug 2026, both screenshotted. Typing the first form's steps
    into the second puts the email address in the password box.

    Trove cannot tell which one is on the screen. That is not a gap to close:
    the window has nothing attached to it, which is the entire reason the
    store's captcha trusts it, and attaching something to read the page would
    trade the working sign-in for a detection. **The person is looking at the
    form.** So each shape is a button, and they press the one they can see -
    the same division of labour as everywhere else here, where the automation
    stops at the thing only a human can do and does not guess at it.

    `label` names the shape on the button, `description` says what to look for
    (and what to click first, since Trove types wherever focus already is), and
    `steps` is the sequence. A store with one shape declares one form and the
    interface shows a single "Sign in for me".
    """

    key: str
    label: str
    description: str
    steps: list[SignInStep]


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

    # The shapes this store's login form comes in, for the "Sign in for me"
    # button - one entry per shape, in the order the interface offers them.
    # **Empty by default, and empty means the button is not offered at all** -
    # only the one-at-a-time Email / Password / Code buttons, which need no
    # knowledge of the form because the person clicks the field first.
    #
    # That default is the honest one. A form is a claim about a page nobody
    # can see while it runs, and a wrong one does not fail harmlessly: typing
    # an Enter into a half-filled form is a failed login attempt, and failed
    # login attempts are what get an account rate-limited or locked - the
    # exact outcome this app is built to avoid. So fill this in only from a
    # form somebody has actually looked at, and say in the comment which it
    # was.
    sign_in_forms: list[SignInForm] = []
    # Anything true of this store whichever shape is on the screen, said in
    # one sentence and shown before the sign-in rather than in a tooltip. A
    # checkbox is the usual case: the steps type and press keys, so they
    # cannot tick "Remember me" - and on most stores an unticked "Remember me"
    # is a session cookie that no profile can keep. Per-shape guidance goes on
    # the form's own `description` instead.
    sign_in_note: str = ""

    # Whether this store's offers can only be read by a signed-in session.
    # `False` for every store with a public feed, and that is the shape to
    # prefer: sessionless discovery is what lets a run end after one HTTP
    # request when nothing is free. Prime Gaming has no public feed (measured:
    # the signed-out page is an empty SPA shell and the data endpoint answers
    # 403), so it sets this and does its discovery in `discover_in_session`.
    # The runner then opens the account's browser session for discovery and
    # claims together - one session, one lock - and `list_free_offers()`
    # returns `[]` as an honest no-op.
    discovery_needs_session: bool = False

    @property
    def sign_in_page(self) -> str:
        return self.signin_url or self.login_url

    async def discover_in_session(self, page: Page) -> list["FreeOffer"]:
        """What is free, read from inside the account's signed-in session.

        Only called when `discovery_needs_session` is true, from within the
        same `manager.session` the claims then use. The default is the empty
        answer: most stores never need this, and the sessionless
        `list_free_offers()` stays the boundary to defend.
        """
        return []

    async def inspect_base_game(
        self, page: Page, offer: "FreeOffer"
    ) -> "BaseGame | None":
        """What game this DLC needs, and whether the account has it.

        Called only for an offer whose `kind` is `dlc`. `None` means this
        adapter cannot say - which is the honest default, and leaves the DLC to
        be attempted on its own as it always was.

        Costs one page load, so it is only called when there is a DLC to claim.
        """
        return None

    def checkout_url(self, offer: "FreeOffer") -> str | None:
        """The page a person opens to finish this offer's checkout by hand.

        Returned to the un-driven sign-in window when a run stops with
        `CheckoutBlocked` - a captcha the driven browser cannot pass. `None`
        (the default) means the store has no such fallback, so the interface
        offers nothing it cannot honour. A store that overrides this returns the
        checkout URL for the *specific* offer, so the window opens straight on
        the order rather than on the shop front.
        """
        return None
    # A sentence for the add-account page saying what this adapter does and
    # does not do.
    blurb: str = ""

    REQUIREMENTS: list[Requirement] = []

    def requirements(self) -> list[Requirement]:
        return list(self.REQUIREMENTS)

    @abstractmethod
    async def list_free_offers(self) -> list[FreeOffer]:
        """What is free right now. No account, no session, no browser."""

    async def list_upcoming_offers(self) -> list[FreeOffer]:
        """What is announced to become free, with `starts_at` in the future.

        The same discipline as `list_free_offers` - a public endpoint, no
        account, no browser - and the same shape back. The default is an empty
        list, because most stores do not announce their giveaways; a store that
        does (Epic names next week's on the same endpoint) overrides this so
        the interface can show a calendar and the scheduler knows the moment
        an announced offer actually turns free.

        Nothing returned here is ever claimed: a claim run works only off what
        `list_free_offers` says is free *now*.
        """
        return []

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
    async def claim(
        self, page: Page, offer: FreeOffer, waiter: "ChallengeWaiter | None" = None
    ) -> ClaimResult:
        """One attempt. Raises `NeedsAttention` when only a person can go on.

        `waiter`, when given, is how a *watched* run answers a captcha: instead
        of stopping, the adapter calls `await waiter.wait(is_cleared)` and the
        person solves the challenge on the screen, then the claim carries on.
        With no waiter (a scheduled run), a captcha is `NeedsAttention` as
        always - Trove never solves one itself.
        """
