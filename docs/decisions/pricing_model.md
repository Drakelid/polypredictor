# Decision: Pricing Model

*Date: 2026‑04‑27 · Decided: 2026‑04‑27*

## Decision

**Single flat tier at $49/month** (annual option: $39/month billed yearly).

This is within the $30–$80 range identified in PRD §11.1 and balances
accessibility with the cost of live data sources (~$130/month: X Basic API
$100 + Glassnode free + CME FedWatch $30 marginal) plus infrastructure
overhead.

## Rationale

1. **Simplicity** — A single tier eliminates plan confusion. Beta users can
   convert with zero upsell friction.

2. **Sustainable at low volume** — At 50 paying subscribers, monthly revenue
   ($2,450) covers data + Render hosting (~$200) with margin for growth.

3. **Competitive positioning** — Comparable crypto signal tools (Coinalyze
   Pro, Token Metrics) charge $39–$99/month. $49 sits comfortably in the
   midrange with a stronger model transparency story.

4. **Annual incentive** — The 20% annual discount reduces churn and front-
   loads cash for data contract commitments.

## What is included

* Full model probability + conformal bands for all tracked markets
* Signal feed (whale flow, arb, large prints, book shocks)
* Journal + calibration tracking
* Model tuning (Conservative / Balanced / Aggressive + custom)
* Polymarket wallet sync (read-only) & auto-fill journal

## What is not included in v1

* White-label / API access — v2 roadmap
* Team seats — v2 roadmap

## Payment implementation

* Stripe Checkout — operator provides `STRIPE_SECRET_KEY` + `STRIPE_PRICE_ID`
* Fallback mock session when Stripe keys are absent (dev / staging)
* Subscription state tracked in `billing_subscriptions` Postgres table
* Webhook endpoint (`/v1/billing/webhook`) updates subscription status on
  `customer.subscription.updated` / `invoice.payment_succeeded` events