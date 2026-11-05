CREATE TABLE plans (
    id TEXT PRIMARY KEY,
    api_call_limit INTEGER NOT NULL CHECK(api_call_limit > 0),
    ai_token_limit INTEGER NOT NULL CHECK(ai_token_limit > 0),
    monthly_price_micro_usd BIGINT NOT NULL CHECK(monthly_price_micro_usd >= 0)
);
INSERT INTO plans VALUES ('free', 1000, 100000, 0), ('pro', 10000, 1000000, 19000000);

CREATE TABLE tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    monthly_budget_micro_usd BIGINT NOT NULL DEFAULT 50000000 CHECK(monthly_budget_micro_usd > 0),
    stripe_customer_id TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE subscriptions (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    plan_id TEXT NOT NULL DEFAULT 'free' REFERENCES plans(id),
    status TEXT NOT NULL DEFAULT 'free',
    stripe_subscription_id TEXT UNIQUE,
    synced_at TIMESTAMPTZ
);
CREATE TABLE usage_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    period TEXT NOT NULL,
    api_calls INTEGER NOT NULL CHECK(api_calls = 1),
    input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
    cached_input_tokens INTEGER NOT NULL CHECK(cached_input_tokens BETWEEN 0 AND input_tokens),
    output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
    reasoning_tokens INTEGER NOT NULL CHECK(reasoning_tokens BETWEEN 0 AND output_tokens),
    total_tokens INTEGER NOT NULL CHECK(total_tokens = input_tokens + output_tokens),
    cost_micro_usd BIGINT NOT NULL CHECK(cost_micro_usd >= 0),
    pricing_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(tenant_id, idempotency_key)
);
CREATE INDEX usage_tenant_period ON usage_events(tenant_id, period);
CREATE INDEX usage_tenant_created ON usage_events(tenant_id, created_at);
CREATE TABLE webhook_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    tenant_id TEXT REFERENCES tenants(id),
    status TEXT NOT NULL CHECK(status IN ('queued', 'processed', 'ignored', 'failed')),
    received_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE REFERENCES webhook_events(event_id),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'done', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at BIGINT NOT NULL,
    lease_until BIGINT,
    lease_token TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX jobs_poll ON jobs(status, available_at);
CREATE INDEX jobs_tenant ON jobs(tenant_id, status);
CREATE TABLE alerts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE checkout_sessions (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    generation INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL,
    stripe_session_id TEXT,
    checkout_url TEXT,
    expires_at BIGINT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX one_running_job_per_tenant ON jobs(tenant_id) WHERE status='running';
