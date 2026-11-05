from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import psycopg
import pytest
from app.errors import DomainError
from app.metering import MeterService
from app.models import GenerateRequest
from app.pricing import price_usage
from tests.conftest import auth

SAMPLE = {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 500, "reasoning_tokens": 200}


def process_record(connection):
    from app.postgres import Database
    return MeterService(Database(*connection)).record("alpha", GenerateRequest(), "multiprocess")[0]["event_id"]


def test_separate_processes_share_idempotency_guarantee(system):
    from concurrent.futures import ProcessPoolExecutor
    app, _, _, _, _ = system
    with ProcessPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(process_record, [(app.state.db.dsn, app.state.db.schema)] * 8))
    assert len(set(ids)) == 1
    assert app.state.meter.summary("alpha")["used"]["event_count"] == 1


def test_exact_integer_pricing_and_monthly_rollup(system):
    app, client, _, alpha, _ = system
    result = client.post("/generate", headers=auth(alpha, "priced"), json=SAMPLE)
    assert result.status_code == 200
    assert result.json()["cost"]["total_micro_usd"] == 24000
    assert result.json()["cost"]["total_usd"] == "0.024000"
    assert result.json()["cost"]["breakdown_micro_usd"] == {"api_calls": 1000, "uncached_input": 6000, "cached_input": 2000, "output_including_reasoning": 15000}
    summary = client.get("/usage", headers=auth(alpha)).json()
    assert summary["used"]["ai_tokens"] == 1500
    assert summary["used"]["cost_micro_usd"] == 24000


def test_retry_returns_identical_response_and_one_event(system):
    _, client, _, alpha, _ = system
    first = client.post("/generate", headers=auth(alpha, "retry"), json=SAMPLE)
    second = client.post("/generate", headers=auth(alpha, "retry"), json=SAMPLE)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert second.headers["Idempotency-Replayed"] == "true"
    assert client.get("/usage", headers=auth(alpha)).json()["used"]["event_count"] == 1


def test_changed_payload_conflicts_without_charge(system):
    _, client, _, alpha, _ = system
    assert client.post("/generate", headers=auth(alpha, "same"), json={}).status_code == 200
    assert client.post("/generate", headers=auth(alpha, "same"), json=SAMPLE).status_code == 409
    assert client.get("/usage", headers=auth(alpha)).json()["used"]["event_count"] == 1


def test_32_concurrent_identical_requests_create_one_event(system):
    app, _, _, _, _ = system
    def call(_):
        return app.state.meter.record("alpha", GenerateRequest(**SAMPLE), "concurrent")[0]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(call, range(32)))
    assert len({r["event_id"] for r in results}) == 1
    assert app.state.meter.summary("alpha")["used"]["event_count"] == 1


def test_concurrent_distinct_requests_cannot_overshoot_quota(system):
    app, _, _, _, _ = system
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE plans SET api_call_limit=10 WHERE id='free'")
    def call(i):
        try:
            app.state.meter.record("alpha", GenerateRequest(), str(i))
            return 200
        except DomainError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(call, range(32)))
    assert statuses.count(200) == 10
    assert statuses.count(429) == 22
    assert app.state.meter.summary("alpha")["used"]["api_calls"] == 10


def test_free_api_quota_exactly_1000_then_429(system):
    app, client, _, alpha, _ = system
    for i in range(1000):
        app.state.meter.record("alpha", GenerateRequest(), str(i))
    response = client.post("/generate", headers=auth(alpha, "1001"), json={})
    assert response.status_code == 429
    assert response.json()["error"]["details"]["used"] == 1000
    assert int(response.headers["Retry-After"]) > 0
    assert app.state.meter.summary("alpha")["used"]["event_count"] == 1000
    # Successful retries still work after quota exhaustion.
    assert client.post("/generate", headers=auth(alpha, "999"), json={}).status_code == 200


def test_token_boundary_and_atomic_rejection(system):
    app, client, _, alpha, _ = system
    assert client.post("/generate", headers=auth(alpha, "at-limit"), json={"input_tokens": 100000}).status_code == 200
    response = client.post("/generate", headers=auth(alpha, "over"), json={"output_tokens": 1})
    assert response.status_code == 429
    assert response.json()["error"]["details"]["metric"] == "ai_tokens"
    assert app.state.meter.summary("alpha")["used"]["event_count"] == 1


def test_payment_required_402_and_budget_guard(system):
    app, client, _, alpha, _ = system
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE subscriptions SET status='past_due' WHERE tenant_id='alpha'")
    assert client.post("/generate", headers=auth(alpha, "payment"), json={}).status_code == 402
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE subscriptions SET status='free' WHERE tenant_id='alpha'")
        conn.execute("UPDATE tenants SET monthly_budget_micro_usd=1000 WHERE id='alpha'")
    assert client.post("/generate", headers=auth(alpha, "budget-boundary"), json={}).status_code == 200
    response = client.post("/generate", headers=auth(alpha, "over-budget"), json={})
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "budget_exceeded"


def test_tenant_isolation_and_key_scope(system):
    _, client, _, alpha, beta = system
    for tenant in (alpha, beta):
        assert client.post("/generate", headers=auth(tenant, "shared-key"), json={}).status_code == 200
    a = client.get("/usage/events", headers=auth(alpha)).json()["items"]
    b = client.get("/usage/events", headers=auth(beta)).json()["items"]
    assert len(a) == len(b) == 1
    assert a[0]["id"] != b[0]["id"]
    assert client.get("/usage", headers={**auth(beta), "X-Tenant-ID": "alpha"}).json()["tenant_id"] == "beta"


@pytest.mark.parametrize("payload", [
    {"input_tokens": -1}, {"input_tokens": 1.5}, {"input_tokens": True}, {"input_tokens": "3"},
    {"cached_input_tokens": 1}, {"reasoning_tokens": 1}, {"input_tokens": 10000001},
    {"tenant_id": "beta"}, {"input_tokens": None},
])
def test_invalid_input_is_422_and_never_metered(system, payload):
    app, client, _, alpha, _ = system
    assert client.post("/generate", headers=auth(alpha, "bad"), json=payload).status_code == 422
    assert app.state.meter.summary("alpha")["used"]["event_count"] == 0


def test_authentication_and_missing_key(system):
    _, client, _, alpha, _ = system
    assert client.get("/usage").status_code == 401
    assert client.get("/usage", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.post("/generate", headers=auth(alpha), json={}).status_code == 422
    assert client.get("/usage?period=2026-13", headers=auth(alpha)).status_code == 422


def test_utc_month_rollover_and_cross_month_idempotency(system):
    app, _, _, _, _ = system
    now = [datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)]
    meter = MeterService(app.state.db, clock=lambda: now[0])
    first, _ = meter.record("alpha", GenerateRequest(input_tokens=100000), "december")
    now[0] = datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert meter.summary("alpha")["used"]["event_count"] == 0
    replay, was_replayed = meter.record("alpha", GenerateRequest(input_tokens=100000), "december")
    assert replay == first and was_replayed
    meter.record("alpha", GenerateRequest(), "january")
    assert meter.summary("alpha", "2026-12")["used"]["ai_tokens"] == 100000
    assert meter.summary("alpha")["used"]["api_calls"] == 1


def test_persistence_survives_service_recreation(system):
    app, _, _, _, _ = system
    from app.postgres import Database
    app.state.meter.record("alpha", GenerateRequest(), "persisted")
    reopened = Database(app.state.db.dsn, app.state.db.schema)
    reopened.migrate()
    assert MeterService(reopened).summary("alpha")["used"]["event_count"] == 1


def test_rollback_leaves_no_charge_on_insert_failure(system):
    app, _, _, _, _ = system
    with app.state.db.transaction() as conn:
        conn.execute("ALTER TABLE usage_events ADD CONSTRAINT reject_test_insert CHECK (api_calls = 0)")
    with pytest.raises(psycopg.IntegrityError):
        app.state.meter.record("alpha", GenerateRequest(), "failed")
    assert app.state.meter.summary("alpha")["used"]["event_count"] == 0


def test_all_cached_and_all_reasoning_not_double_counted():
    result = price_usage(GenerateRequest(input_tokens=1000, cached_input_tokens=1000, output_tokens=1000, reasoning_tokens=1000))
    assert result["total_micro_usd"] == 36000
