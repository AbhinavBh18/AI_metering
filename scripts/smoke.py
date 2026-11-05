"""Live localhost checks and a sanitized demo transcript for the evidence pack."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import httpx
from demo import main as demo


def main():
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as client:
        health = client.get("/health")
        assert health.status_code == 200 and health.json()["status"] == "ok"
        assert health.json()["storage"] == "postgresql"
        docs = client.get("/docs")
        assert docs.status_code == 200
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        assert "/generate" in schema.json()["paths"]
        assert "/webhooks/stripe" in schema.json()["paths"]
        assert client.get("/usage").status_code == 401
        assert client.get("/").status_code == 200
    buffer = StringIO()
    with redirect_stdout(buffer):
        demo()
    result = {"health": health.json(), "docs_status": docs.status_code,
              "openapi_status": schema.status_code, "unauthenticated_usage_status": 401,
              "demo": json.loads(buffer.getvalue())}
    output = json.dumps(result, indent=2)
    destination = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "http-smoke.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
