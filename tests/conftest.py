import hashlib
import hmac
import json
import time
import os
from uuid import uuid4
import psycopg
from psycopg import sql
from dotenv import load_dotenv
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.seed import create_tenant
from config import Settings, ROOT


class FakeStripe:
    """Deterministic boundary fake; never represented as a real Stripe Checkout."""
    def __init__(self):
        self.subs = []
        self.fail = False
        self.checkout_keys = []
        self.session_status = "open"

    def require_config(self):
        pass

    def create_customer(self, tenant_id):
        return {"id": "cus_" + tenant_id, "livemode": False}

    def subscriptions(self, customer_id):
        if self.fail:
            raise RuntimeError("Sensitive upstream information must not be stored")
        return [s for s in self.subs if s["customer"] == customer_id]

    def create_checkout(self, customer_id, tenant_id, key):
        self.checkout_keys.append(key)
        return {"id": "cs_test_" + tenant_id, "url": "https://checkout.stripe.com/test-fixture",
                "expires_at": int(time.time()) + 86400, "livemode": False}

    def retrieve_checkout(self, session_id):
        return {"id": session_id, "url": "https://checkout.stripe.com/test-fixture", "status": self.session_status}


def subscription(customer="cus_alpha", status="active", sub_id="sub_pro", price="price_pro", created=100):
    return {"id": sub_id, "customer": customer, "status": status, "created": created, "livemode": False,
            "items": {"data": [{"price": {"id": price}}]}}


def event(event_id="evt_checkout", event_type="checkout.session.completed", customer="cus_alpha"):
    return {"id": event_id, "type": event_type, "livemode": False,
            "data": {"object": {"id": "cs_test_alpha", "customer": customer}}}


def signed(event_data, secret="whsec_fixture", timestamp=None):
    raw = json.dumps(event_data).encode()
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, {"Stripe-Signature": f"t={timestamp},v1={digest}", "Content-Type": "application/json"}


@pytest.fixture
def postgres_db():
    load_dotenv(ROOT / ".env")
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.fail("Set TEST_DATABASE_URL to a PostgreSQL database where the test user can create schemas.")
    schema = "test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    from app.postgres import Database
    db = Database(dsn, schema)
    try:
        yield db
    finally:
        # Only drop the randomly named schema created by this fixture, never public.
        assert schema.startswith("test_") and len(schema) == 37
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def system(postgres_db):
    fake = FakeStripe()
    settings = Settings(database_url=postgres_db.dsn, database_schema=postgres_db.schema, stripe_secret_key="sk_test_fixture",
                        stripe_webhook_secret="whsec_fixture", stripe_pro_price_id="price_pro")
    app = create_app(settings, fake)
    with TestClient(app) as client:
        alpha = create_tenant(app.state.db, "Alpha", "alpha")
        beta = create_tenant(app.state.db, "Beta", "beta")
        with app.state.db.transaction() as conn:
            conn.execute("UPDATE tenants SET stripe_customer_id='cus_alpha' WHERE id='alpha'")
        yield app, client, fake, alpha, beta


def auth(tenant, key=None):
    headers = {"Authorization": "Bearer " + tenant["api_key"]}
    if key:
        headers["Idempotency-Key"] = key
    return headers
