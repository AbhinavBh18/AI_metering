# Design: AI usage metering and billing

## Problem and boundary
Attribute API calls and simulated AI tokens to authenticated tenants, reject usage that exceeds monthly allowances, estimate exact costs, and mirror Stripe test-mode subscriptions. This is a backend capstone, not a deployed FlyRank production service. Non-goal: collecting usage overages, issuing invoices, or making real model calls.

## Model
`plans` defines Free (1,000 calls / 100,000 tokens) and Pro (10,000 / 1,000,000). `tenants` stores a hash of a random API key, a $50 monthly estimated-usage budget, and a unique Stripe customer mapping. `subscriptions` holds one current entitlement record per tenant. `usage_events` is the billing ledger; `(tenant_id, idempotency_key)` is unique and `(tenant_id, period)` is indexed. `webhook_events`, `jobs`, and `alerts` form a durable processing queue; `checkout_sessions` preserves external idempotency keys across retries. SQL migrations are transactional and versioned.

## API and layers
FastAPI handles authentication, validation, and HTTP mapping. `MeterService` owns quotas, replay semantics, and pricing. `BillingService` owns checkout and verified-event ingestion. `Worker` performs asynchronous Stripe reads. `Database` owns transactions and rollups; `StripeGateway` is the only external HTTP adapter. See README for the full endpoint table and architecture diagram.

## Correctness decisions
- One accepted `/generate` produces one combined event with both usage dimensions. `BEGIN IMMEDIATE` covers deduplication, quota checks, and insertion. A unique database constraint is the final duplicate guard.
- Identical retries return stored JSON, including the original event ID and timestamp, even after the month or subscription changes. Different normalized counts with the same key return 409. Failed requests are not charged or retained as successful idempotency records.
- Quotas use inclusive limits: `used + requested <= limit`. Over-quota is 429 with `Retry-After`; delinquent subscriptions and budget rejection are 402. Months use UTC calendar boundaries, independent of Stripe billing anniversaries.
- Money is integer microdollars. Cached input is a subset of input; reasoning is a subset of output. Historical costs and price versions are stored, never recalculated at new rates.
- Random tenant keys identify the tenant; clients cannot select an arbitrary tenant ID. No cross-tenant read routes exist.
- Stripe signatures cover the raw request body and a timestamp with 300-second tolerance. Verified event IDs and queue insertion commit together. Only known Stripe customers are eligible for synchronization.
- The worker reads current matching-price subscriptions instead of applying possibly stale event payloads. It serializes work per tenant; lease tokens prevent an expired worker from committing. Five attempts use exponential backoff, then persist a failure alert and emit a sanitized error log.

## Tradeoffs
SQLite permits one writer at a time: excellent for a compact demonstrable service, not a high-throughput billing fleet. Usage totals are indexed ledger sums, not cached counters. Hosted checkout and asynchronous entitlement updates are separate: a redirect never grants Pro. The queue provides at-least-once execution with idempotent database effects, not exactly-once network delivery. An AI provider integration would additionally need reservation/settlement for unknown output-token counts.
