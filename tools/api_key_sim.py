"""API keys, end to end through the real app, against a throwaway database.

What it pins:

  1. A key is issued once, from a signed-in session, and only its hash is
     stored - the plaintext appears in the create response and nowhere else.
  2. A full key reads and writes; a read-only key reads and is refused a write
     with a 403 that says so; a revoked or made-up key is a 401.
  3. Whatever its scope, a key never reaches what a person must be present
     for: issuing keys, the password, revealing a game key, typing stored
     sign-in details. Those answer 403 with a sentence, not a bare 401.
  4. `last_used_at` is stamped on use.

No browser, no network, no lifespan (the scheduler never starts).

    backend/.venv/Scripts/python tools/api_key_sim.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="trove-apikey-sim-")
os.environ["ADMIN_PASSWORD"] = "sim-password-123"

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import bootstrap_admin  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, ApiKey, Claim  # noqa: E402
from app.config import get_settings  # noqa: E402

init_db()
bootstrap_admin()

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    person = TestClient(app)
    username = get_settings().admin_username
    r = person.post("/api/auth/login", json={"username": username, "password": "sim-password-123"})
    check("the person signs in", r.status_code == 200, r.text)

    # Something to read and something a key must not reveal.
    db = SessionLocal()
    account = Account(label="Sim", store="epic", profile_path="0001-sim", status="ok")
    db.add(account)
    db.commit()
    claim = Claim(account_id=account.id, store="epic", title="Sim game", outcome="claimed")
    db.add(claim)
    db.commit()
    account_id, claim_id = account.id, claim.id
    db.close()

    print("Issuing:")
    r = person.post("/api/keys", json={"name": "Claude", "scope": "full"})
    check("a full key is issued", r.status_code == 201, r.text)
    full = r.json()["key"]
    check("it starts trv_", full.startswith("trv_"))
    r = person.post("/api/keys", json={"name": "Dashboard", "scope": "read_only"})
    read_only = r.json()["key"]
    listed = person.get("/api/keys").json()
    check("the list never carries the plaintext", all("key" not in k for k in listed))
    db = SessionLocal()
    stored = [k.key_hash for k in db.query(ApiKey).all()]
    db.close()
    check("only hashes are stored", full not in stored and read_only not in stored and len(stored) == 2)

    def as_key(key: str, header: str = "X-API-Key") -> TestClient:
        client = TestClient(app)
        client.headers[header] = key if header == "X-API-Key" else f"Bearer {key}"
        return client

    print("Access:")
    k = as_key(full)
    check("full key reads accounts", k.get("/api/accounts").status_code == 200)
    check("Bearer works too", as_key(full, "Authorization").get("/api/accounts").status_code == 200)
    r = k.patch(f"/api/accounts/{account_id}", json={"notes": "from a key"})
    check("full key writes", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    ro = as_key(read_only)
    check("read-only key reads", ro.get("/api/claims").status_code == 200)
    r = ro.patch(f"/api/accounts/{account_id}", json={"notes": "nope"})
    check("read-only key is refused a write (403)", r.status_code == 403, f"{r.status_code}")
    check("a made-up key is a 401", as_key("trv_notarealkey000000").get("/api/accounts").status_code == 401)
    check("no credential at all is a 401", TestClient(app).get("/api/accounts").status_code == 401)

    print("What a key never reaches:")
    for label, call in (
        ("issuing keys", lambda c: c.post("/api/keys", json={"name": "x"})),
        ("listing keys", lambda c: c.get("/api/keys")),
        ("the password", lambda c: c.post("/api/auth/password", json={"current_password": "sim-password-123", "new_password": "whatever-123"})),
        ("revealing a game key", lambda c: c.get(f"/api/claims/{claim_id}/key")),
        ("typing stored details", lambda c: c.post(f"/api/accounts/{account_id}/type", json={"what": "password"})),
    ):
        r = call(k)
        check(f"{label}: 403 for a full key", r.status_code == 403, f"{r.status_code} {r.text[:120]}")

    print("Bookkeeping:")
    db = SessionLocal()
    used = {k.name: k.last_used_at for k in db.query(ApiKey).all()}
    db.close()
    check("last_used_at stamped on the key that was used", used["Claude"] is not None)

    key_id = next(x["id"] for x in listed if x["name"] == "Claude")
    check("revoking answers 204", person.delete(f"/api/keys/{key_id}").status_code == 204)
    check("a revoked key is a 401", as_key(full).get("/api/accounts").status_code == 401)
    listed = person.get("/api/keys").json()
    check("revoked keys stay listed, after the live ones",
          [x["name"] for x in listed] == ["Dashboard", "Claude"] and listed[1]["revoked_at"])

    print("PASS" if not failures else f"FAILED ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
