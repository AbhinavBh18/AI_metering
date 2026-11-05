"""Persistence boundary: short transactions and database-enforced invariants."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from config import ROOT


class Database:
    def __init__(self, path: str):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    @contextmanager
    def read(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self):
        Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        finally:
            conn.close()
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
            for migration in sorted((ROOT / "migrations").glob("*.sql")):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.name,)).fetchone():
                    continue
                # executescript commits implicitly; execute complete statements instead.
                statement = ""
                for line in migration.read_text(encoding="utf-8").splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        conn.execute(statement)
                        statement = ""
                conn.execute("INSERT INTO schema_migrations VALUES (?)", (migration.name,))


def tenant_plan(conn, tenant_id):
    return conn.execute(
        """SELECT t.*, s.plan_id, s.status, s.stripe_subscription_id,
                  p.api_call_limit, p.ai_token_limit, p.monthly_price_micro_usd
           FROM tenants t JOIN subscriptions s ON s.tenant_id=t.id
           JOIN plans p ON p.id=s.plan_id WHERE t.id=?""", (tenant_id,)
    ).fetchone()


def rollup(conn, tenant_id, period):
    return dict(conn.execute(
        """SELECT COUNT(*) AS event_count, COALESCE(SUM(api_calls),0) AS api_calls,
           COALESCE(SUM(input_tokens),0) AS input_tokens,
           COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,
           COALESCE(SUM(output_tokens),0) AS output_tokens,
           COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
           COALESCE(SUM(total_tokens),0) AS ai_tokens,
           COALESCE(SUM(cost_micro_usd),0) AS cost_micro_usd
           FROM usage_events WHERE tenant_id=? AND period=?""", (tenant_id, period)
    ).fetchone())
