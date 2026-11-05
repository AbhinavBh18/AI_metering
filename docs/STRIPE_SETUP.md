# Stripe test-mode completion guide

This is the remaining external acceptance step. Local tests use a fake Stripe boundary; they do not prove that a hosted checkout was completed. No real payments are supported.

1. Create or sign in to a Stripe test account/sandbox. Stay in test mode. Do not enter live-mode credentials or real card data.
2. In Stripe's test dashboard, create a product named **Metering Pro** and a recurring monthly USD price of **$19.00** (quantity 1, interval count 1). Copy its `price_...` ID.
3. Copy `.env.example` to `.env`. Set `STRIPE_SECRET_KEY` to your `sk_test_...` key and `STRIPE_PRO_PRICE_ID` to that test price ID. Never commit `.env`.
4. Install the [Stripe CLI](https://docs.stripe.com/stripe-cli), then run:

```text
stripe login
stripe listen --events checkout.session.completed,customer.subscription.created,customer.subscription.updated,customer.subscription.deleted --forward-to localhost:8000/webhooks/stripe
```

5. Put the listener's `whsec_...` value in `STRIPE_WEBHOOK_SECRET`. Keep the listener running. Restart `python -m app run` after changing `.env`.
6. Run `python -m app seed`. Open `http://localhost:8000/docs`, click **Authorize**, and use the first tenant's `api_key` from the git-ignored `data/demo-credentials.json` file. Do not include the word `Bearer` in Swagger's token field.
7. Call `GET /usage`: it should show Free. Call `POST /billing/checkout`, open the returned URL, and use Stripe's test card `4242 4242 4242 4242`, a future expiry, and any valid test CVC. Do not use a real card.
8. After completion, allow a few seconds for the listener and worker. `GET /billing/jobs` should show a `done` job; `GET /usage` should show Pro, 10,000 calls, and 1,000,000 tokens. Save sanitized request/response excerpts in `EVIDENCE.md` (never API keys or signature secrets).
9. In the dashboard, cancel that test subscription and confirm a verified `customer.subscription.deleted` event returns the tenant to Free. A cancellation scheduled for period end keeps Pro until Stripe actually ends the subscription.
10. Replay an actual delivered event using Stripe's event resend workflow. For a registered test webhook endpoint, CLI syntax is `stripe events resend evt_... --webhook-endpoint=we_...`. For a CLI-only localhost setup, fetch the real event with `stripe events retrieve evt_...` and use `scripts/replay_stripe_event.py` below. A replay should return `duplicate: true` and create no new job.

## Local replay of a real retrieved event

```powershell
stripe events retrieve evt_REPLACE_WITH_REAL_ID | Set-Content -Encoding utf8 tmp/stripe-event.json
python scripts/replay_stripe_event.py tmp/stripe-event.json
```

This helper signs the retrieved event with your local listener secret, explicitly for local replay. It demonstrates application deduplication, not a second Stripe delivery. The real hosted checkout and original CLI delivery must still be captured separately. `stripe trigger` creates fixtures with unrelated customers; they will be ignored unless they reference a customer created by this application.

## Diagnose without exposing secrets

- 503 `stripe_not_configured`: add the test key and price ID, then restart.
- 503 `invalid_pro_price`: the price must be active, test mode, USD 1900 cents per month.
- 400 `invalid_webhook`: use the secret from the current CLI listener, preserve the raw body, and check the machine clock.
- 409 `subscription_exists`: this customer's Stripe account already has a nonterminal subscription; manage it in the test dashboard.
- Free after checkout: confirm the CLI is forwarding, inspect `/billing/jobs`, and keep `python -m app run` running. Redirects never change plans.
- Failed worker job: fix configuration/connectivity, then run `python -m app requeue JOB_ID`. The alert stays as audit history.

## Official references

- [Checkout Session creation](https://docs.stripe.com/api/checkout/sessions/create)
- [Webhook signatures, duplicates, and event ordering](https://docs.stripe.com/webhooks)
- [Listing current subscriptions](https://docs.stripe.com/api/subscriptions/list)
- [Stripe test cards](https://docs.stripe.com/testing)

REST API version is pinned in `app/stripe_gateway.py` to `2026-10-28.endive`.
