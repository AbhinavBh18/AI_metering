# Build log

## 2026-11-05 - Starting point and assistance

The workspace initially contained `config.py`, `requirements.txt`, and `.env.example`. The supplied capstone PDF was read in full. The intern requested an implementation suitable for the FlyRank Backend AI Engineer internship capstone and resume wording; AI-assisted building is permitted by the brief.

An AI coding assistant generated most of the implementation, SQL schema, tests, documentation, setup scripts, and resume drafts in this session. The intern has not yet independently reviewed every line. This log does not claim independent authorship, an earlier design commit, production usage, or completed portal submission.

## Design and implementation

- Chose FastAPI + SQLite because the brief explicitly allows SQLite and the entire core can run without Docker, credentials, or paid services.
- Separated HTTP, domain logic, persistence, Stripe adapter, and background work. Added a versioned migration with uniqueness constraints and tenant/period indexes.
- Implemented atomic metering, exact boundary semantics, payload-aware idempotency, random tenant API keys, and integer cost accounting.
- Added Stripe test-mode REST integration, signature verification, durable event ingestion, and a worker that reads current Stripe state. Implemented retries, leases, failure alerts, and a local requeue command.
- Added a reproducible local run path, optional Docker files, CI configuration, a live HTTP demo, evaluator manifest, evidence matrix, and explanation-focused resume notes.

## Corrections and problems encountered

1. **Existing unit naming was wrong.** The starter called 1/1,000,000 USD a “micro-cent”; it is a microdollar. Renamed all price/storage fields to `micro_usd` and documented the conversion.
2. **Token counts cannot be independently added.** Encoded cached-input and reasoning subset rules at the validation boundary, then proved exact totals with tests. Reasoning is charged once within output.
3. **Initial file-edit patch was rejected.** The editing tool rejected delete/add operations targeting the same existing file; the patch was rewritten as updates. No source changes were lost.
4. **Virtual environment bootstrapping failed in the sandbox.** The global pip controller installed pinned dependencies into the project virtual environment after network access was approved. The normal README bootstrap remains standard Python venv + pip.
5. **First test run used a missing temporary parent directory.** Created it, then reran. These setup errors were not represented as passing evidence.
6. **Windows sandbox blocked asyncio socket setup.** A diagnostic traceback showed a hang in Python's local `socketpair` creation before application tests started. Tests were rerun with approved local socket access; the stuck diagnostic processes were stopped.
7. **Test-client dependency warning.** The installed Starlette version requests `httpx2` for its test client. Added and pinned that dev dependency; the final suite has no warnings.
8. **Checkout recovery review found gaps.** An early implementation could reuse an expired checkout key and block resubscription after cancellation. Updated session-generation logic and added both regression cases.

## Verification actually performed

- Native Python migrations and demo seeding succeeded.
- The final automated suite passed: **53 tests in 25.00 seconds**, with no warnings. It includes threaded and separate-process contention, real Free-plan boundaries, rollover, error paths, tenant isolation, pricing, signed-event replay, worker leases, retry exhaustion, and checkout REST fixtures.
- Python source compilation succeeded.
- See `docs/evidence/` and `EVIDENCE.md` for the final HTTP smoke-test record and requirement mapping.

## Outstanding external gates

- The intern stated a Stripe test account is not ready and requested instructions. No real Stripe-hosted checkout was performed. The gateway and worker are tested with explicit fakes; those are not live integration proof.
- Docker's engine was stopped, so the Docker configuration has not been run.
- No public GitHub repository, remote CI execution, or portal submission was performed. Commit history must reflect real work; do not fabricate earlier phase commits.

## Intern review before submission

Read `docs/DESIGN.md`, run the demo and tests, trace one accepted request through all layers, and explain the five interview questions in `docs/RESUME.md`. Add your own dated notes about changes and explanations you verified. Keep this AI disclosure honest.

## 2026-11-05 - PostgreSQL migration requested by the intern

This section supersedes the SQLite implementation details and 53-test count above; those entries describe the original build, not the current backend.

- Replaced the application database with PostgreSQL 18 and Psycopg 3.3.8. Application storage is now exclusively PostgreSQL; SQLite remains only in the read-only legacy importer and its test fixture.
- Replaced database-wide write serialization with tenant row locks (`FOR NO KEY UPDATE`) in read-committed transactions. Plan synchronization takes the same tenant lock. Read-only usage summaries use repeatable-read snapshots.
- Added PostgreSQL transactional migrations, advisory locks for concurrent migration/event/queue operations, row-locked job completion, a partial unique index limiting each tenant to one running job, BIGINT money, and TIMESTAMPTZ timestamps.
- Provisioned a project-local PostgreSQL cluster using the existing installed binaries, on localhost:55432. Random SCRAM credentials are in git-ignored `.env`; no system PostgreSQL service was changed. Added Docker database service, database health dependency, and a PostgreSQL CI service.
- Converted the existing test suite to isolated PostgreSQL schemas and added six PostgreSQL regression checks. **59 tests passed in 163.75 seconds**, with no warnings. The suite exercises a real PostgreSQL server, including multiple Python processes.
- Stopped the original SQLite API and imported its **2 tenants, 2 subscriptions, and 1 usage event**, preserving the **24,000 micro-USD** ledger cost and existing API key hashes. The SQLite source remains a backup and was opened read-only. Target row counts and total cost were checked before commit.
- Restarted the API on PostgreSQL. The live HTTP smoke check returned `storage: postgresql`, served docs/OpenAPI, rejected unauthenticated usage, and confirmed two identical requests create one new event with an exact $0.024000 cost.
- Updated setup, design, evidence, and quantified resume wording to PostgreSQL and 59 tests. Earlier SQLite evidence was archived with explicit `sqlite-` filenames.
- Python source compilation and dependency consistency checks passed. `docker compose config --quiet` validates the revised Compose configuration; container runtime execution remains unverified because the Docker engine is stopped.

No real Stripe checkout, public repository publication, or production deployment occurred during this migration. The AI assistant performed this migration; the intern should review the transaction choices in `docs/POSTGRESQL.md` before presenting them.
