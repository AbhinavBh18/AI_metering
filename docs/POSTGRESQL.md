# PostgreSQL setup and migration

The application uses PostgreSQL 18 through Psycopg 3. SQLite is no longer an application storage option. Its old SQL schema is retained only as a legacy import fixture.

## Docker database with native Python API

After installing the Python requirements, copy `.env.example` to `.env` and run:

```text
docker compose up -d db
python -m app migrate
python -m app seed
python -m app run
```

The example configuration uses `metering` and `metering_test` databases on localhost:5432. Credentials in the example are exclusively for local development. The test database is created only on a fresh Docker volume; for an existing volume, create it manually with `docker compose exec db createdb -U metering metering_test` if it does not exist. Docker initialization scripts do not rerun against existing data.

For the complete container stack, use `docker compose up --build`, then `docker compose exec api python -m app seed`. The API connects to the `db` service through the internal Docker network. Compose's database credentials come from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`; the native API uses `DATABASE_URL`.

## Installed PostgreSQL without Docker

Use an existing PostgreSQL database with a user that owns the application schema and can apply migrations. Configure `DATABASE_URL` and a separate `TEST_DATABASE_URL` in `.env`. Never commit real credentials. Tests create and drop random schemas; they do not clear the application's public schema.

On this workstation, PostgreSQL 18 binaries were already installed. The helper creates a separate cluster inside git-ignored `data/postgresql`, binds only to `127.0.0.1:55432`, and generates a random SCRAM password into `.env`. It does not alter the system PostgreSQL service:

```powershell
.\.venv\Scripts\python.exe scripts/local_postgres.py
```

For a fresh checkout, run the helper before manually copying `.env.example`; it creates `.env` itself. It refuses to overwrite `.env` if it already points at a different database. Use `--pg-bin` for another PostgreSQL binary directory and `--port` for another available port.

Restart this project's cluster with the same command. Stop it explicitly with:

```powershell
.\.venv\Scripts\python.exe scripts/local_postgres.py --stop
```

No Windows service is installed. This helper's database user owns the development cluster; use a dedicated least-privilege database role for a shared deployment.

## Preserve the previous SQLite data

Stop the old API first. Configure PostgreSQL, then import **before running seed**:

```powershell
.\.venv\Scripts\python.exe scripts/import_sqlite.py data/metering.db
.\.venv\Scripts\python.exe -m app run
```

The importer:

- Opens the old SQLite file read-only using one consistent source snapshot.
- Applies the PostgreSQL schema, then imports all application tables in one transaction.
- Preserves tenant key hashes, usage IDs, idempotency keys, stored responses, costs, subscriptions, and queued jobs.
- Verifies row counts and total usage cost before committing.
- Refuses a target containing application data. It never merges, truncates, or silently overwrites existing target rows.

Keep `data/demo-credentials.json` alongside the migration: it holds the existing local demo keys, whose hashes are preserved in PostgreSQL. Retain the old database as a backup. If rolling back later, point the previous application version at that file; it will not include usage added after the PostgreSQL cutover.

## Transaction design

- **Metering:** read committed + tenant `FOR NO KEY UPDATE` lock + quota check + insert. The lock is acquired before reading idempotency state or totals. A unique tenant/key constraint is the final duplicate guard.
- **Summaries:** repeatable-read, read-only transactions give a consistent plan and ledger snapshot.
- **Webhook deduplication:** event-scoped transaction advisory lock; event record and queued job commit together.
- **Job claiming:** short queue-claim advisory lock, row locks with `SKIP LOCKED`, and a unique partial index on running jobs per tenant. Remote Stripe calls execute after the claim commits.
- **Job completion:** lock the tenant, lock/check the job lease token, then update entitlements and completion status in one transaction. Reclaiming a lease cannot race an unprotected entitlement write.
- **Migrations:** schema-scoped advisory lock and transactional DDL; repeat/concurrent invocations apply each version once.

Migrations live in `migrations/postgresql/`. Application queries use Psycopg parameter binding; user input is never interpolated into SQL. Connection/statement/lock timeouts bound failures. HTTP database errors are sanitized.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/smoke.py
```

The suite requires a reachable PostgreSQL server; it does not silently fall back to SQLite. Set `TEST_DATABASE_URL` before running. GitHub Actions defines its own PostgreSQL 18 service. Real Stripe checkout is still a separate external verification step.
