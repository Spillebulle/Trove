"""Browser sessions: one persistent profile per account, and who may use it.

This is the module the whole design turns on. CLAUDE.md's first rule is
sessions, not logins: an account signs in once, by hand, and what Trove keeps
afterwards is a Chromium profile directory holding the cookies, the local
storage and the device fingerprint that sign-in produced. There is no password
column anywhere in this app, and adding one would undo the design rather than
extend it.

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
from .timeutil import utcnow

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
    ):
        """Open the account's profile and yield its context.

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
        try:
            # Absolute, always. Real Chrome refuses a relative user-data-dir
            # with a modal dialog that presents only as a launch timeout.
            profile_path = profile_path.resolve()
            profile_path.mkdir(parents=True, exist_ok=True)
            channel = await resolve_channel(playwright, profile_path)
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
            if context is not None:
                try:
                    await context.close()
                except Exception as exc:  # pragma: no cover - teardown noise
                    logger.debug("Closing the context for %s: %s", account_id, exc)
            entry.lock.release()

    async def open_local(
        self, account_id: int, profile_path: Path, url: str, on_closed=None
    ) -> None:
        """Open the profile in an un-driven browser and hold it until it closes.

        Takes the same per-account lock every other path takes, so a scheduled
        run cannot open the profile while somebody is signing in to it - which
        would corrupt it, since two Chromiums must never share a user-data
        directory. The lock is released when the window is closed, which is
        what makes "close the window when you are done" the whole of the user's
        side of the contract.
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
            try:
                # Polled rather than awaited: `Popen` is not an asyncio object,
                # and a thread per sign-in window to call `wait()` would be a
                # thread that outlives the window on a shutdown.
                while process.poll() is None:
                    await asyncio.sleep(2)
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
                # profile itself. Chrome takes a moment to let go of its files
                # on Windows, and opening the profile too soon gets a lock error
                # that reads as a failed sign-in rather than as a race.
                await asyncio.sleep(3)
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
