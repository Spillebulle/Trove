"""Typing into the container's screen, through the X server.

`xdotool` sends synthetic key events with the XTEST extension to whatever
window has focus on the display - which, on the container's screen, is the
account's un-driven Chrome. It is how a stored email, password or TOTP code
gets into the sign-in form without the person typing it key by key through a
remote picture, and it is the same mechanism a desktop password manager uses.
Nothing is attached to the browser; it sees keystrokes.

The text goes to xdotool on stdin (`--file -`), never on the command line,
where it would sit in /proc for the length of the call.

Only meaningful where there is a display to type into, which is the container.
On a desktop "sign in here" opens a window in front of the person and they
have their own tools.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


class TypingUnavailable(RuntimeError):
    """There is no way to type into a screen from here."""


def _xdotool() -> str:
    if not os.environ.get("DISPLAY"):
        raise TypingUnavailable("There is no display to type into.")
    path = shutil.which("xdotool")
    if not path:
        raise TypingUnavailable("xdotool is not installed in this image, so Trove cannot type for you.")
    return path


def type_text(text: str, delay_ms: int = 35) -> None:
    """Type `text` into the focused window, at roughly a person's pace."""
    tool = _xdotool()
    result = subprocess.run(
        [tool, "type", "--delay", str(delay_ms), "--file", "-"],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise TypingUnavailable(
            f"xdotool could not type: {result.stderr.decode(errors='replace').strip()[:200]}"
        )


def has_visible_window(cls: str = "chrome") -> bool | None:
    """Is there a mapped, visible window of this class on the display?

    Used to notice when a browser window has gone away on its own. Epic's
    checkout **closes its own window once the order is placed** - measured on a
    real claim: the person pressed "Add to library", accepted, and the screen
    went black, which on an Xvfb with no window manager is exactly what "no
    windows" looks like. That black screen is the finish, not a fault, and this
    is how Trove can tell rather than leaving somebody staring at it.

    Returns None when the question cannot be asked here (no display, no
    xdotool), which is a different answer from False and must not be turned
    into one: "I could not look" is not "the window is gone".
    """
    try:
        tool = _xdotool()
    except TypingUnavailable:
        return None
    try:
        result = subprocess.run(
            [tool, "search", "--onlyvisible", "--class", cls],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Could not ask the display for windows: %s", exc)
        return None
    # xdotool exits 1 with no output when nothing matches, which is the answer
    # rather than an error.
    return bool(result.stdout.strip())


def window_titles(cls: str = "chrome") -> list[str] | None:
    """The titles of this class's visible windows, or None if it cannot be asked.

    A window's title is a property on the X server, not something read out of
    the page, so this stays on the right side of the line: nothing is attached
    to the browser. It is here to *measure* what Epic leaves on the screen when
    a checkout finishes - the black screen a real claim ended on - so the next
    version can act on what is there rather than on a guess about it.
    """
    try:
        tool = _xdotool()
    except TypingUnavailable:
        return None
    try:
        found = subprocess.run(
            [tool, "search", "--onlyvisible", "--class", cls],
            capture_output=True,
            timeout=10,
        )
        ids = [i for i in found.stdout.decode(errors="replace").split() if i.strip()]
        titles: list[str] = []
        for window_id in ids[:8]:  # a browser has a handful; do not walk a list
            named = subprocess.run(
                [tool, "getwindowname", window_id],
                capture_output=True,
                timeout=10,
            )
            titles.append(named.stdout.decode(errors="replace").strip())
        return titles
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Could not read window titles: %s", exc)
        return None


def press_key(key: str) -> None:
    """Press one named X key (Return, Tab) in the focused window."""
    tool = _xdotool()
    result = subprocess.run([tool, "key", "--", key], capture_output=True, timeout=15)
    if result.returncode != 0:
        raise TypingUnavailable(
            f"xdotool could not press {key}: {result.stderr.decode(errors='replace').strip()[:200]}"
        )
