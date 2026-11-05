import json
from urllib.parse import parse_qs
import httpx
import pytest
from app.stripe_gateway import StripeGateway, STRIPE_API_VERSION
from app.errors import DomainError
from config import Settings


def gateway_with_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr("app.stripe_gateway.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return StripeGateway(Settings(stripe_secret_key="sk_test_fixture", stripe_pro_price_id="price_pro"))


def test_checkout_rest_contract_and_test_price_validation(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["Stripe-Version"] == STRIPE_API_VERSION
        if request.url.path == "/v1/prices/price_pro":
            return httpx.Response(200, json={"id": "price_pro", "livemode": False, "active": True, "currency": "usd", "unit_amount": 1900, "recurring": {"interval": "month", "interval_count": 1}})
        form = parse_qs(request.content.decode())
        assert form["mode"] == ["subscription"]
        assert form["customer"] == ["cus_alpha"]
        assert form["subscription_data[metadata][tenant_id]"] == ["alpha"]
        assert form["line_items[0][price]"] == ["price_pro"]
        assert request.headers["Idempotency-Key"] == "checkout:alpha:1"
        return httpx.Response(200, json={"id": "cs_test_fixture", "livemode": False, "url": "https://checkout.stripe.com/fixture", "expires_at": 10000})
    gateway = gateway_with_transport(monkeypatch, handler)
    assert gateway.create_checkout("cus_alpha", "alpha", "checkout:alpha:1")["id"] == "cs_test_fixture"
    assert len(requests) == 2


def test_subscription_list_is_paginated(monkeypatch):
    def handler(request):
        assert request.url.params["status"] == "all"
        assert request.url.params["customer"] == "cus_alpha"
        if "starting_after" not in request.url.params:
            return httpx.Response(200, json={"data": [{"id": "sub_1"}], "has_more": True})
        assert request.url.params["starting_after"] == "sub_1"
        return httpx.Response(200, json={"data": [{"id": "sub_2"}], "has_more": False})
    assert [s["id"] for s in gateway_with_transport(monkeypatch, handler).subscriptions("cus_alpha")] == ["sub_1", "sub_2"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_upstream_errors_are_sanitized(monkeypatch, status):
    gateway = gateway_with_transport(monkeypatch, lambda req: httpx.Response(status, json={"error": "sensitive"}))
    with pytest.raises(DomainError) as error:
        gateway.subscriptions("cus_alpha")
    assert error.value.status == 502
    assert "sensitive" not in error.value.message


def test_unconfigured_stripe_returns_503():
    with pytest.raises(DomainError) as error:
        StripeGateway(Settings()).create_customer("alpha")
    assert error.value.status == 503


def test_live_response_is_rejected(monkeypatch):
    gateway = gateway_with_transport(monkeypatch, lambda req: httpx.Response(200, json={"livemode": True}))
    with pytest.raises(DomainError, match="Live-mode"):
        gateway.create_customer("alpha")
