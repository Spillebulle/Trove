"""A message about an account links to that account's page in Trove.

Pins `notify.with_trove_link`: the link is built from the `notify.public_url`
setting, only for an http(s) address, never over a link the message already
has (a claim links to the game), and trailing slashes do not double up.

    backend/.venv/Scripts/python tools/notify_link_sim.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trove-notify-sim-"))

from app import notify, settings_store  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"  (got {got!r}, want {want!r})"))
    if not ok:
        failures.append(label)


N = notify.Notification
link = notify.with_trove_link
check("an account message links to its page",
      link(N("t", account_id=3), "https://trove.example.com").url, "https://trove.example.com/accounts/3")
check("a trailing slash does not double up",
      link(N("t", account_id=3), "https://trove.example.com/ ").url, "https://trove.example.com/accounts/3")
check("a message's own link wins (a claim links to the game)",
      link(N("t", account_id=3, url="https://store/p/game"), "https://trove.example.com").url, "https://store/p/game")
check("no address set, no link", link(N("t", account_id=3), "").url, None)
check("not an http(s) address, no link", link(N("t", account_id=3), "trove.local").url, None)
check("no account, no link", link(N("t"), "https://trove.example.com").url, None)
check("the setting exists, empty by default", settings_store.DEFAULTS["notify.public_url"], ("", False))

print("PASS" if not failures else f"FAILED ({len(failures)})")
raise SystemExit(1 if failures else 0)
