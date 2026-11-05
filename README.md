# AI Usage Metering & Billing Engine

**FlyRank internship capstone | Backend AI Engineer track**

A small backend built around billing correctness: authenticated tenant usage, atomic quotas, precise AI-token cost attribution, and Stripe test-mode subscription synchronization. AI responses are simulated; no model API key is required.

```text
Client + tenant API key
        |
        v
FastAPI: validation / authentication / HTTP errors
        |
        +-- POST /generate --> MeterService --> BEGIN IMMEDIATE
        |                                      | deduplicate + fingerprint
        |                                      | check quota + budget
        |                                      + insert event + saved response
        |
        +-- GET /usage ------> indexed monthly ledger rollup
        |
        +-- POST /billing/checkout --> Stripe test Checkout
                                             |
Stripe CLI --> signed webhook --> verify --> event + durable job (one transaction)
                                                         |
                       SQLite <-- entitlement update <-- background worker
                                                         | current Stripe state
                                                         | lease + retries
                                                         + failure alert
```

## What makes the project useful

- **Retry-safe ledger:** one accepted action creates one event, with a database uniqueness constraint and stored response replay. Key reuse with different token counts is rejected.
- **Race-safe quotas:** quota check and insertion commit together, preventing parallel requests from spending the same remaining allowance.
- **AI-specific cost accounting:** cached input is discounted; reasoning is included in output, never double-counted; every cost uses integer microdollars.
- **Tenant isolation:** random bearer keys map to tenants server-side. Only key hashes are stored in the database.
- **Durable payment synchronization:** signed test-mode events are deduplicated and queued; workers fetch current Stripe state, retry failures, fence expired leases, and persist terminal alerts.
- **Reproducible review:** migrations, automated acceptance tests, live demo script, evidence, design decisions, and an honest AI build log.

This is a local capstone implementation. See [EVIDENCE.md](EVIDENCE.md) for verified behavior and the pending real Stripe checkout step.

## Run locally

Requires **Python 3.14**. Run commands from this repository. No Docker or Stripe account is needed for metering, quotas, pricing, or the automated suite.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app seed
.\.venv\Scripts\python.exe -m app run
```

macOS / Linux:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt -c constraints.txt
cp .env.example .env
python -m app seed
python -m app run
```

After activating a virtual environment, the single run command is **`python -m app run`**. It applies migrations and starts both the API and billing worker. The seed step is **`python -m app seed`**; it creates two isolated Free tenants and writes their random credentials to git-ignored `data/demo-credentials.json`. Running it again does not reset usage or keys.

- API explorer: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health: [http://localhost:8000/health](http://localhost:8000/health)
- OpenAPI: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

Click **Authorize** in the API explorer and paste one demo tenant's `api_key`. The server derives tenant identity from this key; do not supply an arbitrary tenant ID. Demo credentials are local only, not embedded in the source. If you lose a local key, create another tenant with `python -m app tenant --name "New demo"`.

In another terminal, run the live acceptance demo:

```powershell
.\.venv\Scripts\python.exe scripts/demo.py
```

It sends the same request twice, checks identical responses and a one-event ledger increase, and verifies the exact $0.024000 estimate. Each demo run adds one new event.

## API contract

| Method | Endpoint | Authentication | Behavior |
|---|---|---|---|
| GET | `/health` | None | Database readiness |
| GET | `/plans` | None | Plan allowances and fixed monthly prices |
| POST | `/generate` | Bearer + `Idempotency-Key` | Simulate one billable API action |
| GET | `/usage?period=2026-11` | Bearer | Selected UTC month; defaults to current month |
| GET | `/usage/events?limit=20&offset=0` | Bearer | Tenant ledger, bounded pagination |
| POST | `/billing/checkout` | Bearer | Create/reuse a Stripe test checkout |
| POST | `/webhooks/stripe` | Stripe signature | Verify and durably queue an event |
| GET | `/billing/jobs` | Bearer | Latest 100 tenant jobs and retry states |
| GET | `/billing/alerts` | Bearer | Tenant's terminal sync failures |

Example request body:

```json
{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":500,"reasoning_tokens":200}
```

Counts must be nonnegative JSON integers, at most 10 million per field. Booleans, strings, unknown fields, and invalid subsets return 422. A zero-token request still consumes one API call. `Idempotency-Key` accepts 1-128 letters, digits, periods, underscores, colons, or hyphens.

| Status | Meaning |
|---|---|
| 200 | Accepted or replayed; body identical for a valid replay |
| 401 | Missing/invalid tenant key |
| 402 | Delinquent payment state or estimated usage budget exceeded |
| 409 | Idempotency conflict, existing subscription, or checkout awaiting sync |
| 422 | Invalid request |
| 429 | Monthly usage quota exceeded, with `Retry-After` in seconds |
| 400 | Invalid Stripe signature, expired timestamp, or live-mode event |
| 502 / 503 | Upstream/configuration/database unavailable; explanatory code included |

## Quotas and money

| Plan | API calls / UTC month | AI tokens / UTC month | Stripe fixed monthly price |
|---|---:|---:|---:|
| Free | 1,000 | 100,000 | $0 |
| Pro | 10,000 | 1,000,000 | $19 in test mode |

Exactly at a limit is allowed; the first request that would exceed it is rejected without inserting an event. Both plans have a default **$50 estimated usage-cost budget per tenant**, stored separately from the subscription fee. Past-due, unpaid, incomplete, and paused subscriptions return 402. Canceled subscriptions fall back to Free. UTC monthly usage resets logically by selecting a new period; no destructive reset job is needed.

Pinned demo rates live in `config.py`, with price version `demo-2026-11-v1`. They are illustrative rates, not current provider quotes:

| Component | Integer micro-USD rate |
|---|---:|
| One API call | 1,000 |
| One uncached input token | 10 |
| One cached input token | 5 |
| One output token, including reasoning | 30 |

**1 USD = 1,000,000 micro-USD.** Cached input is a subset of input; reasoning is a subset of output. For the sample above:

```text
total_tokens = 1,000 input + 500 output = 1,500
cost = 1,000 API + (1,000 - 400) * 10 + 400 * 5 + 500 * 30
     = 24,000 micro-USD = $0.024000
```

Usage estimates are **not** collected by Stripe. Stripe bills only the fixed Pro subscription in test mode. No overage charging, invoice generation, or proration is implemented. Historical usage is returned with current plan limits; it is not a historical entitlement snapshot.

## Stripe and worker operations

Follow [the Stripe setup guide](docs/STRIPE_SETUP.md) to complete the real test checkout. Blank Stripe settings leave the rest of the service usable; checkout returns a clear 503. Only `sk_test_` keys and `livemode: false` events are accepted. A checkout redirect cannot upgrade a tenant.

The background worker runs with the API command. A separate process is also available via `python -m app worker`; `--once` performs one attempt. Jobs are claimed with a 120-second lease and serialized per tenant. Expired leases can be reclaimed; a fencing token prevents an old worker from writing stale results. Five failed attempts lead to a durable alert and a sanitized error log. Retry intervals are 2, 4, 8, and 16 seconds before the fifth attempt. Inspect `/billing/jobs` and `/billing/alerts`, then use the local operator command `python -m app requeue JOB_ID` after fixing the cause.

The worker lists current subscriptions for the mapped customer and configured Pro price, with pagination. Active/trialing subscriptions grant Pro; otherwise delinquent states block new usage, and terminal states fall back to Free. This handles delayed/reordered event payloads. A later Stripe change still requires another webhook; there is no periodic reconciliation job.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify.py
```

The second command also saves the real verbose result to `docs/evidence/test-results.txt`. Tests include 32 concurrent retries, competing requests at a quota boundary, multiple processes sharing one database, all 1,000 Free calls, UTC year rollover, malformed input, forged/replayed webhooks, Stripe REST contract fixtures, worker crashes/leases/retries, cancellation, and checkout recovery. Tests never contact Stripe and use isolated temporary databases. GitHub Actions runs the suite after the repository is published.

## Optional Docker path

```text
docker compose up --build
docker compose exec api python -m app seed
docker compose exec api cat /app/data/demo-credentials.json
```

The final command displays local demo keys; do not paste them into public evidence. The app runs as a non-root user and persists SQLite in a named volume. The port binds only to localhost. Docker configuration is supplied but was not runtime-verified in the development environment because the Docker engine was stopped.

## Repository map

```text
app/                 HTTP, domain services, storage, Stripe gateway, worker, CLI
migrations/          Versioned SQL schema and indexes
tests/               Deterministic behavioral and boundary tests
scripts/             Live demo, verification capture, local Stripe event replay
docs/DESIGN.md       One-page architecture and tradeoffs
docs/STRIPE_SETUP.md Remaining external integration procedure
docs/RESUME.md       Honest resume bullets and interview talking points
EVIDENCE.md          Requirement-to-proof matrix and outstanding gates
BUILDLOG.md          AI assistance, corrections, and verification history
capstone.yaml        Evaluator run, seed, test, and probe commands
```

## Limitations and submission status

- Built for a single-host capstone. SQLite serializes writers; no throughput or production-scale claim is made. Moving to Postgres requires tenant-row locking or equivalent serializable logic, not simply changing the connection string.
- AI usage is supplied to a simulator. A real inference service must meter trusted provider counts and reserve quota before generation, then settle actual usage.
- Local bearer keys are sufficient for the demo, but key rotation, organization membership/RBAC, public signup, abuse rate limits, and TLS termination are outside scope.
- Signed events for unknown customers are ignored. Provision customers through this app. There is no automatic reconciliation for missed events, external alert delivery, or queue retention policy.
- The API acknowledges a verified event after durable enqueueing; `/usage` changes asynchronously. A worker failure can temporarily leave stale entitlements and creates a visible failure alert after retries.
- A real hosted Stripe checkout, Docker execution, remote CI run, and public GitHub publication are not claimed as completed. See the exact submission checklist in `EVIDENCE.md`.
- Publish this as its own public repository, suggested name `flyrank-capstone-metering-billing`, after checking that `.env`, `data/`, and local credentials remain ignored. Submit the repository URL through the internship portal. Do not claim fabricated earlier commits or a production deployment.

## References

The implementation follows Stripe's documented [Checkout parameters](https://docs.stripe.com/api/checkout/sessions/create), [webhook verification and delivery semantics](https://docs.stripe.com/webhooks), and [subscription listing API](https://docs.stripe.com/api/subscriptions/list). The project requirements come from the supplied FlyRank capstone brief; no external document was treated as authority to publish code, access accounts, or submit the project on the intern's behalf.
