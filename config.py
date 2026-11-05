"""Pinned capstone prices, not a claim about any AI provider's live rates."""
from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
MICRO_USD_PER_USD = 1_000_000
PRICING_VERSION = "demo-2026-11-v1"


class PRICING:
    API_CALL = 1_000  # $0.001 per call
    AI_INPUT_TOKEN = 10  # $0.01 / 1,000 uncached input tokens
    AI_CACHED_INPUT_TOKEN = 5  # $0.005 / 1,000 cached input tokens
    AI_OUTPUT_TOKEN = 30  # $0.03 / 1,000 output tokens, including reasoning


@dataclass(frozen=True)
class Settings:
    database_path: str = str(ROOT / "data" / "metering.db")
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_pro_price_id: str = ""
    public_base_url: str = "http://localhost:8000"
    port: int = 8000

    def __post_init__(self):
        if self.stripe_secret_key and not self.stripe_secret_key.startswith("sk_test_"):
            raise ValueError("Only Stripe sk_test_ keys are allowed")
        if not self.public_base_url.startswith(("http://localhost:", "http://127.0.0.1:", "https://")):
            raise ValueError("PUBLIC_BASE_URL must be localhost HTTP or HTTPS")

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            database_path=os.getenv("DATABASE_PATH", str(ROOT / "data" / "metering.db")),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY", ""),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            stripe_pro_price_id=os.getenv("STRIPE_PRO_PRICE_ID", ""),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
            port=int(os.getenv("PORT", "8000")),
        )
