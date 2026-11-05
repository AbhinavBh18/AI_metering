import hashlib
import json
from pathlib import Path
import secrets
from app.metering import utc_now
from config import ROOT


def create_tenant(db, name, tenant_id=None):
    tenant_id = tenant_id or "tenant_" + secrets.token_hex(8)
    key = "meter_" + secrets.token_urlsafe(32)
    with db.transaction() as conn:
        conn.execute("INSERT INTO tenants(id,name,api_key_hash,created_at) VALUES (%s,%s,%s,%s)",
                     (tenant_id, name, hashlib.sha256(key.encode()).hexdigest(), utc_now().isoformat()))
        conn.execute("INSERT INTO subscriptions(tenant_id) VALUES (%s)", (tenant_id,))
    return {"tenant_id": tenant_id, "name": name, "api_key": key}


def seed_demo(db):
    # Credentials are random and deliberately written only to git-ignored local data.
    path = ROOT / "data" / "demo-credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with db.read() as conn:
        exists = conn.execute("SELECT COUNT(*) AS count FROM tenants WHERE id IN ('demo-alpha','demo-beta')").fetchone()["count"]
    if exists:
        return {"message": "Demo tenants already exist; no keys or usage were reset.", "credentials_file": str(path)}
    records = [create_tenant(db, "Acme AI Studio", "demo-alpha"), create_tenant(db, "Orbit Research", "demo-beta")]
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return {"message": "Created two isolated Free tenants.", "credentials_file": str(path)}
