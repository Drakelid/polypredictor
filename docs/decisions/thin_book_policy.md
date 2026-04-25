# Decision: Thin-Book Markets — Tag, Not Exclude

*Date: 2026-04-25*

## Context

PRD §11.4 raised an open question: when a market's top-of-book notional
liquidity is below ~$1k, do we **exclude** it from the dashboard or **tag**
it with a visible badge so the user can decide for themselves?

## Decision

**Tag, not exclude.**

Implementation already follows this rule:

* `is_thin_book(top_of_book_depth_usdc, threshold_usdc)` in
  [packages/model/src/model/liquidity.py](packages/model/src/model/liquidity.py)
  is the single source of truth for the threshold check.
* `services/api/src/api/markets.py` derives a per-market `thin_book` flag
  from the latest `market_quotes` bid/ask sizes against
  `settings.thin_book_top_level_depth_threshold_usdc` (default $1,000) and
  surfaces it on `/v1/markets` and `/v1/markets/{cid}/model`.
* The dashboard renders a `thin_book` badge on the market list and detail
  views so the user can see the warning without losing the market.

## Rationale

* **User judgement first.** Some users actively want to see thin markets —
  e.g. niche long-tail crypto markets that haven't attracted depth yet but
  still have real edge. Excluding them silently is paternalistic.
* **Edge alerts already gate themselves.** The Kelly-sizing path caps
  recommended size by the `kelly_cap_default` setting and the
  edge-suppression path drops alerts when resolution risk is elevated;
  thin liquidity gets a similar treatment via the adversarial-flow blend
  floor (low book depth + elevated flow → ensemble delta is attenuated).
  So a thin-book market already self-throttles its alerting; tagging it
  is purely informational.
* **Survivorship correctness.** The walk-forward backtest corpus
  intentionally includes low-volume markets (M6.1 exit criterion: corpus
  is survivorship-free). Excluding thin markets from the live UI but
  including them in the backtest would introduce a population mismatch
  between training-time and serve-time.

## Consequences

* Operators can still hide thin markets at render time by filtering on the
  `thin_book` badge in the dashboard layer if they want — but this is a UI
  preference, not a model contract.
* If a future incident shows that thin-book markets attract phantom-edge
  alerts at unacceptable rates, the policy switches to "tag + auto-suppress
  alerts" before "exclude entirely". The setting
  `thin_book_top_level_depth_threshold_usdc` remains the single tuning knob.
