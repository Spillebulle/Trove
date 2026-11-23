"""A sign-in made with browser-session cookies survives the handover to a run.

Pins `browser.quiet_profile`'s two halves (the live measurement is in
CLAUDE.md, "A login that lasts until the browser closes"):

  1. it turns on "continue where you left off" (`session.restore_on_startup`
     = 1), which is what makes Chrome keep session cookies across a restart,
     merging into the profile's own preferences rather than replacing them;
  2. it clears both saved-tab folders, so that setting restores no tabs - and
     does so even when the preferences needed no change.

No browser, no network.

    backend/.venv/Scripts/python tools/session_keep_sim.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trove-session-sim-"))

from app import browser  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)


profile = Path(tempfile.mkdtemp()) / "profile"
default = profile / "Default"
default.mkdir(parents=True)
(default / "Preferences").write_text(json.dumps({"profile": {"name": "Person"}, "session": {"startup_urls": ["x"]}}))
for name in browser.SESSION_TAB_DIRS:
    (default / name).mkdir()
    (default / name / "Tabs_1").write_text("tab")
(default / "Cookies").write_text("not touched")

browser.quiet_profile(profile)
prefs = json.loads((default / "Preferences").read_text())
check("continue-where-you-left-off is on", prefs.get("session", {}).get("restore_on_startup") == 1)
check("the profile's own preferences are kept", prefs["profile"].get("name") == "Person"
      and prefs["session"].get("startup_urls") == ["x"])
check("the pop-up preferences still land", prefs.get("credentials_enable_service") is False)
check("both saved-tab folders are gone", not any((default / n).exists() for n in browser.SESSION_TAB_DIRS))
check("the cookie database is untouched", (default / "Cookies").read_text() == "not touched")

for name in browser.SESSION_TAB_DIRS:  # a second launch: prefs unchanged, tabs back
    (default / name).mkdir()
browser.quiet_profile(profile)
check("tabs are cleared even when no preference changed",
      not any((default / n).exists() for n in browser.SESSION_TAB_DIRS))

fresh = Path(tempfile.mkdtemp()) / "never-opened"
browser.quiet_profile(fresh)
check("a never-opened profile gets the setting too",
      json.loads((fresh / "Default" / "Preferences").read_text())["session"]["restore_on_startup"] == 1)

print("PASS" if not failures else f"FAILED ({len(failures)})")
raise SystemExit(1 if failures else 0)
