# Capstone evidence

Verified locally on **2026-11-05**, using Windows, Python 3.14.0, PostgreSQL 18, and Psycopg 3.3.8. Evidence below distinguishes actual local behavior from work that requires an external account. The earlier SQLite evidence is archived in `docs/evidence/sqlite-baseline.md`; it is not the current backend.

**Local result: 59 automated tests passed, with no warnings. The running HTTP API also passed the smoke/demo checks.** Real hosted Stripe Checkout, Docker runtime validation, public GitHub publication, and remote CI execution remain outstanding.

## Recorded commands and output

Final automated run:

```text
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/postgres-tests --maxfail=2
...........................................................                    [100%]
59 passed in 163.75s (0:02:43)
```

Saved in [docs/evidence/test-results.txt](docs/evidence/test-results.txt). Run `python scripts/verify.py` for a fresh verbose transcript that names each test. These are deterministic tests against a real PostgreSQL 18 server, using isolated temporary schemas; Stripe is replaced by an explicit fake or HTTP transport fixture.

PostgreSQL data cutover and native run:

```text
python scripts/import_sqlite.py data/metering.db
tenants: 2
subscriptions: 2
usage_events: 1
cost_micro_usd: 24000
source_modified: false

python -m app run
INFO app.worker Billing worker started
INFO: Application startup complete.
INFO: Uvicorn running on http://127.0.0.1:8000
```

The import preserved the existing tenant key hashes and stored usage response. The original random demo keys remain valid in the git-ignored data directory. No credential values are included in evidence. The subsequent HTTP smoke test adds one new event, independently of the imported event.

Live HTTP check (`python scripts/smoke.py`), saved in [docs/evidence/http-smoke.json](docs/evidence/http-smoke.json):

```json
{
  "health": {"status": "ok", "storage": "postgresql", "payments": "test_mode_only"},
  "docs_status": 200,
  "openapi_status": 200,
  "unauthenticated_usage_status": 401,
  "demo": {
    "first_status": 200,
    "retry_status": 200,
    "identical_response": true,
    "new_usage_events": 1,
    "total_tokens": 1500,
    "cost_micro_usd": 24000,
    "cost_usd": "0.024000"
  }
}
```

`python -m compileall -q app config.py scripts` completed with exit code 0 after the migration. The migrated application and live smoke script also executed successfully. The PostgreSQL driver dependency check and Compose configuration check are recorded in the migration build log.

## Section 6: requirement-to-proof map

Every listed test below was included in the 59-pass run. Test names can be run individually with `python -m pytest tests/FILE.py::TEST_NAME -v`.

| Requirement | Status | Pasted proof / assertion verified |
|---|---|---|
| One usage event per action under retries | PASS | Live HTTP: `identical_response: true`, `new_usage_events: 1`. `test_retry_returns_identical_response_and_one_event`: two HTTP 200 responses have identical JSON and ledger count is 1. |
| Proof against double-counting | PASS for this design's tested boundary | `test_32_concurrent_identical_requests_create_one_event`: 32 attempts yield one event ID. `test_separate_processes_share_idempotency_guarantee`: eight calls across four processes yield one event. Database uniqueness and a transaction enforce the invariant beyond the test schedule. |
| Plan-aware quota rejection | PASS | `test_free_api_quota_exactly_1000_then_429`: 1,000 accepted calls; call 1,001 returns 429 and event count stays 1,000. `test_concurrent_distinct_requests_cannot_overshoot_quota`: 10 accepted and 22 rejected at a 10-call test limit. |
| Correct 429 / 402 with reason | PASS | `test_token_boundary_and_atomic_rejection`: 100,000 tokens accepted, the next token returns 429 with `metric: ai_tokens`. `test_payment_required_402_and_budget_guard`: delinquency and budget overrun return 402; exact budget is accepted. `Retry-After` is positive. |
| Monthly tenant cost rollup | PASS | Live demo: 24,000 micro-USD. `test_exact_integer_pricing_and_monthly_rollup` asserts the same ledger sum; `test_utc_month_rollover_and_cross_month_idempotency` proves new-month usage starts at zero without deleting historical records. |
| Correct cached-input and reasoning pricing | PASS | `test_all_cached_and_all_reasoning_not_double_counted`: 1,000 fully cached input + 1,000 output entirely reasoning costs 36,000 micro-USD including the call. Sample mixed categories total 1,500 tokens, not 2,100. |
| Pinned pricing and exact totals | PASS | `config.py` uses 1,000 / 10 / 5 / 30 micro-USD rates. Sample arithmetic: `1000 + (1000-400)*10 + 400*5 + 500*30 = 24000`. Saved event includes `demo-2026-11-v1`. |
| Stripe hosted checkout end to end | **PENDING external verification** | REST adapter and orchestration tests pass, including `test_checkout_rest_contract_and_test_price_validation`. **No real Stripe session was completed.** Follow `docs/STRIPE_SETUP.md`; paste real sanitized before/after usage and worker output below. |
| Signed, deduplicated webhooks update subscription | PASS locally; real delivery pending | `test_forged_webhook_400_no_side_effects`: bad signature gives 400 and zero stored events. `test_duplicate_event_is_processed_once`: duplicate is true, one job, one attempt. `test_signed_checkout_job_upgrades_free_to_pro`: signed fixture queues work; worker changes limits to 10,000 / 1,000,000. |
| Required data model and tenant isolation | PASS | `migrations/postgresql/001_initial.sql` includes tenants, plans, subscriptions, and usage events. `test_tenant_isolation_and_key_scope`: same key text works independently for two tenants; event IDs and read results are isolated. `test_persistence_survives_service_recreation` preserves the ledger after reopening the database. |
| README, diagram, setup, and required files | PRESENT | `README.md` includes a text architecture diagram and exact native/optional Docker commands. `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, and `.env.example` exist. Public repository URL is still pending. |

## Shared requirements and additional checks

| Requirement | Proof |
|---|---|
| Layered architecture | HTTP in `app/main.py`; logic in metering/billing/pricing/worker; database operations in `app/postgres.py`; external network boundary in `app/stripe_gateway.py`. Design rationale in `docs/DESIGN.md`. |
| Input validation | Nine parametrized `test_invalid_input_is_422_and_never_metered` cases pass; missing auth is 401 and missing idempotency key is 422. Raw input is not echoed in validation errors. |
| Background job, retries, failure alert | `test_transient_failure_then_recovery` verifies backoff then recovery. `test_retry_exhaustion_alert_and_tenant_isolation` verifies five attempts, failed state, a durable tenant-visible alert, and sanitized `RuntimeError` storage. A sanitized error log is emitted. No email/Slack delivery is claimed. |
| Persistence, migrations, indexes | Migration is applied transactionally; tenant-period and job polling indexes exist. PostgreSQL ledger rows survive service recreation. No in-memory ledger is used. |
| Idempotency | Usage key uniqueness, webhook event ID uniqueness, and external checkout idempotency keys. `test_changed_payload_conflicts_without_charge` proves 409 without another charge. |
| Secret hygiene | Blank safe Stripe settings in `.env.example`; `.env` and `data/` ignored. `test_live_key_configuration_rejected` and `test_live_response_is_rejected` reject live mode. API keys are random and hashed in the database; local seed credentials remain git-ignored. No Stripe credential was supplied during this build. |
| AI cost attribution and budget | Each event has tenant, usage counts, BIGINT integer cost, and pricing version. Budget boundary test passes. Counts are simulated, not inferred from a model call. |
| Stale worker fencing and recovery | `test_expired_worker_cannot_commit_stale_snapshot` leaves the plan unchanged after lease expiry. `test_expired_lease_recovered_and_tenant_jobs_serialized` proves reclaim and per-tenant serialization. |
| Out-of-order subscription events | `test_old_event_cannot_restore_canceled_subscription` and `test_old_cancellation_does_not_remove_new_active_subscription` pass against current-state fixtures. |
| Checkout recovery | `test_expired_checkout_uses_new_durable_key` and `test_canceled_subscription_can_resubscribe` pass. |

## PostgreSQL-specific regression checks

All six were included in the 59-pass run:

| Test | Observed assertion |
|---|---|
| `test_other_tenant_can_meter_while_first_tenant_is_locked` | Beta commits while Alpha's tenant lock remains held. |
| `test_parallel_webhook_deliveries_enqueue_exactly_once` | 16 deliveries through 8 workers create one queue entry. |
| `test_parallel_claims_never_run_two_jobs_for_one_tenant` | 8 competing claims for one tenant yield exactly one running job. |
| `test_parallel_migrations_are_safe` | 4 concurrent migration calls apply the schema once. |
| `test_bigint_money_and_integer_rollup` | 3,000,000,000 micro-USD persists and rolls up as a Python integer. |
| `test_legacy_import_preserves_data_and_refuses_nonempty_target` | Legacy tenant/subscription data is preserved; the source bytes are unchanged; a second import is rejected. |

The separate real-data cutover additionally preserved one existing usage event and its exact 24,000 micro-USD cost. Saved transcript: `docs/evidence/postgresql-import.json`.

## Acceptance probes

1. **Duplicate billable request:** passed both in the suite and over live localhost HTTP.
2. **Exact quota boundary:** passed against real PostgreSQL with all 1,000 Free calls and a rejecting 1,001st call.
3. **Real Stripe Checkout changes Free to Pro:** **not yet run**. A signed fixture proves internal behavior only.
4. **Forged webhook and duplicate real event:** forged-event and deduplication mechanics passed locally. A real Stripe-delivered event replay is still pending.
5. **Pinned AI pricing and `/usage`:** passed in the suite and live HTTP demo.

## Submission checklist

- [x] Implement core metering, pricing, quotas, and tenant isolation.
- [x] Implement and locally test test-mode checkout, webhooks, durable jobs, and alerts.
- [x] Run deterministic tests and native HTTP smoke checks.
- [x] Prepare README, design, manifest, evidence, AI build log, and resume notes.
- [ ] Configure Stripe test credentials locally and complete a real hosted checkout.
- [ ] Record a real Free-to-Pro transition, cancellation, and event replay without exposing secrets.
- [ ] Run Docker if you intend to claim container validation; native operation is already verified.
- [ ] Create a dedicated public GitHub repository and push source with truthful commit history.
- [ ] Run the GitHub Actions workflow and review the public repository for leaked local files.
- [ ] Review and explain the code, then submit the repository URL through the portal.

### Real Stripe evidence to add after completion

Record actual UTC timestamp, sanitized test event ID, `/usage` before and after, `/billing/jobs` processed result, and duplicate-event response. Do not fill this section with fixture output. There is deliberately no placeholder claiming success.
