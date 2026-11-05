from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4
from app.db import tenant_plan, rollup
from app.errors import DomainError
from app.pricing import price_usage, usd_string
from config import PRICING_VERSION


def utc_now():
    return datetime.now(timezone.utc)


def month_bounds(now):
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


class MeterService:
    def __init__(self, db, clock=utc_now):
        self.db, self.clock = db, clock

    def record(self, tenant_id, usage, key):
        fingerprint = hashlib.sha256(json.dumps(usage.model_dump(), sort_keys=True).encode()).hexdigest()
        with self.db.transaction() as conn:
            old = conn.execute("SELECT request_hash, response_json FROM usage_events WHERE tenant_id=? AND idempotency_key=?", (tenant_id, key)).fetchone()
            if old:
                if old["request_hash"] != fingerprint:
                    raise DomainError(409, "idempotency_conflict", "This key was already used with different token counts.")
                return json.loads(old["response_json"]), True
            now = self.clock()
            period = now.strftime("%Y-%m")
            _, reset = month_bounds(now)
            tenant = tenant_plan(conn, tenant_id)
            if tenant["status"] in {"past_due", "unpaid", "incomplete", "paused"}:
                raise DomainError(402, "payment_required", "Subscription payment is required before generating.", {"subscription_status": tenant["status"]})
            used = rollup(conn, tenant_id, period)
            quantities = {"api_calls": 1, "ai_tokens": usage.input_tokens + usage.output_tokens}
            limits = {"api_calls": tenant["api_call_limit"], "ai_tokens": tenant["ai_token_limit"]}
            for metric, requested in quantities.items():
                if used[metric] + requested > limits[metric]:
                    raise DomainError(429, "quota_exceeded", f"Monthly {metric} quota exceeded. Upgrade or wait for the next UTC month.",
                                      {"metric": metric, "used": used[metric], "requested": requested, "limit": limits[metric], "resets_at": reset.isoformat()},
                                      {"Retry-After": str(max(1, int((reset - now).total_seconds())))})
            cost = price_usage(usage)
            if used["cost_micro_usd"] + cost["total_micro_usd"] > tenant["monthly_budget_micro_usd"]:
                raise DomainError(402, "budget_exceeded", "Monthly estimated usage-cost budget would be exceeded.",
                                  {"used_micro_usd": used["cost_micro_usd"], "requested_micro_usd": cost["total_micro_usd"], "budget_micro_usd": tenant["monthly_budget_micro_usd"]})
            event_id = str(uuid4())
            response = {"event_id": event_id, "tenant_id": tenant_id, "period": period,
                        "result": "Simulated AI response; no model was called.",
                        "usage": {"api_calls": 1, **usage.model_dump(), "total_tokens": quantities["ai_tokens"]},
                        "cost": cost, "created_at": now.isoformat()}
            conn.execute("INSERT INTO usage_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, tenant_id, key, fingerprint, period, 1, usage.input_tokens, usage.cached_input_tokens,
                 usage.output_tokens, usage.reasoning_tokens, quantities["ai_tokens"], cost["total_micro_usd"],
                 PRICING_VERSION, json.dumps(response), now.isoformat()))
            return response, False

    def summary(self, tenant_id, period=None):
        now = self.clock()
        period = period or now.strftime("%Y-%m")
        with self.db.read() as conn:
            tenant = tenant_plan(conn, tenant_id)
            used = rollup(conn, tenant_id, period)
        return {"tenant_id": tenant_id, "period": period, "plan": tenant["plan_id"],
                "subscription_status": tenant["status"], "used": used,
                "limits": {"api_calls": tenant["api_call_limit"], "ai_tokens": tenant["ai_token_limit"]},
                "budget_micro_usd": tenant["monthly_budget_micro_usd"],
                "usage_cost_usd": usd_string(used["cost_micro_usd"]),
                "current_plan_monthly_price_usd": usd_string(tenant["monthly_price_micro_usd"]),
                "note": "Usage costs are estimates, not collected charges. Limits and plan describe current entitlements; usage describes the selected UTC month."}
