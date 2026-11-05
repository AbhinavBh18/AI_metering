"""Durable jobs: per-tenant serialization, expiring leases, retries, failure alerts."""
import logging
import threading
import time
from uuid import uuid4
from app.metering import utc_now
from app.postgres import lock_tenant

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
LEASE_SECONDS = 120


class Worker:
    def __init__(self, db, gateway, price_id, clock=time.time):
        self.db, self.gateway, self.price_id, self.clock = db, gateway, price_id, clock

    def fail(self, conn, job, code):
        terminal = job["attempts"] >= MAX_ATTEMPTS
        conn.execute("UPDATE jobs SET status=%s,available_at=%s,lease_until=NULL,lease_token=NULL,last_error=%s WHERE id=%s",
                     ("failed" if terminal else "queued", int(self.clock()) + min(300, 2 ** job["attempts"]), code, job["id"]))
        if terminal:
            conn.execute("UPDATE webhook_events SET status='failed' WHERE event_id=%s", (job["event_id"],))
            conn.execute("INSERT INTO alerts VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (job_id) DO NOTHING",
                         (str(uuid4()), job["tenant_id"], job["id"], "billing_sync_failed",
                          "Stripe synchronization exhausted retries. Inspect configuration, then requeue this job.", utc_now().isoformat()))
            logger.error("billing_sync_failed job_id=%s tenant_id=%s", job["id"], job["tenant_id"])

    def claim(self):
        now = int(self.clock())
        with self.db.transaction() as conn:
            # Serialize short queue claims, never network work or metering.
            conn.execute("SELECT pg_advisory_xact_lock(30, hashtext(current_schema()))")
            expired = conn.execute("SELECT * FROM jobs WHERE status='running' AND lease_until<=%s FOR UPDATE", (now,)).fetchall()
            for job in expired:
                self.fail(conn, job, "lease_expired")
            job = conn.execute("""SELECT j.* FROM jobs j WHERE j.status='queued' AND j.available_at<=%s
                AND NOT EXISTS (SELECT 1 FROM jobs active WHERE active.tenant_id=j.tenant_id AND active.status='running')
                ORDER BY j.available_at,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED""", (now,)).fetchone()
            if not job:
                return None
            token = str(uuid4())
            conn.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease_until=%s,lease_token=%s WHERE id=%s",
                         (now + LEASE_SECONDS, token, job["id"]))
            return dict(conn.execute("SELECT * FROM jobs WHERE id=%s", (job["id"],)).fetchone())

    def snapshot(self, customer_id):
        matches = []
        for sub in self.gateway.subscriptions(customer_id):
            if sub.get("livemode") is not False or sub.get("customer") != customer_id:
                raise ValueError("invalid Stripe subscription")
            if any(item.get("price", {}).get("id") == self.price_id for item in sub.get("items", {}).get("data", [])):
                matches.append(sub)
        entitled = [s for s in matches if s["status"] in {"active", "trialing"}]
        blocking = [s for s in matches if s["status"] in {"past_due", "unpaid", "incomplete", "paused"}]
        candidates = entitled or blocking or matches
        if not candidates:
            return "free", "free", None
        newest = max(candidates, key=lambda s: (s["created"], s["id"]))
        return ("pro" if entitled else "free", newest["status"], newest["id"])

    def run_once(self):
        job = self.claim()
        if not job:
            return False
        try:
            with self.db.read() as conn:
                customer = conn.execute("SELECT stripe_customer_id FROM tenants WHERE id=%s", (job["tenant_id"],)).fetchone()["stripe_customer_id"]
            plan, status, subscription_id = self.snapshot(customer)
            with self.db.transaction() as conn:
                lock_tenant(conn, job["tenant_id"])
                owned = conn.execute("SELECT 1 FROM jobs WHERE id=%s AND status='running' AND lease_token=%s AND lease_until>%s FOR UPDATE",
                                     (job["id"], job["lease_token"], int(self.clock()))).fetchone()
                if owned:
                    conn.execute("UPDATE subscriptions SET plan_id=%s,status=%s,stripe_subscription_id=%s,synced_at=%s WHERE tenant_id=%s",
                                 (plan, status, subscription_id, utc_now().isoformat(), job["tenant_id"]))
                    conn.execute("UPDATE jobs SET status='done',lease_until=NULL,lease_token=NULL,last_error=NULL WHERE id=%s", (job["id"],))
                    conn.execute("UPDATE webhook_events SET status='processed' WHERE event_id=%s", (job["event_id"],))
        except Exception as exc:
            # Store only the exception class; upstream messages can contain sensitive data.
            with self.db.transaction() as conn:
                owned = conn.execute("SELECT 1 FROM jobs WHERE id=%s AND status='running' AND lease_token=%s FOR UPDATE", (job["id"], job["lease_token"])).fetchone()
                if owned:
                    self.fail(conn, job, type(exc).__name__)
        return True

    def run_forever(self, stop=None):
        stop = stop or threading.Event()
        logger.info("Billing worker started")
        while not stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.error("worker_poll_failed; retrying in 1 second")
                worked = False
            if not worked:
                stop.wait(1)
