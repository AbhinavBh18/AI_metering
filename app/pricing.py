from config import PRICING, PRICING_VERSION, MICRO_USD_PER_USD
from app.models import GenerateRequest


def usd_string(micro_usd: int) -> str:
    return f"{micro_usd // MICRO_USD_PER_USD}.{micro_usd % MICRO_USD_PER_USD:06d}"


def price_usage(usage: GenerateRequest):
    parts = {
        "api_calls": PRICING.API_CALL,
        "uncached_input": (usage.input_tokens - usage.cached_input_tokens) * PRICING.AI_INPUT_TOKEN,
        "cached_input": usage.cached_input_tokens * PRICING.AI_CACHED_INPUT_TOKEN,
        # Output already includes reasoning: do not add reasoning again.
        "output_including_reasoning": usage.output_tokens * PRICING.AI_OUTPUT_TOKEN,
    }
    total = sum(parts.values())
    return {"currency": "USD", "unit": "micro_usd", "pricing_version": PRICING_VERSION,
            "breakdown_micro_usd": parts, "total_micro_usd": total, "total_usd": usd_string(total)}
