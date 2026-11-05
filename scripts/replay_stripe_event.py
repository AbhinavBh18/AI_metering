"""Explicit local replay helper; never fabricates hosted-checkout evidence."""
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/replay_stripe_event.py PATH_TO_RETRIEVED_EVENT_JSON")
    settings = Settings.from_env()
    if not settings.stripe_webhook_secret:
        raise SystemExit("Configure STRIPE_WEBHOOK_SECRET in .env first.")
    event = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    if event.get("livemode") is not False:
        raise SystemExit("Only real test-mode event exports may be replayed.")
    raw = json.dumps(event).encode()
    timestamp = int(time.time())
    signature = hmac.new(settings.stripe_webhook_secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    response = httpx.post("http://localhost:8000/webhooks/stripe", content=raw,
                          headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"}, timeout=15)
    print(response.status_code, response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
