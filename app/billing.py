import hashlib
import hmac
import json
import time
from uuid import uuid4

from app.errors import DomainError
from app.metering import utc_now

EVENT_TYPES = {"checkout.session.completed", "customer.subscription.created",
               "customer.subscription.updated", "customer.subscription.deleted"}


def verify_event(raw, signature, secret, now=None):
    if not secret:
        raise DomainError(503, "webhook_not_configured", "Configure the Stripe webhook signing secret.")
    try:
        fields = [part.split("=", 1) for part in signature.split(",")]
        timestamp = next(int(v) for k, v in fields if k == "t")
        signatures = [v for k, v in fields if k == "v1"]
        if abs((time.time() if now is None else now) - timestamp) > 300:
            raise ValueError("stale")
        expected = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, value) for value in signatures):
            raise ValueError("invalid")
        event = json.loads(raw)
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"].startswith("evt_"):
            raise ValueError("event")
        if event.get("livemode") is not False:
            raise ValueError("test mode required")
        if not isinstance(event.get("type"), str) or not isinstance(event.get("data", {}).get("object"), dict):
            raise ValueError("shape")
        return event
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
        raise DomainError(400, "invalid_webhook", "Invalid, expired, or non-test-mode Stripe webhook.") from None


class BillingService:
    def __init__(self, db, gateway):
        self.db, self.gateway = db, gateway

    def checkout(self, tenant_id):
        self.gateway.require_config()
        with self.db.read() as conn:
            tenant = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        customer_id = tenant["stripe_customer_id"]
        if not customer_id:
            customer_id = self.gateway.create_customer(tenant_id)["id"]
            with self.db.transaction() as conn:
                conn.execute("UPDATE tenants SET stripe_customer_id=COALESCE(stripe_customer_id,?) WHERE id=?", (customer_id, tenant_id))
                customer_id = conn.execute("SELECT stripe_customer_id FROM tenants WHERE id=?", (tenant_id,)).fetchone()[0]
        # Do not start a second subscription while webhook delivery is delayed.
        current = self.gateway.subscriptions(customer_id)
        if any(s.get("status") not in {"canceled", "incomplete_expired"} for s in current):
            raise DomainError(409, "subscription_exists", "This Stripe customer already has a subscription. Manage it in the test dashboard.")
        now = int(time.time())
        with self.db.read() as conn:
            existing = conn.execute("SELECT * FROM checkout_sessions WHERE tenant_id=?", (tenant_id,)).fetchone()
        remote_status = None
        if existing and existing["stripe_session_id"]:
            remote = self.gateway.retrieve_checkout(existing["stripe_session_id"])
            remote_status = remote.get("status")
            # A completed session can be replaced after its subscription is canceled.
            if remote_status == "complete" and not current:
                raise DomainError(409, "checkout_completed", "Checkout completed; wait for the verified webhook to synchronize your subscription.")
            if remote_status == "open":
                return {"checkout_url": remote["url"], "session_id": remote["id"], "mode": "test"}
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM checkout_sessions WHERE tenant_id=?", (tenant_id,)).fetchone()
            # A durable key also survives a crash between Stripe creation and local save.
            if not row:
                conn.execute("INSERT INTO checkout_sessions(tenant_id,idempotency_key,expires_at) VALUES (?,?,?)",
                             (tenant_id, f"checkout:{tenant_id}:1", now + 86400))
            elif (row["expires_at"] <= now or row["completed"] or remote_status in {"expired", "complete"}) and (not existing or row["generation"] == existing["generation"]):
                generation = row["generation"] + 1
                conn.execute("""UPDATE checkout_sessions SET generation=?,idempotency_key=?,stripe_session_id=NULL,
                             checkout_url=NULL,expires_at=?,completed=0 WHERE tenant_id=?""",
                             (generation, f"checkout:{tenant_id}:{generation}", now + 86400, tenant_id))
            row = conn.execute("SELECT * FROM checkout_sessions WHERE tenant_id=?", (tenant_id,)).fetchone()
            key = row["idempotency_key"]
        session = self.gateway.create_checkout(customer_id, tenant_id, key)
        with self.db.transaction() as conn:
            conn.execute("""UPDATE checkout_sessions SET stripe_session_id=?,checkout_url=?,expires_at=?
                         WHERE tenant_id=? AND idempotency_key=?""",
                         (session["id"], session["url"], session["expires_at"], tenant_id, key))
        return {"checkout_url": session["url"], "session_id": session["id"], "mode": "test"}

    def receive(self, event):
        obj = event["data"]["object"]
        customer = obj.get("customer")
        if isinstance(customer, dict):
            customer = customer.get("id")
        if not isinstance(customer, str):
            customer = None
        with self.db.transaction() as conn:
            old = conn.execute("SELECT status FROM webhook_events WHERE event_id=?", (event["id"],)).fetchone()
            if old:
                return {"received": True, "duplicate": True, "status": old["status"]}
            tenant = conn.execute("SELECT id FROM tenants WHERE stripe_customer_id=?", (customer,)).fetchone()
            supported = event["type"] in EVENT_TYPES and tenant is not None
            tenant_id = tenant["id"] if tenant else None
            status = "queued" if supported else "ignored"
            conn.execute("INSERT INTO webhook_events VALUES (?,?,?,?,?)",
                         (event["id"], event["type"], tenant_id, status, utc_now().isoformat()))
            if supported:
                conn.execute("INSERT INTO jobs(id,event_id,tenant_id,status,available_at) VALUES (?,?,?,'queued',?)",
                             (str(uuid4()), event["id"], tenant_id, int(time.time())))
                if event["type"] == "checkout.session.completed":
                    conn.execute("UPDATE checkout_sessions SET completed=1 WHERE tenant_id=? AND stripe_session_id=?", (tenant_id, obj.get("id")))
        return {"received": True, "duplicate": False, "status": status}
