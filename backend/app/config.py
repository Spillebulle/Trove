"""Configuration, from the environment.

One settings object, cached. Two values are generated on first boot and
persisted beside the database rather than demanded of the user: the session
secret and the encryption key. A self-hosted app that refuses to start until
somebody generates a Fernet key is an app most people never start.
"""
from __future__ import annotations

import os
import secrets
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """`/data` in the container, `./data` on a development machine.

    The container mounts a volume at `/data` and that is the only sane default
    there. On Windows an absolute `/data` resolves onto the current drive's
    root, which is outside the project and usually not writable, so a
    developer would hit a permission error before ever seeing a page.
    `DATA_DIR` overrides both.
    """
    if os.name == "nt":
        return Path("./data")
    return Path("/data")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Trove"
    data_dir: Path = Field(default_factory=_default_data_dir)
    log_level: str = "INFO"

    # --- Server ----------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080
    # Only needed for a dev frontend on another origin. In production the API
    # serves the built SPA from the same origin and this stays empty.
    #
    # A comma-separated string rather than a `list[str]`, and that is not a
    # style choice. pydantic-settings JSON-decodes a complex field before any
    # validator runs, so `CORS_ORIGINS=http://localhost:5173` in a .env file
    # fails to parse and takes the whole app down at import time with an error
    # that names the field and not the reason. A string always parses; the
    # splitting happens in `cors_origin_list` below.
    cors_origins: str = ""

    # --- Security --------------------------------------------------------
    # Signs the session cookie. Generated into <data_dir>/.secret_key if unset.
    secret_key: str = ""
    session_ttl_days: int = 30
    # Fernet key for TOTP secrets, claimed game keys and webhook URLs at rest.
    # Generated into <data_dir>/.credential_key if unset.
    credential_key: str = ""
    # The single user is bootstrapped with this password if no user exists yet.
    # Left unset it is "changeme", and the app says so loudly in the log.
    admin_username: str = "admin"
    admin_password: str = ""

    # --- Browser ---------------------------------------------------------
    # Headed is the default, and that is not a preference. CLAUDE.md's whole
    # design is not to be detected, and `headless=new` is one of the signals
    # bot detection reads. In Docker the entrypoint supplies an Xvfb display so
    # headed still works there. Set TROVE_HEADLESS=true only to measure it.
    headless: bool = False

    # Which browser to drive. "auto" uses real Google Chrome when it is
    # installed and falls back to Playwright's bundled Chromium; "chromium"
    # forces the bundle; "chrome" or "msedge" force that channel and fail
    # loudly if it is absent.
    #
    # This is not a preference either, and it is the single most important
    # setting in this file. Playwright's Chromium is built **without the
    # proprietary codecs**: measured side by side, it answers "no" to H.264,
    # HEVC and AAC where real Chrome answers "probably", and its
    # `userAgentData.brands` never contains "Google Chrome" - while its
    # user-agent string claims to be Chrome. Cloudflare's Turnstile probes
    # exactly those codecs, which was visible in the browser console as
    # "Failed to parse video contentType: video/mp4; codecs=avc1.42E01E".
    # A browser that says it is Chrome and cannot play what Chrome plays is a
    # browser that fails the challenge however honestly a person clicks it.
    browser_channel: str = "auto"
    # Where the per-account browser profiles live. One directory per account,
    # never shared. Inside the data volume, so a backup takes the sessions too.
    profile_dir: Path | None = None
    # How long a Playwright action may take before the run gives up and files
    # the account for attention. Generous: a store's checkout is slow.
    browser_timeout_ms: int = 45000
    # How long an idle live-view browser stays open before it is closed. The
    # user opens the window, signs in, and wanders off; the browser must not
    # sit there holding a profile lock forever.
    live_idle_timeout_s: int = 600

    # --- The live view's picture -----------------------------------------
    #
    # These two decide the bandwidth, and the default used to be "as fast as
    # possible", which is not a setting anybody chose. The screencast sends one
    # frame per acknowledgement, Trove acknowledged immediately, and a store
    # page with a carousel on it therefore rendered flat out: **measured at 113
    # frames a second and 7.7 MB/s**, or 62 Mbit/s, which no home upstream link
    # survives. It presents as the picture lagging further and further behind,
    # because the frames queue in buffers between here and the browser.
    #
    # Twelve is enough to sign in and answer a captcha, and it is what a remote
    # desktop feels like rather than what a video feels like. Raise it on a LAN.
    live_max_fps: int = 12
    # JPEG quality. A store page is text and flat colour, which JPEG handles
    # well; 50 is visibly fine and about a third smaller than 60.
    live_quality: int = 50
    # The frame's longest side. The viewport stays 1280x800 - this only scales
    # the picture of it, so clicks are unaffected - and 1024 is a third fewer
    # bytes again for something nobody is reading text off.
    live_max_width: int = 1024

    # --- Schedule --------------------------------------------------------
    # The default gap between runs for an account that does not set its own.
    # Hours, not minutes: CLAUDE.md's human cadence, and a store's weekly
    # giveaway does not reward polling.
    default_interval_hours: int = 8
    # Jitter, as a fraction of the interval. A run at exactly 06:00:00 every
    # day is a signature; plus or minus 15 % of eight hours is a dull smear.
    interval_jitter: float = 0.15
    # A floor, so a typo in the UI cannot turn a claimer into a hammer.
    min_interval_hours: int = 1

    # Set by the Dockerfile. There is no reason to set it by hand.
    in_container: bool = False

    @property
    def has_visible_desktop(self) -> bool:
        """Could a person actually see a browser window Trove opened?

        Not the same question as "is there a DISPLAY". The container sets one -
        Xvfb, so headed Chrome has somewhere to draw - but nobody is looking at
        it, so opening a sign-in window there would put it somewhere the user
        can never reach while telling them to go and use it. A control that
        lies is worse than none.
        """
        if self.headless:
            return False
        if self.in_container or Path("/.dockerenv").exists():
            return False
        if os.name == "nt" or sys.platform == "darwin":
            return True
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "trove.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def profiles_path(self) -> Path:
        """Where the per-account browser profiles live. **Always absolute.**

        The `resolve()` is a bug fix, not tidiness. `DATA_DIR` defaults to
        `./data` on Windows, which makes every profile path relative, and real
        Chrome *refuses a relative `--user-data-dir`*: it puts up a dialog
        saying it "cannot read and write to its own data directory" and then
        hangs until somebody dismisses it, which Playwright reports only as a
        launch timeout. Playwright's bundled Chromium tolerates the relative
        path, so this stayed hidden until Trove was pointed at real Chrome.
        """
        return (self.profile_dir or (self.data_dir / "profiles")).resolve()

    @property
    def screenshots_path(self) -> Path:
        return (self.data_dir / "screenshots").resolve()


class DataDirectoryError(RuntimeError):
    """The data directory exists but Trove cannot write to it."""


def _persisted(path: Path, generate) -> str:
    """Read a generated-once value from disk, creating it on the first call.

    O_CREAT with O_EXCL rather than a check-then-write: the lifespan hook and
    an early scheduler tick can both reach this on a cold start, and two
    workers each generating their own key would leave one of them encrypting
    with a key the file no longer holds.
    """
    if path.exists():
        value = path.read_text().strip()
        if value:
            return value
    value = generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, "w") as handle:
        handle.write(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows / non-POSIX
    return value


def _prepare_data_dir(settings: Settings) -> None:
    """Create the data directory, with an error that says how to fix it.

    A bind-mounted volume owned by the wrong user is the commonest setup
    mistake, and a bare PermissionError traceback says nothing useful about it.
    """
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.profiles_path.mkdir(parents=True, exist_ok=True)
        settings.screenshots_path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise DataDirectoryError(
            f"Cannot write to the data directory {settings.data_dir}.\n"
            "In Docker this usually means the mounted volume is owned by a "
            "different user than the container. Fix it with either:\n"
            "  - PUID/PGID environment variables matching the directory's "
            "owner (run `id -u` and `id -g` on the host), or\n"
            "  - `sudo chown -R 1000:1000 ./data` on the host."
        ) from exc


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    _prepare_data_dir(settings)

    if not settings.secret_key:
        settings.secret_key = _persisted(
            settings.data_dir / ".secret_key", lambda: secrets.token_urlsafe(48)
        )
    if not settings.credential_key:
        from cryptography.fernet import Fernet

        settings.credential_key = _persisted(
            settings.data_dir / ".credential_key",
            lambda: Fernet.generate_key().decode("ascii"),
        )
    return settings
