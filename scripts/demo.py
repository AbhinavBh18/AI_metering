"""Live HTTP demo. Run after `python -m app seed` and `python -m app run`."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--credentials", default="data/demo-credentials.json")
    args = parser.parse_args()
    tenant = json.loads(Path(args.credentials).read_text())[0]
    headers = {"Authorization": "Bearer " + tenant["api_key"], "Idempotency-Key": "demo-" + str(uuid4())}
    body = {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 500, "reasoning_tokens": 200}
    with httpx.Client(base_url=args.base_url, timeout=15) as client:
        before = client.get("/usage", headers=headers)
        before.raise_for_status()
        first = client.post("/generate", headers=headers, json=body)
        first.raise_for_status()
        second = client.post("/generate", headers=headers, json=body)
        second.raise_for_status()
        after = client.get("/usage", headers=headers)
        after.raise_for_status()
    assert first.json() == second.json()
    assert after.json()["used"]["event_count"] - before.json()["used"]["event_count"] == 1
    assert first.json()["cost"]["total_micro_usd"] == 24000
    print(json.dumps({"first_status": first.status_code, "retry_status": second.status_code,
                      "identical_response": first.json() == second.json(),
                      "new_usage_events": 1, "total_tokens": first.json()["usage"]["total_tokens"],
                      "cost_micro_usd": first.json()["cost"]["total_micro_usd"],
                      "cost_usd": first.json()["cost"]["total_usd"]}, indent=2))


if __name__ == "__main__":
    main()
