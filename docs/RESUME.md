# Resume wording and interview preparation

Use the role title and dates that match your internship records. Describe this as an internship capstone; do not imply that it was deployed into FlyRank's production systems.

## Recommended experience entry

**FlyRank | Backend AI Engineer Intern**  
*Add your actual internship dates*

- Built a multi-tenant AI usage metering and billing capstone using Python, FastAPI, and SQLite, with atomic quota enforcement, tenant-scoped authentication, and retry-safe usage tracking.
- Implemented integer-based AI token pricing with cached-input discounts, reasoning-token accounting, monthly cost rollups, and per-tenant budget guards; validated billing behavior with **53 automated tests**.
- Developed Stripe test-mode checkout and signed webhook processing with durable background jobs, event deduplication, retry backoff, lease recovery, and failure alerts.

These bullets reflect the implemented code and local verification. The Stripe bullet says **developed**, not that a real hosted checkout has already been verified. After completing the documented Stripe acceptance step, you can say **“Integrated and verified Stripe test-mode checkout…”**.

## If your resume has room for only two bullets

- Engineered a multi-tenant AI metering API with atomic quotas, idempotent billing events, and precise cached-input/reasoning-token cost accounting using Python, FastAPI, and SQLite.
- Verified retry, quota, isolation, and payment-sync behavior through **53 automated tests**, including concurrent and cross-process requests; implemented signed Stripe webhooks and durable retry workers.

## Optional assignment bullet

The intern reports completing five assignments. Their contents were not provided or reviewed, so avoid inventing skills or outcomes for them. A factual line, if it fits your records:

> Completed five backend AI engineering assignments and developed a usage metering and billing capstone as part of FlyRank's internship program.

Update the wording to “submitted” or “earned the certificate” only after those events actually occur. Usually the technical project bullets are stronger than a generic assignment-count bullet.

## Project section alternative

**AI Usage Metering & Billing Engine | Python, FastAPI, SQLite, Stripe, pytest, Docker**  
*FlyRank internship capstone | add the public repository link after publication*

> Designed retry-safe metering and quota enforcement for API calls and AI tokens, with integer cost accounting, tenant isolation, and asynchronous Stripe subscription synchronization; covered concurrency and failure scenarios with 53 passing automated tests.

Docker configuration is included; do not say the application is container-validated until you run the Docker workflow. Do not list PostgreSQL, Redis, real LLM inference, production scale, latency improvements, revenue savings, or user counts: none were demonstrated here.

## Five interview explanations to practice

1. **Why doesn't a retry double-charge?** A unique tenant/key pair, normalized request hash, and saved response are checked inside the same write transaction as quota enforcement and event insertion. Same key/different counts is 409. The external Stripe system has its own idempotency keys.
2. **What happens when two requests compete for the last allowance?** SQLite's `BEGIN IMMEDIATE` serializes writers. The second transaction sees the first committed event before checking the quota. The database constraint remains the final duplicate guard.
3. **Why not add all four token counts?** Cached tokens are already in input; reasoning tokens are already in output. For 1,000 input, 400 cached, 500 output, and 200 reasoning, total tokens are 1,500 and the pinned cost including the API call is exactly $0.024000.
4. **Why fetch Stripe after receiving an event?** Events may be delayed or reordered. A verified event schedules a current-state fetch; its old payload is not directly written as entitlement truth. Worker leases and fencing prevent stale workers from committing after losing ownership.
5. **What would you change for production?** Trusted provider metering with reservation/settlement, PostgreSQL transaction design, stronger account/key lifecycle controls, reconciliation for missed webhooks, operational monitoring, and a load-tested deployment. Do not claim these are already implemented.

## Short spoken project description

“For my FlyRank backend AI engineering capstone, I built the usage-accounting layer behind an AI SaaS product. It meters API calls and simulated model tokens, enforces tenant quotas, and computes costs without floating-point money. I focused on retries and concurrency: repeated requests return the original result, and concurrent requests cannot overspend the same quota. I also built Stripe test-mode subscription sync with verified webhooks and a durable retry worker. The local suite has 53 passing tests.”
