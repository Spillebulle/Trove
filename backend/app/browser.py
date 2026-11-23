"""Browser sessions: one persistent profile per account, and who may use it.

This is the module the whole design turns on. CLAUDE.md's first rule is
sessions, not logins: an account signs in once, by hand, and what Trove keeps
afterwards is a Chromium profile directory holding the cookies, the local
storage and the device fingerprint that sign-in produced. A run signs in with
nothing. (An account may hold encrypted sign-in details, but those are typed
into the person's own sign-in window when they press a button - see
`keyboard.py` - and nothing that runs unattended can read them.)

Three facts drive the shape of everything below.

**A persistent profile is a lock.** Chromium refuses to open a user-data
directory that another Chromium already has open, and when it does not refuse
it corrupts it. So access to an account's profile is serialised behind one
`asyncio.Lock` per account, and both users of a profile - a scheduled claim run
and the live view the user drives by hand - go through the same gate. A run
that arrives while the user is signing in waits, and if it waits too long it
gives up and tries at the next tick rather than queueing up behind a person who
has gone to make tea.

**Headed is the default.** `headless=new` is one of the signals bot detection
reads, and this app's whole purpose is not to be read that way. In Docker the
entrypoint provides an Xvfb display so headed still works there. CLAUDE.md
asks for this to be measured rather than assumed, and it has not been measured
yet; the default is the conservative guess until it is.

**Nothing here retries.** A claim that fails, fails once. The caller files the
account for attention and stops. A retry loop against a store that has just
challenged you is the fastest route to a locked account, which is a far worse
outcome than a missed free game.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import stat
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .config import get_settings
from .keyboard import window_titles
from .timeutil import utcnow

# How many two-second polls the un-driven checkout window must show no visible
# browser window before Trove treats the checkout as finished and closes it.
# Long enough that a moment between pages is never mistaken for the end, short
# enough that nobody sits looking at a black screen wondering.
BLANK_POLLS_BEFORE_FINISH = 4

logger = logging.getLogger(__name__)

settings = get_settings()

# The viewport every profile uses. A common desktop size, stated once: a
# viewport that changes between runs is itself a fingerprint, and one of the
# odd sizes a headless default picks (800x600) is a well-known tell.
VIEWPORT = {"width": 1280, "height": 800}

# Chromium flags. Short on purpose.
#
# `--disable-blink-features=AutomationControlled` removes the `navigator.
# webdriver` flag, which is the single cheapest thing a store checks. This is
# the one anti-detection measure Trove takes, and it is a removal of a signal
# rather than a forgery of one: the app is not pretending to be a person, it is
# declining to announce itself as a robot while a person's own session is used
# on their own machine.
#
# Nothing here solves a challenge, and nothing here ever will. When a store
# asks a question, a person answers it through the live view.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    # Shared memory in a container defaults to 64 MB and Chromium will crash
    # part-way through a heavy store page without this.
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    # The password manager and the "save your password?" bubble have nothing to
    # do here, and the bubble can sit over the button a run is trying to click.
    "--disable-features=PasswordManagerOnboarding,AutofillServerCommunication",
]

# What a browser in the container needs that one on a desktop does not. Shared
# between the Playwright launch and the un-driven sign-in window, because both
# draw on the same Xvfb display and both fall over in the same ways without it.
CONTAINER_ARGS = [
    # **Give the browser a GPU, even a software one.** Under Xvfb there is no
    # GPU, and Chrome's answer to that is to switch WebGL off and report no
    # WebGPU adapter at all - "No available adapters." was the exact line a
    # Cloudflare challenge logged in the live view. A browser whose user-agent
    # says desktop Chrome and whose WebGL is absent is a contradiction of the
    # same kind as the codec one: real Chrome on a real desktop always has it.
    #
    # `--ignore-gpu-blocklist` lets Chrome use Mesa's llvmpipe through ANGLE
    # (the Dockerfile installs it), which makes WebGL exist and report a real
    # renderer string; `--enable-unsafe-webgpu` lets an adapter appear on top
    # of it. This is the same principle as driving real Chrome rather than the
    # bundle: not a forged signal but a capability the browser is supposed to
    # have, restored. It was the difference, for the other claimer projects that
    # hit this wall, between a captcha that came back "incorrect response"
    # forever and one a person could answer.
    "--ignore-gpu-blocklist",
    "--enable-unsafe-webgpu",
    # **The cookie store, and why the sign-in loop happened.** Chrome on Linux
    # encrypts the cookie database with a key from the system keyring (Secret
    # Service / gnome-keyring). A container has no keyring, so which key Chrome
    # uses is unstable between launches - and cookies written under one key are
    # undecryptable under the next, which reads as "logged out". It bit exactly
    # here: Playwright's *driven* browser already passes these two flags
    # (`--password-store=basic --use-mock-keychain`, deterministic obfuscation,
    # no keyring), but the *un-driven* sign-in window did not. So a person
    # signed in through the un-driven window, its cookies were encrypted with a
    # keyring key, and the driven session-check - using the mock keychain -
    # could not read them and reported the account signed out. Reopening the
    # un-driven window could not read its own previous cookies either, and the
    # whole thing looped. Giving the un-driven window the same two flags makes
    # both browsers share one deterministic key, so the session written by hand
    # survives the handoff to the run and survives a restart.
    "--password-store=basic",
    "--use-mock-keychain",
]

# `--no-sandbox`, and when. Chrome's sandbox needs either its setuid helper
# or unprivileged user namespaces, and a container can withhold both: on an
# older kernel or a stricter seccomp profile Chrome then refuses to start at
# all - a failure that is worse than it sounds, because `resolve_channel`
# would read it as "Chrome is not usable", fall back to the bundled Chromium,
# and quietly leave the install unable to answer a captcha. On a current
# Docker, measured by the smoke workflow, Chrome starts sandboxed as `pwuser`
# without the flag. So the default is to find out: `resolve_channel` tries
# the sandbox first and adds the flag only if Chrome would not come up, and
# says which in the log. The isolation dropped in that case is the browser's
# own inside a container that is already the boundary, and the pages are two
# storefronts. `CONTAINER_SANDBOX=on|off` overrides the probe either way.
NO_SANDBOX = "--no-sandbox"
if settings.in_container and settings.container_sandbox.strip().lower() == "off":
    CONTAINER_ARGS.insert(0, NO_SANDBOX)

if settings.in_container:
    LAUNCH_ARGS.extend(CONTAINER_ARGS)


def _sandbox_arg_sets() -> list[list[str]]:
    """The launch-arg variants to probe, in the order to prefer them."""
    mode = settings.container_sandbox.strip().lower()
    if settings.in_container and mode == "auto":
        return [list(LAUNCH_ARGS), list(LAUNCH_ARGS) + [NO_SANDBOX]]
    return [list(LAUNCH_ARGS)]


def _commit_no_sandbox() -> None:
    """The probe found Chrome needs the flag: give it to every launch from now."""
    if NO_SANDBOX not in LAUNCH_ARGS:
        LAUNCH_ARGS.append(NO_SANDBOX)
    if NO_SANDBOX not in CONTAINER_ARGS:
        CONTAINER_ARGS.insert(0, NO_SANDBOX)


# Which browser channel actually worked, once we have found out. `False` means
# "not decided yet"; `None` means "the bundled Chromium", which is also what
# Playwright wants for that case.
_channel: str | None | bool = False


def _channel_candidates() -> list[str | None]:
    """The channels to try, in order, for the configured preference."""
    wanted = (settings.browser_channel or "auto").strip().lower()
    if wanted in ("chromium", "bundled", ""):
        return [None]
    if wanted == "auto":
        # Real Chrome first. See `config.browser_channel` for why this matters
        # more than anything else in the launch: the bundled Chromium has no
        # H.264, HEVC or AAC, and a challenge that probes for them fails a
        # browser whose user-agent claims to be Chrome.
        return ["chrome", None]
    return [wanted, None]


async def resolve_channel(playwright: Playwright, profile_path: Path) -> str | None:
    """Work out which browser to drive, once, and remember it.

    Tries each candidate by actually launching it, because "is Chrome
    installed" has no answer worth trusting short of starting it: a path can
    exist and be a broken install, and a channel can be present on one platform
    and not another. The probe is a throwaway profile, never the account's, so a
    failed attempt cannot leave a real profile half-initialised.
    """
    global _channel
    if _channel is not False:
        return _channel  # type: ignore[return-value]
    # Several browsers can start in the same instant after a restart (three
    # automatic sign-ins did, 23 Nov 2026), and three probes sharing one
    # probe directory deleted it from under each other. One probe; the rest
    # wait for its answer.
    async with _channel_lock:
        if _channel is not False:
            return _channel  # type: ignore[return-value]
        return await _probe_channel(playwright, profile_path)


_channel_lock = asyncio.Lock()


async def _probe_channel(playwright: Playwright, profile_path: Path) -> str | None:
    global _channel
    probe_dir = profile_path.parent / ".channel-probe"
    for candidate in _channel_candidates():
        launched = False
        # In a container on "auto", each channel is tried with Chrome's own
        # sandbox first and with --no-sandbox second; the first that starts
        # decides for every later launch, the un-driven window included.
        for args in _sandbox_arg_sets():
            try:
                probe_dir.mkdir(parents=True, exist_ok=True)
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(probe_dir),
                    headless=True,
                    channel=candidate,
                    args=args,
                    # **Playwright adds --no-sandbox itself unless told not
                    # to** (`chromiumSandbox` defaults to false), which made
                    # the first version of this probe a lie: "with its sandbox"
                    # launched every time because it never had one, and the
                    # un-driven window - which gets only the flags it is given -
                    # then died on the real sandbox with nothing to say why.
                    # Ask for the sandbox explicitly when probing without the
                    # flag, so the answer is about Chrome and not Playwright.
                    chromium_sandbox=NO_SANDBOX not in args,
                    timeout=60_000,
                )
                await context.close()
            except Exception as exc:
                logger.info(
                    "Browser channel %r %s is not usable (%s).",
                    candidate or "bundled chromium",
                    "without its sandbox" if NO_SANDBOX in args else "with its sandbox",
                    str(exc).splitlines()[0][:120],
                )
                continue
            finally:
                purge_profile(probe_dir)
            launched = True
            if NO_SANDBOX in args and NO_SANDBOX not in LAUNCH_ARGS:
                _commit_no_sandbox()
                logger.warning(
                    "Chrome would not start with its own sandbox in this "
                    "container, so it runs with --no-sandbox. That is the usual "
                    "state on an older kernel or a strict seccomp profile; the "
                    "container is the isolation boundary either way."
                )
            elif settings.in_container:
                logger.info("Chrome starts with its own sandbox in this container.")
            break
        if not launched:
            continue

        _channel = candidate
        if candidate is None and (settings.browser_channel or "auto").lower() == "auto":
            logger.warning(
                "Driving Playwright's bundled Chromium because Google Chrome "
                "was not found. The bundle ships no H.264, HEVC or AAC while "
                "claiming to be Chrome, and Cloudflare's challenge probes for "
                "exactly those, so a store may refuse to let you past a "
                "captcha however honestly you answer it. Installing Chrome "
                "(or `playwright install chrome`) is the fix."
            )
        else:
            logger.info("Driving %s.", candidate or "the bundled Chromium")
        return _channel  # type: ignore[return-value]

    # Nothing launched at all. Let the caller's launch raise the real error
    # rather than inventing one here.
    _channel = None
    return None


class ProfileBusy(RuntimeError):
    """Somebody else is using this account's browser profile."""


class NeedsAttention(Exception):
    """The run met something only a person can answer.

    Carries the sentence the user will read and, where one was taken, the
    screenshot that is the evidence for it. Raising this is how an adapter
    stops: it never guesses, never retries, and never asks for a captcha
    solving service.
    """

    def __init__(self, reason: str, screenshot: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.screenshot = screenshot


class SignedOut(NeedsAttention):
    """The health check read the account as signed out.

    Its own type so the runner can tell "the session is gone" - the one stop
    an automatic sign-in can answer - from every other question a store asks.
    """


class StoreChallenged(NeedsAttention):
    """The store put an anti-bot challenge in front of the *driven* browser.

    Distinct from a broken session, and it matters which one you are looking
    at. A signed-out account fails the same way on every run until a person
    signs in, so parking it (`needs_attention`, which the scheduler skips) is
    right. A challenge is not that: Cloudflare judges the browser and the
    address on the visit in front of it, so the session behind it may be
    perfectly good and the very next run may sail through. Parking on one
    would stop the schedule over a verdict that has already expired - and it
    is what turned "Epic showed a challenge" into a loop of sign in, check,
    challenge, sign in again.

    So the runner records this, keeps the account where it was and lets the
    ordinary cadence try again. One page load every few hours is polite by
    any measure, and it is the only thing that can find out whether the
    verdict has changed.
    """


class StoreUnavailable(StoreChallenged):
    """The store answered with an error of its own: a 5xx, a 429, or nothing.

    The same fact as a challenge from a different direction, so it inherits
    the same handling: the run is recorded with the reason, the account is
    left where it was, and the ordinary cadence tries again. Reported from a
    live install: GOG's sections API answered `500 Domain Not Found` for a
    while (an outage on GOG's side; it answered 200 again soon after) and the
    run's generic failure path parked the account - and the page then told the
    person to sign in, which could not have fixed a server that was down.
    itch.io's `429 Too Many Requests` page did the same through the health
    check, which matched neither signed-in nor signed-out and answered "could
    not tell". Neither says anything about the session.
    """


# The words every refusal sentence carries, so a later visit that the store
# *does* answer can recognise the note as stale and clear it. Deliberately not
# in a challenge's sentence ("left it alone"): a store answering a plain
# request says nothing about whether it will wave the driven browser through.
REFUSAL_MARK = "not your account, so Trove has left the account alone"

# How builds before StoreUnavailable wrote the same fact: the generic failure
# path's `f"{type(exc).__name__}: {exc}"` of an httpx refusal.
_LEGACY_REFUSAL_PREFIXES = (
    "HTTPStatusError: Server error",
    "HTTPStatusError: Client error '429",
    "ConnectError:",
    "ConnectTimeout:",
    "ReadTimeout:",
)


def is_refusal_note(reason: str | None) -> bool:
    """Whether an account's reason only says "the store refused a visit"."""
    if not reason:
        return False
    return REFUSAL_MARK in reason or reason.startswith(_LEGACY_REFUSAL_PREFIXES)


def refusal_reason(store_name: str, status: int | None) -> str | None:
    """The sentence for a store that refused the visit, or None if it did not.

    `status` is the HTTP status the store answered with, or None when nothing
    arrived. Only 429 and 5xx count: a 403 is how Cloudflare serves a
    challenge (the adapters' own markers handle that), and any other 4xx is
    more likely an endpoint that changed, which a person should hear about.
    """
    tail = f" That is the store's side, {REFUSAL_MARK} and will try again on the next run."
    if status == 429:
        return (
            f"{store_name} answered 429 Too Many Requests: it wants fewer"
            f" visits from this address for a while." + tail
        )
    if status is not None and status >= 500:
        return f"{store_name}'s own server answered {status}, an error on its end." + tail
    return None


def http_refusal_reason(store_name: str, exc: BaseException) -> str | None:
    """`refusal_reason` for an httpx failure: a refusing status, or unreachable."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        return refusal_reason(store_name, exc.response.status_code)
    if isinstance(exc, httpx.TransportError):
        return (
            f"{store_name} could not be reached ({type(exc).__name__})."
            f" That is the network or the store, {REFUSAL_MARK} and will try"
            " again on the next run."
        )
    return None


class CheckoutBlocked(NeedsAttention):
    """The checkout hit a captcha the *driven* browser cannot pass.

    A distinct stop, because it has a distinct remedy. Epic raises a Talon
    captcha at "Add to library", and a human solve performed in the
    CDP-driven browser is *rejected* at the order step - proven, Aug 2026:
    ``confirm-order`` returns HTTP 400 ``epic.error.captcha.challenge.failed``
    with the token attached. The browser is refused for being automated, and no
    amount of honest clicking changes that.

    So this is not "solve it on the screen" (that is the driven browser, which
    fails). It is "finish this one order in the **un-driven** window" - the same
    plain Chrome that already passes the sign-in challenge, with no CDP on it.
    It carries the offer's external id so the interface can open that window
    straight on the checkout page.
    """

    def __init__(
        self, reason: str, screenshot: str | None = None, offer_id: str | None = None
    ) -> None:
        super().__init__(reason, screenshot)
        self.offer_id = offer_id


def profile_dir_for(account_id: int, label: str) -> str:
    """The directory name for an account's profile, relative to the root.

    The id leads so the name is unique whatever the label says, and a slug of
    the label follows so a person looking in the data directory can tell the
    profiles apart. Renaming an account does not move its profile: the stored
    name is what the account row points at, and re-deriving it would orphan a
    signed-in session.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
    return f"{account_id:04d}-{slug}" if slug else f"{account_id:04d}"


def purge_profile(path: Path) -> list[str]:
    """Empty a browser profile. Returns the names it could not remove.

    **Emptying, not unlinking.** Removing the directory itself is not the goal
    and on Windows is often not possible: measured on this project, every file
    inside deleted cleanly and then `rmtree` failed with `WinError 5` on the now
    empty directory, because it sits under a OneDrive folder and the sync client
    holds a handle on it. An anti-virus scanner does the same thing. Since
    Chromium initialises happily into an existing empty directory, an empty
    profile *is* a fresh profile, and insisting on the inode turned a working
    reset into a 500.

    Read-only files are chmod-ed and retried once. Chromium leaves a few, and
    one of them refusing to go should not strand the rest of the profile.
    """
    failed: list[str] = []
    if not path.exists():
        return failed

    def _retry(func, target, _exc):
        # Only a deletion is worth retrying after a chmod. `rmtree` also
        # reports failures of `os.open`/`os.scandir`, which take more than a
        # path - calling them back with one raised a TypeError that escaped.
        if func not in (os.unlink, os.remove, os.rmdir):
            failed.append(Path(target).name)
            return
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            failed.append(Path(target).name)

    for entry in path.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, onerror=_retry)
            else:
                entry.unlink()
        except OSError:
            failed.append(entry.name)

    # Then the directory, if the filesystem will let go of it. It usually will
    # on Linux and often will not on Windows, and neither outcome is a failure.
    try:
        path.rmdir()
    except OSError:
        pass
    return failed


def find_chrome_executable() -> Path | None:
    """Where real Chrome lives on this machine, or None.

    Needed because the un-driven sign-in window is launched by Trove itself
    rather than by Playwright, so there is no browser object to ask. Checked in
    the order a desktop is likely to have them, Chrome before Chromium, because
    the whole point of that window is to be the most ordinary browser possible.
    """
    candidates: list[str] = []
    if os.name == "nt":
        for root in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if root:
                candidates.append(str(Path(root) / "Google/Chrome/Application/chrome.exe"))
    else:
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/opt/google/chrome/chrome",
        ]

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)

    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


class NoLocalBrowser(RuntimeError):
    """There is no browser on this machine that a person could sign in with."""



# What Chrome must not draw over the page. In the container these are set by
# enterprise policy (docker/chrome-policy.json), which is the authoritative
# way and covers both browsers; a desktop has no policy directory Trove may
# write to - a machine-wide policy is not this app's to install - so the same
# settings go into the profile's own preferences instead.
QUIET_PREFS = {
    # The "Save password?" bubble, and its auto-sign-in cousin.
    "credentials_enable_service": False,
    "credentials_enable_autosignin": False,
    "profile": {
        "password_manager_enabled": False,
        # Block web notification prompts rather than asking. The prompt is a
        # bubble across the top of the page and it takes the next click.
        "default_content_setting_values": {"notifications": 2},
    },
    # The autofill dropdown, which opens over the field under it.
    "autofill": {"profile_enabled": False, "credit_card_enabled": False},
    # "Continue where you left off". Not for the tabs - `_forget_open_tabs`
    # clears those before every launch - but because it is what makes Chrome
    # keep a site's *browser-session* cookies across a restart. Fanatical,
    # Humble and IndieGala sign a person in with exactly that kind of cookie,
    # so the session made in the sign-in window was dropped the moment the
    # run's Chrome started, and every check read "signed out" (measured on the
    # live install, 23 Nov 2026: no login cookie left in any of the three
    # profiles). An ordinary Chrome setting, the one most people browse with;
    # nothing about a cookie is changed or forged.
    "session": {"restore_on_startup": 1},
}

# Where Chrome keeps the tabs it would restore. Both exist on Chrome 151; with
# only `Sessions` cleared, measured, the restored tabs still grew by one per
# launch - a run every few hours would be a browser opening dozens of store
# pages at once within a week.
SESSION_TAB_DIRS = ("Sessions", "Sessions_Encrypted")


def _forget_open_tabs(profile_path: Path) -> None:
    """Remove the saved tabs, so "continue where you left off" restores none.

    Measured in the container: with these gone and the preference on, a
    browser-session cookie survives three reopens running and Chrome opens
    exactly one tab each time. Never fatal - the worst a failure costs is an
    extra tab.
    """
    for name in SESSION_TAB_DIRS:
        try:
            shutil.rmtree(profile_path / "Default" / name, ignore_errors=True)
        except Exception as exc:  # pragma: no cover - rmtree already ignores
            logger.debug("Could not clear %s in %s: %s", name, profile_path.name, exc)


def _merge(into: dict, updates: dict) -> bool:
    """Deep-merge `updates` into `into`. True if anything actually changed."""
    changed = False
    for key, value in updates.items():
        if isinstance(value, dict):
            child = into.get(key)
            if not isinstance(child, dict):
                child = {}
                into[key] = child
                changed = True
            changed |= _merge(child, value)
        elif into.get(key) != value:
            into[key] = value
            changed = True
    return changed



# The files Chrome makes when it takes a profile. `SingletonLock` is a
# **symlink** to `<host>-<pid>` that points at nothing, so it has to be tested
# with `lexists`: `Path.exists()` follows the link, finds no target and answers
# False for a lock that is very much there.
SINGLETONS = ("SingletonLock", "SingletonCookie", "SingletonSocket")

# Chrome's exit is not instant even after the process is gone: the network
# service flushes the cookie database on the way out, and the session a person
# just created is in that flush.
PROFILE_SETTLE_S = 3.0
PROFILE_RELEASE_TIMEOUT_S = 10.0


def _lock_owner_alive(profile_path: Path) -> bool:
    """Is a live process still holding this profile's singleton lock?

    **A lock file is not evidence on its own.** Chrome removes these when it
    shuts down through its own UI; it does *not* when it is terminated, which
    is how Trove closes every window it opens. So a left-behind `SingletonLock`
    is the normal state after a sign-in window closes, and treating its mere
    presence as "still held" means waiting for something that will never
    happen - measured in the container, where it stalled the post-sign-in check
    by the full timeout after Chrome had already exited 0.

    Chrome's own test is the one to copy: the lock is a symlink named
    `<hostname>-<pid>`, and it is stale when that pid is gone. Anything that
    cannot be read - no symlink, no pid in the name, a foreign hostname, or a
    platform without `SingletonLock` at all - is treated as **not held**, which
    is the direction that keeps a stale lock from blocking a sign-in forever.
    """
    lock = profile_path / "SingletonLock"
    if not os.path.lexists(lock):
        return False
    try:
        target = os.readlink(lock)
    except OSError:
        return False
    _, _, pid_text = target.rpartition("-")
    if not pid_text.isdigit():
        return False
    pid = int(pid_text)
    try:
        os.kill(pid, 0)  # signal 0: existence check, changes nothing
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # somebody else's process, so alive
    except OSError:
        return False
    return True


async def wait_for_profile_release(
    profile_path: Path, timeout_s: float = PROFILE_RELEASE_TIMEOUT_S
) -> bool:
    """Wait until no live Chrome holds this profile, then a beat more.

    Between a sign-in window closing and the session check opening the same
    profile there is a handover, and it used to be a flat three-second sleep -
    a guess at how long Chrome takes to finish. When the guess is short the
    check opens the profile while the old Chrome is still writing its cookie
    database, reads the cookies as they were *before* the sign-in, and reports
    an account that just signed in as signed out. Signing in a second time then
    "works", because by then the first Chrome has finished.

    In the ordinary case the caller has already waited for the process to
    exit, so `_lock_owner_alive` is false at once and this is `PROFILE_SETTLE_S`
    - the part that covers the flush, which is the part that was too short.
    The loop is for the case that is not ordinary: another Chrome, started by
    hand or left from a crash, genuinely holding the directory.

    Returns whether the profile was seen free. False means the timeout passed
    with a live holder; the caller carries on regardless, because refusing to
    check a session is worse than checking one a moment early.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if not _lock_owner_alive(profile_path):
            await asyncio.sleep(PROFILE_SETTLE_S)
            return True
        await asyncio.sleep(0.5)
    logger.warning(
        "A live Chrome still holds %s after %.0f s. Carrying on; if the "
        "session check says signed out here, that is why.",
        profile_path.name,
        timeout_s,
    )
    return False


def quiet_profile(profile_path: Path) -> None:
    """Turn off Chrome's own pop-ups in this profile, before it is launched.

    The bubbles matter more here than in an ordinary browser. The sign-in
    window is watched through VNC on a display with **no window manager**, so a
    bubble cannot be dragged aside; and the "Save password?" one appears the
    instant a sign-in succeeds, which is exactly when the store is putting up
    the next thing to click.

    Only ever called while the account's lock is held and no Chrome has the
    profile open, because Chrome rewrites this file as it exits and a
    concurrent write would be lost or, worse, half-applied.

    Failure here is never fatal and never retried. A profile is a signed-in
    session that took a person and a captcha to make; losing one to a
    preferences write would be a far worse outcome than a bubble. So anything
    unexpected is logged and left exactly as it was found - including a
    Preferences file that will not parse, which is Chrome's to repair.
    """
    _forget_open_tabs(profile_path)
    prefs_path = profile_path / "Default" / "Preferences"
    try:
        if prefs_path.exists():
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            if not isinstance(prefs, dict):
                logger.warning("%s is not a JSON object; left alone.", prefs_path)
                return
        else:
            # A profile Chrome has never opened. Writing the file first is
            # fine - Chrome merges its own defaults on top of what it finds.
            prefs = {}
        if not _merge(prefs, QUIET_PREFS):
            return
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: a half-written Preferences file is a profile Chrome will
        # reset, and resetting it is losing the session.
        temp = prefs_path.with_suffix(".trove-tmp")
        temp.write_text(json.dumps(prefs), encoding="utf-8")
        os.replace(temp, prefs_path)
        logger.info("Quieted Chrome's pop-ups in %s.", profile_path.name)
    except Exception as exc:
        logger.warning(
            "Could not quiet Chrome's pop-ups in %s (%s). Harmless: it only "
            "means the browser may offer to save a password.",
            profile_path.name,
            exc,
        )


def launch_detached(profile_path: Path, url: str):
    """Open the account's profile in an ordinary browser window. No automation.

    This is the escape hatch, and it exists because the live view cannot be
    one. Streaming a page needs the DevTools protocol attached, a page can tell
    when it is, and a challenge that has decided a browser is automated will not
    accept an answer from it however honestly a person clicks. That is
    structural: it cannot be fixed by sending better mouse events, and chasing
    it with stealth patches is the arms race CLAUDE.md refuses to enter.

    So for the one step that genuinely needs a person, the automation is
    removed rather than disguised. Trove starts the browser as a plain
    subprocess pointed at the account's profile directory, and then has no
    connection to it at all: no CDP, no `--enable-automation`, no
    `navigator.webdriver`, nothing to detect. It is a person using Chrome on
    their own computer, which is exactly what it is.

    The flags are deliberately only the two that stop a first-run wizard from
    covering the page. Everything else Playwright normally adds is automation
    scaffolding this window must not have.

    Returns the `Popen`, whose lifetime is the sign-in session: when the person
    closes the window, the process exits and the profile is free again.
    """
    executable = find_chrome_executable()
    if executable is None:
        raise NoLocalBrowser(
            "No Google Chrome on this machine, so Trove cannot open a normal "
            "browser window for you to sign in with. Install Chrome, or sign "
            "in through the live view instead."
        )
    profile_path = profile_path.resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    quiet_profile(profile_path)
    args = [
        str(executable),
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if settings.in_container:
        # The same sandbox and GPU flags the driven browser gets, for the same
        # reasons; none of them is an automation flag. Plus a window the size
        # of the Xvfb screen, placed at its origin: there is no window manager
        # on that display to size or place anything, so a window left to its
        # own devices opens at some default and the person sees a corner of it.
        args += CONTAINER_ARGS
        args += [
            f"--window-size={VIEWPORT['width']},{VIEWPORT['height']}",
            "--window-position=0,0",
            # Full screen, as F11 would: the person is looking at this through
            # the screen view and the address bar, the tab strip and the
            # --no-sandbox infobar are a fifth of a small picture they cannot
            # use. The page is the whole screen. `--kiosk` would do the same
            # and also take away Esc and every dialog, which is too much.
            "--start-fullscreen",
            # The shared-memory problem is the same as for the driven browser.
            "--disable-dev-shm-usage",
        ]
    if settings.browser_proxy:
        # The same address the runs use. A session made from one address and
        # replayed from another is exactly what a store's edge is watching for.
        args.append(f"--proxy-server={settings.browser_proxy}")
    args.append(url)
    return subprocess.Popen(
        args,
        # Detached, so the window outlives the request that opened it and
        # closing Trove does not kill somebody's half-finished sign-in.
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# -- Passing Cloudflare's check with nothing attached --------------------------
#
# Measured on the live install, 22 Nov 2026, on copies of the Epic account's
# profile (cookies intact), loading the store and reading only the X window's
# title - so the reading attached nothing:
#
#   debugging port open, nothing connected          -> check passes in ~6 s
#   bare CDP connection, Page.navigate only         -> passes
#   bare CDP, plus Runtime.enable + Page.enable     -> passes
#   Playwright, launched as a run is                -> never passes
#   Playwright, connect_over_cdp to plain Chrome    -> never passes
#   Playwright, no viewport, no --enable-automation -> never passes
#
# So it is not the DevTools protocol and not the domains Playwright enables on
# the page: it is Playwright being attached at all - most likely its automatic
# attachment to every frame, the challenge's own among them. And when the check
# *does* pass naturally, Playwright attaching afterwards is fine: the same
# probe then navigated to the wishlist under Playwright, signed in.
#
# So a session that declares a `warm_url` starts real Chrome itself, opens that
# URL over a bare connection, lets the check finish (or not) on its own, and
# only then connects Playwright. Nothing is solved, clicked or forged; the
# check looks at a browser that genuinely has nothing unusual attached at that
# moment, which is the un-driven sign-in window's principle applied to the
# first page load of a run.

_CHALLENGE_TITLE = re.compile(r"just a moment|one more step|attention required|security check", re.I)
# How long the check gets to finish with nothing attached. It took 6-9 s when
# measured; a check still up at this point wants a person, and the adapter's
# own health check will say so.
UNATTACHED_CHECK_S = 30.0


def is_challenge_title(title: str | None) -> bool:
    return bool(title) and bool(_CHALLENGE_TITLE.search(title))


def read_devtools_port(profile_path: Path) -> int | None:
    """The port Chrome chose for `--remote-debugging-port=0`, from its own file."""
    try:
        first = (profile_path / "DevToolsActivePort").read_text().splitlines()[0].strip()
        return int(first)
    except (OSError, ValueError, IndexError):
        return None


def _free_local_port() -> int:
    """A port nothing on 127.0.0.1 is listening on right now."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _page_targets(port: int) -> list[dict]:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as response:
            return [t for t in json.load(response) if t.get("type") == "page"]
    except Exception:
        return []


async def _start_attachable_chrome(executable: Path, profile_path: Path) -> tuple[subprocess.Popen, int]:
    """Real Chrome on the profile with a local DevTools port and nothing attached.

    The flags are the un-driven window's (sandbox, GPU, cookie store) plus the
    port: the set the probe measured passing. None of Playwright's launch
    scaffolding, because Playwright is not launching it.
    """
    (profile_path / "DevToolsActivePort").unlink(missing_ok=True)
    # A port picked here and passed as a number - not `--remote-debugging-port=0`
    # with Chrome choosing. Measured on the live install, same profile copy,
    # minutes apart: with `=0` Cloudflare's check never passed (five runs, 30 s
    # each); with a fixed number it passed in 8-9 s every time (five runs). Why
    # is not established. The flags below are exactly the measured passing set:
    # `--remote-debugging-address` and `--lang` were dropped with it, since
    # neither was in the runs that passed.
    port = _free_local_port()
    args = [
        str(executable),
        f"--user-data-dir={profile_path}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        f"--window-size={VIEWPORT['width']},{VIEWPORT['height']}",
        "--window-position=0,0",
    ]
    if settings.in_container:
        args += CONTAINER_ARGS
    if NO_SANDBOX in LAUNCH_ARGS and NO_SANDBOX not in args:
        args.append(NO_SANDBOX)
    if settings.headless:
        args.append("--headless=new")
    if settings.browser_proxy:
        args.append(f"--proxy-server={settings.browser_proxy}")
    if os.name == "nt":
        # A desktop only: Windows tells Chrome its window is covered, Chrome
        # marks the page hidden, and a hidden page drops every mouse and key
        # event sent over DevTools (measured, `visibilityState: hidden`).
        # Leaves the container's measured flag set untouched.
        args += ["--disable-backgrounding-occluded-windows",
                 "--disable-features=CalculateNativeWinOcclusion"]
    args.append("about:blank")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 20
    while loop.time() < deadline:
        if _page_targets(port):
            return proc, port
        if proc.poll() is not None:
            break
        await asyncio.sleep(0.3)
    _stop_process(proc)
    raise RuntimeError("Chrome did not open its DevTools port in time.")


async def _pass_check_unattached(port: int, url: str) -> None:
    """Open `url` over a bare connection and give any check time to finish."""
    import websockets

    targets = _page_targets(port)
    if not targets:
        return
    loop = asyncio.get_event_loop()
    started = loop.time()
    seen_check = False
    async with websockets.connect(targets[0]["webSocketDebuggerUrl"], max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
        while loop.time() - started < UNATTACHED_CHECK_S:
            await asyncio.sleep(1.5)
            now = _page_targets(port)
            title = now[0].get("title", "") if now else ""
            target_url = now[0].get("url", "") if now else ""
            if is_challenge_title(title):
                seen_check = True
                continue
            # Loaded: a real title on an http page, not the blank tab or a URL
            # standing in for a title mid-load.
            if target_url.startswith("http") and title and not title.startswith("http"):
                if seen_check:
                    logger.info(
                        "The security check at %s passed by itself after %.0fs, "
                        "with nothing attached.", url, loop.time() - started,
                    )
                return
    logger.info(
        "The page at %s was still on a security check after %.0fs with nothing "
        "attached; handing over to the run, which will report it.", url, UNATTACHED_CHECK_S,
    )


async def _close_gracefully(port: int, proc: subprocess.Popen) -> None:
    """Ask Chrome to close itself over DevTools, then make sure it has."""
    import urllib.request

    import websockets

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            ws_url = json.load(response)["webSocketDebuggerUrl"]
        async with websockets.connect(ws_url, max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                await asyncio.wait_for(ws.recv(), timeout=5)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Browser.close on port %s: %s", port, exc)
    try:
        await asyncio.to_thread(proc.wait, 15)
    except subprocess.TimeoutExpired:
        pass
    await asyncio.to_thread(_stop_process, proc)


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


@dataclass
class Lease:
    """Who currently holds an account's profile, and since when."""

    holder: str
    since: object = field(default_factory=utcnow)


class AccountBrowser:
    """One account's profile, and the lock that serialises access to it."""

    def __init__(self, account_id: int, profile_path: Path) -> None:
        self.account_id = account_id
        self.profile_path = profile_path
        self.lock = asyncio.Lock()
        self.lease: Lease | None = None
        self.context: BrowserContext | None = None
        # The un-driven sign-in window, while one is open. Kept so it can be
        # closed from the interface: on a screen with no window manager there
        # may be nothing to click to close it.
        self.process: subprocess.Popen | None = None


class BrowserManager:
    """Owns the Playwright process and one `AccountBrowser` per account.

    A single Playwright instance is started lazily on the first use and stopped
    on shutdown. Starting it costs a subprocess, so an app that never claims
    anything never pays for it.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._accounts: dict[int, AccountBrowser] = {}
        self._start_lock = asyncio.Lock()
        # Held so asyncio does not collect a waiter mid sign-in, which would
        # leave the profile locked with nothing watching the window.
        self._signin_tasks: set[asyncio.Task] = set()

    async def _ensure_playwright(self) -> Playwright:
        async with self._start_lock:
            if self._playwright is None:
                logger.info("Starting Playwright.")
                self._playwright = await async_playwright().start()
            return self._playwright

    def _entry(self, account_id: int, profile_path: Path) -> AccountBrowser:
        entry = self._accounts.get(account_id)
        if entry is None:
            entry = AccountBrowser(account_id, profile_path)
            self._accounts[account_id] = entry
        return entry

    def holders(self) -> dict[int, str]:
        """Every profile currently held, and by what. For the screen view's caption."""
        return {
            account_id: entry.lease.holder
            for account_id, entry in self._accounts.items()
            if entry.lease is not None
        }

    def who_holds(self, account_id: int) -> str | None:
        entry = self._accounts.get(account_id)
        return entry.lease.holder if entry and entry.lease else None

    @asynccontextmanager
    async def session(
        self,
        account_id: int,
        profile_path: Path,
        holder: str,
        wait_s: float = 0.0,
        warm_url: str | None = None,
    ):
        """Open the account's profile and yield its context.

        `warm_url`, when the adapter declares one, is opened before Playwright
        is attached, so a Cloudflare check on it can pass with nothing attached
        (see `_pass_check_unattached`). Needs real Chrome; without it the
        session launches through Playwright as before.

        `holder` is a word for whoever is asking - "run" or "live" - and is
        what the other side is told when it cannot have the profile. `wait_s`
        is how long to wait for the lock before giving up: a manual run from
        the UI can afford to wait a few seconds, the scheduler waits not at all
        and comes back at the next tick.
        """
        entry = self._entry(account_id, profile_path)
        try:
            await asyncio.wait_for(entry.lock.acquire(), timeout=wait_s or 0.001)
        except (asyncio.TimeoutError, TimeoutError):
            held_by = entry.lease.holder if entry.lease else "something else"
            raise ProfileBusy(
                f"The browser profile for this account is in use by "
                f"{held_by}. Close it and try again."
            ) from None

        entry.lease = Lease(holder=holder)
        playwright = await self._ensure_playwright()
        context: BrowserContext | None = None
        # Set only for a warm session: Chrome started by Trove, Playwright
        # connected to it rather than owning it.
        process: subprocess.Popen | None = None
        connected: Browser | None = None
        try:
            # Absolute, always. Real Chrome refuses a relative user-data-dir
            # with a modal dialog that presents only as a launch timeout.
            profile_path = profile_path.resolve()
            profile_path.mkdir(parents=True, exist_ok=True)
            quiet_profile(profile_path)
            channel = await resolve_channel(playwright, profile_path)
            executable = find_chrome_executable() if warm_url and channel == "chrome" else None
            if executable is not None:
                process, port = await _start_attachable_chrome(executable, profile_path)
                await _pass_check_unattached(port, warm_url)
                connected = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = connected.contexts[0]
                context.set_default_timeout(settings.browser_timeout_ms)
                entry.context = context
                yield context
                return
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=settings.headless,
                channel=channel,
                args=LAUNCH_ARGS,
                viewport=VIEWPORT,
                # A stated locale and timezone rather than whatever the
                # container reports. A profile whose language changes between
                # runs looks like a session being replayed somewhere else,
                # which is exactly the thing a store watches for.
                locale="en-GB",
                timezone_id="Europe/Oslo",
                accept_downloads=False,
                proxy={"server": settings.browser_proxy} if settings.browser_proxy else None,
                # Whatever the probe decided, for real: without this Playwright
                # quietly adds --no-sandbox of its own.
                chromium_sandbox=NO_SANDBOX not in LAUNCH_ARGS,
            )
            context.set_default_timeout(settings.browser_timeout_ms)
            entry.context = context
            yield context
        finally:
            entry.context = None
            entry.lease = None
            if connected is not None:
                # Disconnect, then end Chrome ourselves: Playwright did not
                # start it, so closing the connection leaves it running.
                try:
                    await connected.close()
                except Exception as exc:  # pragma: no cover - teardown noise
                    logger.debug("Disconnecting from %s: %s", account_id, exc)
            elif context is not None:
                try:
                    await context.close()
                except Exception as exc:  # pragma: no cover - teardown noise
                    logger.debug("Closing the context for %s: %s", account_id, exc)
            if process is not None:
                # Off the event loop: Chrome flushes its cookies on the way out,
                # and the session it just wrote is in that flush.
                await asyncio.to_thread(_stop_process, process)
            entry.lock.release()

    @asynccontextmanager
    async def bare_session(self, account_id: int, profile_path: Path, holder: str, wait_s: float = 5.0):
        """Real Chrome on the profile with a DevTools port and nothing else.

        For the automatic sign-in (`autosignin.py`): the same lock as every
        other path, the same launch as the warm session - which is the one
        Cloudflare was measured to let through - and no Playwright at all.
        Yields the port. On the way out Chrome is asked to close itself
        (`Browser.close`), so the cookie database is flushed the way a person
        closing the window flushes it, and the profile is given its settle
        time before the lock is released.
        """
        entry = self._entry(account_id, profile_path)
        try:
            await asyncio.wait_for(entry.lock.acquire(), timeout=wait_s or 0.001)
        except (asyncio.TimeoutError, TimeoutError):
            held_by = entry.lease.holder if entry.lease else "something else"
            raise ProfileBusy(
                f"The browser profile for this account is in use by "
                f"{held_by}. Close it and try again."
            ) from None
        entry.lease = Lease(holder=holder)
        process: subprocess.Popen | None = None
        port = 0
        try:
            profile_path = profile_path.resolve()
            profile_path.mkdir(parents=True, exist_ok=True)
            quiet_profile(profile_path)
            channel = await resolve_channel(await self._ensure_playwright(), profile_path)
            executable = find_chrome_executable() if channel == "chrome" else None
            if executable is None:
                raise RuntimeError("Signing in by itself needs real Chrome, and it is not installed.")
            process, port = await _start_attachable_chrome(executable, profile_path)
            yield port
        finally:
            if process is not None:
                await _close_gracefully(port, process)
                await asyncio.sleep(PROFILE_SETTLE_S)
            entry.lease = None
            entry.lock.release()

    async def open_local(
        self,
        account_id: int,
        profile_path: Path,
        url: str,
        on_closed=None,
        auto_finish: bool = False,
    ) -> None:
        """Open the profile in an un-driven browser and hold it until it closes.

        Takes the same per-account lock every other path takes, so a scheduled
        run cannot open the profile while somebody is signing in to it - which
        would corrupt it, since two Chromiums must never share a user-data
        directory. The lock is released when the window is closed, which is
        what makes "close the window when you are done" the whole of the user's
        side of the contract.

        `auto_finish` is for the checkout window, where the *page* decides when
        it is done: Epic's checkout ends by taking its own window away, and on
        an Xvfb with no window manager that is a black screen and nothing else.
        With it set, Trove watches the display for that (through xdotool, an X
        server property - nothing is attached to the browser) and closes the
        window itself once the browser has had no visible window for a few
        seconds running, which runs `on_closed` exactly as pressing Done would.
        It only ever fires *after* a window has been seen, so a slow-starting
        Chrome is never mistaken for a finished one.
        """
        entry = self._entry(account_id, profile_path)
        if entry.lock.locked():
            held_by = entry.lease.holder if entry.lease else "something else"
            raise ProfileBusy(
                f"The browser profile for this account is in use by {held_by}."
            )
        await entry.lock.acquire()
        entry.lease = Lease(holder="a sign-in window")
        try:
            if settings.in_container:
                # The un-driven window shares CONTAINER_ARGS with the driven
                # browser, and whether those carry --no-sandbox is decided by
                # launching once. Decide before this window opens, so the
                # first sign-in of a fresh install does not open a Chrome that
                # dies on the sandbox with nothing to say why.
                playwright = await self._ensure_playwright()
                await resolve_channel(playwright, profile_path)
            process = launch_detached(profile_path, url)
        except Exception:
            entry.lease = None
            entry.lock.release()
            raise
        entry.process = process

        opened_at = asyncio.get_event_loop().time()

        async def _wait() -> None:
            # For `auto_finish`: how the display has looked so far. A window has
            # to have been seen before its absence can mean anything, and it has
            # to be absent for several polls running, so a moment between pages
            # is never read as the end.
            seen_window = False
            blank_polls = 0
            asked_to_close = False
            last_titles: list[str] | None = None
            try:
                # Polled rather than awaited: `Popen` is not an asyncio object,
                # and a thread per sign-in window to call `wait()` would be a
                # thread that outlives the window on a shutdown.
                while process.poll() is None:
                    await asyncio.sleep(2)
                    if not auto_finish:
                        continue
                    # Ask the X server what is on the screen. In a thread: it is
                    # a subprocess call, and the event loop is also serving the
                    # screen view's frames.
                    titles = await asyncio.to_thread(window_titles)
                    if titles is None:
                        continue  # cannot look here; leave it to the person
                    if titles != last_titles:
                        # The measurement this exists for: what the checkout
                        # actually leaves on the screen, in the log, so the next
                        # version is written against it rather than a guess.
                        logger.info(
                            "Account %s: the window on the screen is now %s.",
                            account_id,
                            titles or "gone (no visible browser window)",
                        )
                        last_titles = titles
                    if titles:
                        seen_window = True
                        blank_polls = 0
                        continue
                    if not seen_window or asked_to_close:
                        continue  # not up yet, or already on its way out
                    blank_polls += 1
                    if blank_polls >= BLANK_POLLS_BEFORE_FINISH:
                        logger.info(
                            "Account %s: the browser has had no window for %.0f s, "
                            "so the checkout has closed itself. Finishing up.",
                            account_id,
                            blank_polls * 2.0,
                        )
                        try:
                            process.terminate()
                        except OSError as exc:  # pragma: no cover - already gone
                            logger.debug("Closing the finished window: %s", exc)
                        # Not a `break`: let the loop see the process actually
                        # exit, so the lock is released and the profile is read
                        # only once Chrome has let go of it.
                        asked_to_close = True
                # A window nobody could have used. Chrome dying on launch -
                # the sandbox, a missing library, a display that is not there -
                # looks from the interface like a window that was closed at
                # once, so say what happened where somebody will read it.
                if asyncio.get_event_loop().time() - opened_at < 4:
                    logger.warning(
                        "The sign-in window for account %s exited within seconds "
                        "(exit code %s). Chrome did not come up; in a container "
                        "this is usually the sandbox, the display or a missing "
                        "library. `python -m app.diagnose` will say which.",
                        account_id,
                        process.returncode,
                    )
            finally:
                entry.lease = None
                entry.process = None
                if entry.lock.locked():
                    entry.lock.release()
                logger.info("The sign-in window for account %s has closed.", account_id)

            if on_closed is not None:
                # After the lock is released, because the callback wants the
                # profile itself - and after Chrome has actually let go of it,
                # not after a fixed sleep. Opening it too soon reads the cookie
                # database mid-flush, which reports the session the person just
                # made as absent. See `wait_for_profile_release`.
                await wait_for_profile_release(profile_path)
                try:
                    await on_closed()
                except Exception:
                    logger.exception(
                        "The check after the sign-in window closed failed for "
                        "account %s.",
                        account_id,
                    )

        task = asyncio.create_task(_wait(), name=f"trove-signin-{account_id}")
        self._signin_tasks.add(task)
        task.add_done_callback(self._signin_tasks.discard)

    def close_local(self, account_id: int) -> bool:
        """Ask the account's sign-in window to close. Returns whether there was one.

        A polite terminate, not a kill: Chrome flushes its profile to disk on
        SIGTERM (and on the close Windows sends), which is the whole point - the
        session the person just created has to reach the directory. The waiter
        in `open_local` sees the exit and releases the lock as usual.
        """
        entry = self._accounts.get(account_id)
        if entry is None or entry.process is None or entry.process.poll() is not None:
            return False
        try:
            entry.process.terminate()
        except OSError as exc:  # pragma: no cover - already gone
            logger.debug("Closing the sign-in window for %s: %s", account_id, exc)
            return False
        return True

    async def stop(self) -> None:
        """Shut Playwright down. Called from the app's lifespan."""
        for entry in self._accounts.values():
            if entry.context is not None:
                try:
                    await entry.context.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:  # pragma: no cover
                logger.debug("Stopping Playwright: %s", exc)
            self._playwright = None


manager = BrowserManager()


async def first_page(context: BrowserContext) -> Page:
    """The context's page, made if it does not have one, and told it has focus.

    A persistent context opens with one blank page already. Making a second and
    leaving the first is how a run ends up driving a tab the user cannot see in
    the live view.

    The focus emulation matters as much here as it does in the live view. A run
    happens in a window that is behind everything, or in a container with no
    window manager at all, so the page reports `document.hasFocus() === false` -
    and a store's challenge reads that as a page nobody is looking at. See
    `live.enable_focus` for why this is a removal rather than a forgery.
    """
    page = context.pages[0] if context.pages else await context.new_page()
    try:
        from .live import enable_focus

        cdp = await context.new_cdp_session(page)
        await enable_focus(cdp)
        # The session is left attached deliberately. Detaching it drops the
        # emulation with it, and the run needs the page to keep believing it is
        # focused for as long as it is being driven.
    except Exception as exc:  # pragma: no cover - never worth failing a run for
        logger.debug("Could not enable focus emulation for the run: %s", exc)
    return page


async def screenshot(page: Page, name: str) -> str | None:
    """Save a full-page screenshot and return its name, or None.

    The evidence for an attention item. Best effort by design: a page that has
    already navigated away, or a browser that has crashed, must not turn a
    "needs attention" into a "failed" - the user still needs to be told about
    the first thing.
    """
    path = settings.screenshots_path / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=False)
        return name
    except Exception as exc:
        logger.warning("Could not take the screenshot %s: %s", name, exc)
        return None


def screenshot_name(account_id: int, store: str, tag: str) -> str:
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")[:40] or "shot"
    return f"{account_id:04d}-{store}-{stamp}-{safe}.png"


async def close_browser(browser: Browser | None) -> None:  # pragma: no cover
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass
