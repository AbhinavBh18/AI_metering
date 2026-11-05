from concurrent.futures import ThreadPoolExecutor
import threading
import sqlite3
import pytest
from app.billing import BillingService
from app.legacy_import import import_sqlite
from app.models import GenerateRequest
from app.postgres import lock_tenant
from config import ROOT
from tests.conftest import event


def test_other_tenant_can_meter_while_first_tenant_is_locked(system):
    app, _, _, _, _ = system
    completed = threading.Event()
    errors = []
    def write_beta():
        try:
            app.state.meter.record("beta", GenerateRequest(), "independent")
            completed.set()
        except Exception as exc:
            errors.append(exc)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with app.state.db.transaction() as conn:
            lock_tenant(conn, "alpha")
            future = pool.submit(write_beta)
            assert completed.wait(5), "Tenant beta should not wait for alpha's row lock"
        future.result()
    assert not errors


def test_parallel_webhook_deliveries_enqueue_exactly_once(system):
    app, _, fake, _, _ = system
    billing = BillingService(app.state.db, fake)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: billing.receive(event()), range(16)))
    assert sum(not r["duplicate"] for r in results) == 1
    with app.state.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1


def test_parallel_claims_never_run_two_jobs_for_one_tenant(system):
    app, _, fake, _, _ = system
    billing = BillingService(app.state.db, fake)
    for i in range(8):
        billing.receive(event(f"evt_parallel_{i}"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: app.state.worker.claim(), range(8)))
    assert sum(job is not None for job in claims) == 1


def test_parallel_migrations_are_safe(postgres_db):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: postgres_db.migrate(), range(4)))
    with postgres_db.read() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"] == 1


def test_bigint_money_and_integer_rollup(system):
    app, _, _, _, _ = system
    app.state.meter.record("alpha", GenerateRequest(), "large-integer")
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE usage_events SET cost_micro_usd=3000000000 WHERE tenant_id='alpha'")
        conn.execute("UPDATE tenants SET monthly_budget_micro_usd=5000000000 WHERE id='alpha'")
    result = app.state.meter.summary("alpha")
    assert result["used"]["cost_micro_usd"] == 3_000_000_000
    assert type(result["used"]["cost_micro_usd"]) is int
    assert result["usage_cost_usd"] == "3000.000000"


def test_legacy_import_preserves_data_and_refuses_nonempty_target(postgres_db, tmp_path):
    source_path = tmp_path / "legacy.db"
    with sqlite3.connect(source_path) as conn:
        conn.executescript((ROOT / "migrations" / "001_initial.sql").read_text())
        conn.execute("INSERT INTO tenants(id,name,api_key_hash,created_at) VALUES ('old','Old tenant','hash','2026-11-05T00:00:00+00:00')")
        conn.execute("INSERT INTO subscriptions(tenant_id) VALUES ('old')")
    original = source_path.read_bytes()
    postgres_db.migrate()
    result = import_sqlite(source_path, postgres_db)
    assert result["rows"]["tenants"] == 1
    assert result["rows"]["subscriptions"] == 1
    assert source_path.read_bytes() == original
    with pytest.raises(ValueError, match="empty PostgreSQL"):
        import_sqlite(source_path, postgres_db)
    with postgres_db.read() as conn:
        assert conn.execute("SELECT api_key_hash FROM tenants WHERE id='old'").fetchone()["api_key_hash"] == "hash"
