"""PostgreSQL connections and transaction-scoped locks."""
from contextlib import contextmanager
import re
import psycopg
from psycopg.rows import dict_row
from config import ROOT


class Database:
    def __init__(self, dsn: str, schema: str = "public"):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("Invalid database schema")
        self.dsn, self.schema = dsn, schema

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5,
                              options=f"-c search_path={self.schema} -c lock_timeout=15000 -c statement_timeout=30000")

    @contextmanager
    def read(self):
        with self.connect() as conn:
            # Consistent plan + usage view across multiple reads.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield conn

    @contextmanager
    def transaction(self):
        # Read committed is deliberate: after waiting for a tenant lock, subsequent
        # statements must see the previous writer's committed usage event.
        with self.connect() as conn:
            yield conn

    def migrate(self):
        with self.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(10, hashtext(%s))", (self.schema,))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
            for migration in sorted((ROOT / "migrations" / "postgresql").glob("*.sql")):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (migration.name,)).fetchone():
                    continue
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
                conn.execute("INSERT INTO schema_migrations VALUES (%s)", (migration.name,))


def lock_tenant(conn, tenant_id):
    # NO KEY UPDATE serializes this tenant's billing operations while permitting
    # foreign-key checks from independent queue/ledger operations.
    conn.execute("SELECT id FROM tenants WHERE id=%s FOR NO KEY UPDATE", (tenant_id,)).fetchone()


def tenant_plan(conn, tenant_id):
    return conn.execute(
        """SELECT t.*, s.plan_id, s.status, s.stripe_subscription_id,
                  p.api_call_limit, p.ai_token_limit, p.monthly_price_micro_usd
           FROM tenants t JOIN subscriptions s ON s.tenant_id=t.id
           JOIN plans p ON p.id=s.plan_id WHERE t.id=%s""", (tenant_id,)
    ).fetchone()


def rollup(conn, tenant_id, period):
    return dict(conn.execute(
        """SELECT COUNT(*) AS event_count, COALESCE(SUM(api_calls),0)::bigint AS api_calls,
           COALESCE(SUM(input_tokens),0)::bigint AS input_tokens,
           COALESCE(SUM(cached_input_tokens),0)::bigint AS cached_input_tokens,
           COALESCE(SUM(output_tokens),0)::bigint AS output_tokens,
           COALESCE(SUM(reasoning_tokens),0)::bigint AS reasoning_tokens,
           COALESCE(SUM(total_tokens),0)::bigint AS ai_tokens,
           COALESCE(SUM(cost_micro_usd),0)::bigint AS cost_micro_usd
           FROM usage_events WHERE tenant_id=%s AND period=%s""", (tenant_id, period)
    ).fetchone())
