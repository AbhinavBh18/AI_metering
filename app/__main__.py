import argparse
import json
import logging
import uvicorn
from app.postgres import Database
from app.main import create_app
from app.seed import create_tenant, seed_demo
from app.stripe_gateway import StripeGateway
from app.worker import Worker
from config import Settings


def main():
    parser = argparse.ArgumentParser(description="AI usage metering capstone")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run HTTP API and durable billing worker")
    run.add_argument("--host", default="127.0.0.1")
    sub.add_parser("seed", help="Seed two demo tenants without resetting existing data")
    sub.add_parser("migrate")
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    tenant = sub.add_parser("tenant")
    tenant.add_argument("--name", required=True)
    requeue = sub.add_parser("requeue", help="Operator-only CLI: retry a failed billing job")
    requeue.add_argument("job_id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    db = Database(settings.database_url, settings.database_schema)
    db.migrate()
    if args.command == "run":
        uvicorn.run(create_app(settings, start_worker=True), host=args.host, port=settings.port)
    elif args.command == "seed":
        print(json.dumps(seed_demo(db), indent=2))
    elif args.command == "tenant":
        print(json.dumps(create_tenant(db, args.name), indent=2))
    elif args.command == "migrate":
        print("Migrations applied.")
    elif args.command == "worker":
        worker = Worker(db, StripeGateway(settings), settings.stripe_pro_price_id)
        if args.once:
            print(json.dumps({"worked": worker.run_once()}))
        else:
            worker.run_forever()
    elif args.command == "requeue":
        with db.transaction() as conn:
            job = conn.execute("SELECT event_id FROM jobs WHERE id=%s AND status='failed' FOR UPDATE", (args.job_id,)).fetchone()
            if not job:
                parser.error("No failed job with that ID")
            conn.execute("UPDATE jobs SET status='queued',attempts=0,available_at=0,last_error=NULL WHERE id=%s", (args.job_id,))
            conn.execute("UPDATE webhook_events SET status='queued' WHERE event_id=%s", (job["event_id"],))
        print("Job requeued. The historical failure alert is retained.")


if __name__ == "__main__":
    main()
