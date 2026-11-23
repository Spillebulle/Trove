"""The keys-to-redeem page's API, through the real app, on a throwaway database.

What it pins:

  1. `/api/redeem` lists only claims that carry a key, still-to-redeem first,
     and never the key itself.
  2. Marking a key redeemed (and back) stamps and clears `key_redeemed_at` and
     moves it down the list; a claim with no key answers 404.
  3. A read-only API key may list but not mark, and no API key can reveal a
     key's value - that stays a signed-in person's request.

No browser, no network, no lifespan (the scheduler never starts).

    backend/.venv/Scripts/python tools/redeem_sim.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="trove-redeem-sim-")
os.environ["ADMIN_PASSWORD"] = "sim-password-123"

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import bootstrap_admin  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Claim  # noqa: E402
from app.timeutil import utcnow  # noqa: E402

init_db()
bootstrap_admin()
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    person = TestClient(app)
    r = person.post("/api/auth/login", json={"username": get_settings().admin_username, "password": "sim-password-123"})
    check("the person signs in", r.status_code == 200, r.text)

    db = SessionLocal()
    account = Account(label="Sim", store="prime", profile_path="0001-sim", status="ok")
    db.add(account)
    db.commit()
    now = utcnow()
    older = Claim(account_id=account.id, store="prime", title="Older key", outcome="claimed",
                  key_code=encrypt("AAAAA-BBBBB-CCCCC"), key_store="steam", created_at=now - timedelta(days=2))
    newer = Claim(account_id=account.id, store="prime", title="Newer key", outcome="claimed",
                  key_code=encrypt("GOG-1234"), key_store="gog", created_at=now - timedelta(days=1))
    plain = Claim(account_id=account.id, store="epic", title="Library game", outcome="claimed")
    db.add_all([older, newer, plain])
    db.commit()
    older_id, newer_id, plain_id = older.id, newer.id, plain.id
    db.close()

    print("Listing:")
    rows = person.get("/api/redeem").json()
    check("only the two keyed claims", [r["title"] for r in rows] == ["Newer key", "Older key"], str([r["title"] for r in rows]))
    check("no key value in the list", all("key_code" not in r for r in rows) and "AAAAA" not in str(rows))
    check("both still to redeem", all(r["key_redeemed_at"] is None for r in rows))

    print("Marking:")
    r = person.post(f"/api/claims/{newer_id}/redeemed", json={"redeemed": True})
    check("marking answers the claim, stamped", r.status_code == 200 and r.json()["key_redeemed_at"], r.text)
    rows = person.get("/api/redeem").json()
    check("a redeemed key sinks below the to-do ones", [r["title"] for r in rows] == ["Older key", "Newer key"])
    r = person.post(f"/api/claims/{newer_id}/redeemed", json={"redeemed": False})
    check("and can be put back", r.status_code == 200 and r.json()["key_redeemed_at"] is None)
    r = person.post(f"/api/claims/{plain_id}/redeemed", json={"redeemed": True})
    check("a claim with no key is a 404", r.status_code == 404)
    r = person.get(f"/api/claims/{older_id}/key")
    check("the person can reveal the key", r.status_code == 200 and r.json()["key_code"] == "AAAAA-BBBBB-CCCCC")

    print("API keys:")
    full = person.post("/api/keys", json={"name": "Full", "scope": "full"}).json()["key"]
    ro = person.post("/api/keys", json={"name": "RO", "scope": "read_only"}).json()["key"]
    bot = TestClient(app)
    r = bot.get("/api/redeem", headers={"X-API-Key": ro})
    check("a read-only key may list", r.status_code == 200 and len(r.json()) == 2)
    r = bot.post(f"/api/claims/{older_id}/redeemed", json={"redeemed": True}, headers={"X-API-Key": ro})
    check("a read-only key may not mark", r.status_code == 403)
    r = bot.post(f"/api/claims/{older_id}/redeemed", json={"redeemed": True}, headers={"X-API-Key": full})
    check("a full key may mark", r.status_code == 200)
    r = bot.get(f"/api/claims/{older_id}/key", headers={"X-API-Key": full})
    check("no API key can reveal a key's value", r.status_code == 403)

    print("PASS" if not failures else f"FAILED ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
