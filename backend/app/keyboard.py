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


def press_key(key: str) -> None:
    """Press one named X key (Return, Tab) in the focused window."""
    tool = _xdotool()
    result = subprocess.run([tool, "key", "--", key], capture_output=True, timeout=15)
    if result.returncode != 0:
        raise TypingUnavailable(
            f"xdotool could not press {key}: {result.stderr.decode(errors='replace').strip()[:200]}"
        )
