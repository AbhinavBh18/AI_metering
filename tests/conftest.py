import hashlib
import hmac
import json
import time
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.seed import create_tenant
from config import Settings


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
def system(tmp_path):
    fake = FakeStripe()
    settings = Settings(database_path=str(tmp_path / "test.db"), stripe_secret_key="sk_test_fixture",
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
