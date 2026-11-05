# Resume wording and interview preparation

Use the role title and dates that match your internship records. Describe this as an internship capstone; do not imply that it was deployed into FlyRank's production systems.

## Recommended experience entry

**FlyRank | Backend AI Engineer Intern**  
*Add your actual internship dates*

- Built a multi-tenant AI billing backend using Python, FastAPI, and PostgreSQL, implementing integer-based token pricing and **2 subscription tiers** with configurable quotas of up to **10,000 API calls and 1 million tokens per tenant/month**.
- Validated metering, quota enforcement, and tenant isolation through **59 passing automated tests**; demonstrated that **32 retry attempts produced exactly 1 billable event** in a concurrency test.
- Developed Stripe test-mode checkout and signed webhook processing with durable background jobs, **5-attempt retry handling**, **120-second worker leases**, and automated failure alerts.

These bullets reflect the implemented code and local verification. The Stripe bullet says **developed**, not that a real hosted checkout has already been verified. After completing the documented Stripe acceptance step, you can say **“Integrated and verified Stripe test-mode checkout…”**.

## If your resume has room for only two bullets

- Engineered a multi-tenant AI billing API with precise token pricing and **2 subscription tiers**, enforcing monthly quotas of up to **10,000 API calls and 1 million tokens per tenant** using Python, FastAPI, and PostgreSQL.
- Passed **59 automated tests**, including a concurrency test reducing **32 retry attempts to 1 billable event**; implemented signed Stripe webhooks and background jobs with **5-attempt retry handling**.

## Optional assignment bullet

The intern reports completing five assignments. Their contents were not provided or reviewed, so avoid inventing skills or outcomes for them. A factual line, if it fits your records:

> Completed five backend AI engineering assignments and developed a usage metering and billing capstone as part of FlyRank's internship program.

Update the wording to “submitted” or “earned the certificate” only after those events actually occur. Usually the technical project bullets are stronger than a generic assignment-count bullet.

## Project section alternative

**AI Usage Metering & Billing Engine | Python, FastAPI, PostgreSQL, Stripe, pytest, Docker**  
*FlyRank internship capstone | add the public repository link after publication*

> Built an AI billing backend with 2 subscription tiers and integer token-cost accounting; passed 59 automated tests, including 32 retry attempts producing 1 billable event, and implemented Stripe synchronization with 5-attempt job retries.

## What the numbers demonstrate

| Number | Evidence and scope |
|---|---|
| 2 tiers; 10,000 calls / 1 million tokens | Configured Free/Pro plan allowances, not measured production traffic or throughput. |
| 59 passing tests | Recorded local suite result in `EVIDENCE.md`; not a code-coverage percentage. |
| 32 attempts / 1 billable event | Concurrency test submits 32 identical attempts through a pool of 16 workers; not a claim of 32 simultaneous in-flight requests. |
| 8 calls across 4 processes | Additional cross-process test produces one event; useful as an interview detail. |
| 5 attempts; 120-second leases | Implemented worker retry limit and lease duration; not a recovery-time or uptime guarantee. |

Do not turn these into a percentage reduction in production billing errors, revenue savings, or latency improvements. Those outcomes were not measured.

Docker configuration is included; do not say the application is container-validated until you run the Docker workflow. Do not list Redis, real LLM inference, production scale, latency improvements, revenue savings, or user counts: none were demonstrated here.

## Five interview explanations to practice

1. **Why doesn't a retry double-charge?** A unique tenant/key pair, normalized request hash, and saved response are checked inside the same write transaction as quota enforcement and event insertion. Same key/different counts is 409. The external Stripe system has its own idempotency keys.
2. **What happens when two requests compete for the last allowance?** PostgreSQL's `FOR NO KEY UPDATE` locks the tenant row inside a read-committed transaction. The next request for that tenant sees the previous committed event before checking the quota; another tenant can write independently. The database constraint remains the final duplicate guard.
3. **Why not add all four token counts?** Cached tokens are already in input; reasoning tokens are already in output. For 1,000 input, 400 cached, 500 output, and 200 reasoning, total tokens are 1,500 and the pinned cost including the API call is exactly $0.024000.
4. **Why fetch Stripe after receiving an event?** Events may be delayed or reordered. A verified event schedules a current-state fetch; its old payload is not directly written as entitlement truth. Worker leases and fencing prevent stale workers from committing after losing ownership.
5. **What would you change for production?** Trusted provider metering with reservation/settlement, connection pooling and capacity planning, stronger account/key lifecycle controls, reconciliation for missed webhooks, operational monitoring, and a load-tested deployment. Do not claim these are already implemented.

## Short spoken project description

“For my FlyRank backend AI engineering capstone, I built the usage-accounting layer behind an AI SaaS product. It meters API calls and simulated model tokens, enforces tenant quotas, and computes costs without floating-point money. I focused on retries and concurrency: repeated requests return the original result, and concurrent requests cannot overspend the same quota. I also built Stripe test-mode subscription sync with verified webhooks and a durable retry worker. The local suite has 59 passing tests.”
