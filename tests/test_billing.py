import time
import pytest
from app.billing import verify_event
from app.errors import DomainError
from app.worker import Worker
from config import Settings
from tests.conftest import auth, event, signed, subscription


def deliver(client, data=None):
    raw, headers = signed(data or event())
    return client.post("/webhooks/stripe", content=raw, headers=headers)


def test_signed_checkout_job_upgrades_free_to_pro(system):
    app, client, fake, alpha, _ = system
    fake.subs = [subscription()]
    assert deliver(client).json()["status"] == "queued"
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "free"
    assert app.state.worker.run_once()
    usage = client.get("/usage", headers=auth(alpha)).json()
    assert usage["plan"] == "pro"
    assert usage["limits"] == {"api_calls": 10000, "ai_tokens": 1000000}


def test_duplicate_event_is_processed_once(system):
    app, client, fake, _, _ = system
    fake.subs = [subscription()]
    assert deliver(client).json()["duplicate"] is False
    assert deliver(client).json()["duplicate"] is True
    assert app.state.worker.run_once()
    assert not app.state.worker.run_once()
    assert deliver(client).json()["status"] == "processed"
    with app.state.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"] == 1
        assert conn.execute("SELECT attempts FROM jobs").fetchone()["attempts"] == 1


def test_forged_webhook_400_no_side_effects(system):
    app, client, _, _, _ = system
    raw, headers = signed(event(), secret="wrong")
    assert client.post("/webhooks/stripe", content=raw, headers=headers).status_code == 400
    with app.state.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM webhook_events").fetchone()["count"] == 0
        assert conn.execute("SELECT plan_id FROM subscriptions WHERE tenant_id='alpha'").fetchone()["plan_id"] == "free"


@pytest.mark.parametrize("kind", ["stale", "future", "live", "malformed"])
def test_webhook_rejects_invalid_envelopes(system, kind):
    _, client, _, _, _ = system
    data = event()
    timestamp = int(time.time())
    if kind == "stale":
        timestamp -= 600
    elif kind == "future":
        timestamp += 600
    elif kind == "live":
        data["livemode"] = True
    else:
        data["data"] = None
    raw, headers = signed(data, timestamp=timestamp)
    assert client.post("/webhooks/stripe", content=raw, headers=headers).status_code == 400


def test_updated_and_deleted_subscriptions_sync_current_state(system):
    app, client, fake, alpha, _ = system
    for index, status in enumerate(["active", "past_due", "canceled"]):
        fake.subs = [subscription(status=status)]
        deliver(client, event(f"evt_{index}", "customer.subscription.deleted" if status == "canceled" else "customer.subscription.updated"))
        app.state.worker.run_once()
        result = client.get("/usage", headers=auth(alpha)).json()
        assert result["subscription_status"] == status
        assert result["plan"] == ("pro" if status == "active" else "free")


def test_old_event_cannot_restore_canceled_subscription(system):
    app, client, fake, alpha, _ = system
    fake.subs = [subscription(status="canceled")]
    deliver(client, event("evt_delayed_active", "customer.subscription.updated"))
    app.state.worker.run_once()
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "free"


def test_old_cancellation_does_not_remove_new_active_subscription(system):
    app, client, fake, alpha, _ = system
    fake.subs = [subscription(status="canceled"), subscription(sub_id="sub_new", created=200)]
    deliver(client, event("evt_old_delete", "customer.subscription.deleted"))
    app.state.worker.run_once()
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "pro"


def test_other_price_does_not_grant_pro(system):
    app, client, fake, alpha, _ = system
    fake.subs = [subscription(price="price_other")]
    deliver(client)
    app.state.worker.run_once()
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "free"


def test_unknown_customer_and_event_are_ignored(system):
    app, client, _, _, _ = system
    assert deliver(client, event("evt_unknown", customer="cus_unknown")).json()["status"] == "ignored"
    assert deliver(client, event("evt_unhandled", "invoice.created")).json()["status"] == "ignored"
    assert not app.state.worker.run_once()


def test_retry_exhaustion_alert_and_tenant_isolation(system):
    app, client, fake, alpha, beta = system
    fake.fail = True
    deliver(client)
    for _ in range(5):
        assert app.state.worker.run_once()
        with app.state.db.transaction() as conn:
            conn.execute("UPDATE jobs SET available_at=0")
    job = client.get("/billing/jobs", headers=auth(alpha)).json()["items"][0]
    assert job["status"] == "failed" and job["attempts"] == 5
    assert job["last_error"] == "RuntimeError"
    assert len(client.get("/billing/alerts", headers=auth(alpha)).json()["items"]) == 1
    assert client.get("/billing/jobs", headers=auth(beta)).json()["items"] == []
    assert client.get("/billing/alerts", headers=auth(beta)).json()["items"] == []


def test_transient_failure_then_recovery(system):
    app, client, fake, alpha, _ = system
    fake.fail = True
    deliver(client)
    app.state.worker.run_once()
    assert not app.state.worker.run_once()  # exponential backoff
    fake.fail = False
    fake.subs = [subscription()]
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE jobs SET available_at=0")
    assert app.state.worker.run_once()
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "pro"


def test_expired_lease_recovered_and_tenant_jobs_serialized(system):
    app, client, fake, _, _ = system
    deliver(client, event("evt_one"))
    deliver(client, event("evt_two"))
    job = app.state.worker.claim()
    assert job and app.state.worker.claim() is None
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until=0 WHERE id=%s", (job["id"],))
    recovered = app.state.worker.claim()
    assert recovered and recovered["id"] != job["id"]
    with app.state.db.read() as conn:
        assert conn.execute("SELECT last_error FROM jobs WHERE id=%s", (job["id"],)).fetchone()["last_error"] == "lease_expired"


def test_expired_worker_cannot_commit_stale_snapshot(system):
    app, client, fake, alpha, _ = system
    deliver(client)
    now = [1000]
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE jobs SET available_at=0")
    worker = Worker(app.state.db, fake, "price_pro", clock=lambda: now[0])
    def slow_subscriptions(customer):
        now[0] += 121
        return [subscription()]
    fake.subscriptions = slow_subscriptions
    worker.run_once()
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "free"


def test_checkout_reuses_open_session_and_does_not_grant_plan(system):
    app, client, fake, alpha, _ = system
    first = client.post("/billing/checkout", headers=auth(alpha))
    second = client.post("/billing/checkout", headers=auth(alpha))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(fake.checkout_keys) == 1
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "free"
    client.get("/billing/return?result=success")
    assert client.get("/usage", headers=auth(alpha)).json()["plan"] == "free"


def test_checkout_rejects_existing_subscription_even_before_webhook(system):
    _, client, fake, alpha, _ = system
    fake.subs = [subscription()]
    assert client.post("/billing/checkout", headers=auth(alpha)).status_code == 409


def test_live_key_configuration_rejected():
    with pytest.raises(ValueError, match="Only Stripe"):
        Settings(stripe_secret_key="sk_live_forbidden")


def test_signature_rotation_accepts_any_valid_v1():
    raw, headers = signed(event())
    signature = headers["Stripe-Signature"] + ",v1=invalid"
    assert verify_event(raw, signature, "whsec_fixture")["id"] == "evt_checkout"


def test_expired_checkout_uses_new_durable_key(system):
    _, client, fake, alpha, _ = system
    client.post("/billing/checkout", headers=auth(alpha))
    fake.session_status = "expired"
    assert client.post("/billing/checkout", headers=auth(alpha)).status_code == 200
    assert fake.checkout_keys == ["checkout:alpha:1", "checkout:alpha:2"]


def test_canceled_subscription_can_resubscribe(system):
    _, client, fake, alpha, _ = system
    client.post("/billing/checkout", headers=auth(alpha))
    fake.session_status = "complete"
    fake.subs = [subscription(status="canceled")]
    assert client.post("/billing/checkout", headers=auth(alpha)).status_code == 200
    assert fake.checkout_keys[-1] == "checkout:alpha:2"
