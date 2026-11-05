"""One-time, read-only import of the previous SQLite ledger into empty PostgreSQL."""
from pathlib import Path
import sqlite3
from psycopg import sql

TABLES = ("tenants", "subscriptions", "usage_events", "webhook_events", "jobs", "alerts", "checkout_sessions")


def import_sqlite(source_path, db):
    source_path = Path(source_path).resolve(strict=True)
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute("BEGIN")  # One consistent source snapshot.
        with db.transaction() as target:
            target.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in ("plans", *TABLES))))
            for table in TABLES:
                if target.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
                    raise ValueError("Import requires an empty PostgreSQL application database; no rows were changed.")
            for row in source.execute("SELECT * FROM plans"):
                target.execute("""INSERT INTO plans VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE
                    SET api_call_limit=EXCLUDED.api_call_limit, ai_token_limit=EXCLUDED.ai_token_limit,
                    monthly_price_micro_usd=EXCLUDED.monthly_price_micro_usd""", tuple(row))
            counts = {}
            for table in TABLES:
                rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
                for row in rows:
                    columns = list(row.keys())
                    target.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns)), tuple(row))
                actual = target.execute(sql.SQL("SELECT COUNT(*) AS count FROM {}").format(sql.Identifier(table))).fetchone()["count"]
                if actual != len(rows):
                    raise ValueError("Imported row-count mismatch; transaction rolled back.")
                counts[table] = actual
            old_cost = source.execute("SELECT COALESCE(SUM(cost_micro_usd),0) FROM usage_events").fetchone()[0]
            new_cost = target.execute("SELECT COALESCE(SUM(cost_micro_usd),0)::bigint AS cost FROM usage_events").fetchone()["cost"]
            if old_cost != new_cost:
                raise ValueError("Imported cost mismatch; transaction rolled back.")
            return {"rows": counts, "cost_micro_usd": new_cost, "source_modified": False}
    finally:
        source.close()
