from contextlib import asynccontextmanager
import hashlib
import psycopg
import threading
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.billing import BillingService, verify_event
from app.postgres import Database
from app.errors import DomainError
from app.metering import MeterService
from app.models import GenerateRequest
from app.stripe_gateway import StripeGateway
from app.worker import Worker
from config import Settings

bearer = HTTPBearer(auto_error=False)


def create_app(settings=None, gateway=None, start_worker=False):
    settings = settings or Settings.from_env()
    db = Database(settings.database_url, settings.database_schema)
    gateway = gateway or StripeGateway(settings)
    meter, billing = MeterService(db), BillingService(db, gateway)
    worker = Worker(db, gateway, settings.stripe_pro_price_id)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        stop = threading.Event()
        thread = None
        if start_worker:
            thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True, name="billing-worker")
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=20)

    app = FastAPI(title="AI Usage Metering & Billing Engine", version="1.0.0", lifespan=lifespan,
                  description="FlyRank backend capstone. Simulated AI usage, integer USD pricing, tenant quotas, and Stripe test-mode subscriptions.")
    app.state.db, app.state.meter, app.state.worker = db, meter, worker

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Exclude raw user input and exception objects from validation responses.
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Invalid request.",
                            "details": [{"field": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]}})

    @app.exception_handler(psycopg.OperationalError)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"error": {"code": "database_unavailable", "message": "Database temporarily unavailable; retry with the same idempotency key."}}, headers={"Retry-After": "1"})

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise DomainError(401, "unauthorized", "A tenant API key is required.", headers={"WWW-Authenticate": "Bearer"})
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        with db.read() as conn:
            tenant = conn.execute("SELECT id FROM tenants WHERE api_key_hash=%s", (digest,)).fetchone()
        if not tenant:
            raise DomainError(401, "unauthorized", "Invalid tenant API key.", headers={"WWW-Authenticate": "Bearer"})
        return tenant["id"]

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home():
        return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>AI Metering | FlyRank Capstone</title><body style="font:18px/1.6 system-ui;max-width:760px;margin:12vh auto;padding:24px;color:#172033;background:#f4f7fb">
        <p style="color:#486580">FLYRANK / BACKEND AI ENGINEERING CAPSTONE</p><h1>Every token accounted for.</h1>
        <p>A tenant-aware usage metering and billing API with precise token pricing, atomic quota checks, and Stripe test-mode subscriptions.</p>
        <p><a href="/docs">Explore the interactive API</a> &nbsp; <a href="/health">Service health</a></p>
        <p>Start with <code>python -m app seed</code>, then use a generated tenant key in the API's <strong>Authorize</strong> dialog.</p>
        <small>AI responses are simulated. No real payments or model calls.</small></body></html>"""

    @app.get("/health", tags=["Operations"])
    def health():
        with db.read() as conn:
            conn.execute("SELECT 1 FROM schema_migrations").fetchone()
        return {"status": "ok", "storage": "postgresql", "payments": "test_mode_only"}

    @app.get("/plans", tags=["Billing"])
    def plans():
        with db.read() as conn:
            return {"plans": [dict(row) for row in conn.execute("SELECT * FROM plans ORDER BY api_call_limit")], "currency_unit": "micro_usd"}

    @app.post("/generate", tags=["Metering"])
    def generate(usage: GenerateRequest, tenant_id: Annotated[str, Depends(authenticate)],
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]):
        result, replayed = meter.record(tenant_id, usage, idempotency_key)
        return JSONResponse(result, headers={"Idempotency-Replayed": str(replayed).lower()})

    @app.get("/usage", tags=["Metering"])
    def usage(tenant_id: Annotated[str, Depends(authenticate)],
              period: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None):
        return meter.summary(tenant_id, period)

    @app.get("/usage/events", tags=["Metering"])
    def events(tenant_id: Annotated[str, Depends(authenticate)], limit: Annotated[int, Query(ge=1, le=100)] = 20,
               offset: Annotated[int, Query(ge=0, le=100000)] = 0):
        with db.read() as conn:
            rows = conn.execute("""SELECT id,period,api_calls,input_tokens,cached_input_tokens,output_tokens,
                reasoning_tokens,total_tokens,cost_micro_usd,pricing_version,created_at FROM usage_events
                WHERE tenant_id=%s ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s""", (tenant_id, limit, offset)).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}

    @app.post("/billing/checkout", tags=["Billing"])
    def checkout(tenant_id: Annotated[str, Depends(authenticate)]):
        return billing.checkout(tenant_id)

    @app.get("/billing/return", response_class=HTMLResponse, include_in_schema=False)
    def checkout_return(result: str = "success"):
        # Redirects are not payment proof and never mutate entitlements.
        message = "Checkout cancelled. Your plan has not changed." if result == "cancelled" else "Checkout returned. Your plan updates after the verified Stripe webhook is processed."
        return f"<!doctype html><html lang='en'><title>Checkout return</title><body style='font:18px system-ui;padding:48px'><h1>{message}</h1><p>Check GET /usage in <a href='/docs'>the API explorer</a>.</p></body></html>"

    @app.post("/webhooks/stripe", tags=["Billing"])
    async def webhook(request: Request, stripe_signature: Annotated[str, Header()] = ""):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1_048_576:
                raise DomainError(413, "payload_too_large", "Webhook exceeds 1 MiB.")
        event = verify_event(bytes(raw), stripe_signature, settings.stripe_webhook_secret)
        # Dispatch database work to the threadpool to keep the event loop free.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(billing.receive, event)

    @app.get("/billing/jobs", tags=["Operations"])
    def jobs(tenant_id: Annotated[str, Depends(authenticate)]):
        with db.read() as conn:
            rows = conn.execute("SELECT id,event_id,status,attempts,last_error FROM jobs WHERE tenant_id=%s ORDER BY created_at DESC,id DESC LIMIT 100", (tenant_id,)).fetchall()
        return {"items": [dict(row) for row in rows]}

    @app.get("/billing/alerts", tags=["Operations"])
    def alerts(tenant_id: Annotated[str, Depends(authenticate)]):
        with db.read() as conn:
            rows = conn.execute("SELECT id,job_id,code,message,created_at FROM alerts WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100", (tenant_id,)).fetchall()
        return {"items": [dict(row) for row in rows]}

    return app
