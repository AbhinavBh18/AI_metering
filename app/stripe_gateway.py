"""Small, injectable Stripe REST adapter. No secret or upstream body is logged."""
import httpx
from app.errors import DomainError

STRIPE_API_VERSION = "2026-10-28.endive"


class StripeGateway:
    def __init__(self, settings):
        self.settings = settings

    def require_config(self):
        if not self.settings.stripe_secret_key or not self.settings.stripe_pro_price_id:
            raise DomainError(503, "stripe_not_configured", "Set the Stripe test key and Pro price in .env.")

    def request(self, method, path, data=None, key=None, params=None):
        self.require_config()
        headers = {"Authorization": f"Bearer {self.settings.stripe_secret_key}", "Stripe-Version": STRIPE_API_VERSION}
        if key:
            headers["Idempotency-Key"] = key
        try:
            with httpx.Client(timeout=15) as client:
                response = client.request(method, "https://api.stripe.com/v1" + path, headers=headers, data=data, params=params)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise DomainError(502, "stripe_unavailable", "Stripe could not complete this test-mode operation. Retry safely.") from None
        if result.get("livemode") is True:
            raise DomainError(502, "live_mode_rejected", "Live-mode objects are forbidden.")
        return result

    def create_customer(self, tenant_id):
        return self.request("POST", "/customers", {"metadata[tenant_id]": tenant_id}, key=f"customer:{tenant_id}")

    def create_checkout(self, customer_id, tenant_id, key):
        price = self.request("GET", f"/prices/{self.settings.stripe_pro_price_id}")
        if (price.get("livemode") is not False or price.get("currency") != "usd"
                or price.get("unit_amount") != 1900 or not price.get("active")
                or price.get("recurring", {}).get("interval") != "month"
                or price.get("recurring", {}).get("interval_count") != 1):
            raise DomainError(503, "invalid_pro_price", "Pro requires an active test-mode $19.00 USD monthly price.")
        return self.request("POST", "/checkout/sessions", {
            "mode": "subscription", "customer": customer_id,
            "line_items[0][price]": self.settings.stripe_pro_price_id,
            "line_items[0][quantity]": "1", "client_reference_id": tenant_id,
            "metadata[tenant_id]": tenant_id, "subscription_data[metadata][tenant_id]": tenant_id,
            "success_url": self.settings.public_base_url + "/billing/return?result=success",
            "cancel_url": self.settings.public_base_url + "/billing/return?result=cancelled",
        }, key=key)

    def retrieve_checkout(self, session_id):
        return self.request("GET", f"/checkout/sessions/{session_id}")

    def subscriptions(self, customer_id):
        items, cursor = [], None
        while True:
            params = {"customer": customer_id, "status": "all", "limit": 100}
            if cursor:
                params["starting_after"] = cursor
            page = self.request("GET", "/subscriptions", params=params)
            items.extend(page["data"])
            if not page.get("has_more"):
                return items
            if not page["data"]:
                raise DomainError(502, "stripe_invalid_page", "Stripe returned an incomplete subscription list.")
            cursor = page["data"][-1]["id"]
