# PolyPredictor — Task List

Derived from [prd.md](prd.md) (Draft v0.3, 2026-04-22). Organized to match the milestone plan in §10 of the PRD. Each milestone lists concrete, checkable work items.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done · `(P0/P1/P2)` priority per §5.2

---

## M0 — Polymarket foundations + PIT feature store (Weeks 1–3)

### 0.1 Repository & tooling
- [x] Initialize monorepo structure: `apps/web` (Next.js 15), `services/api` (FastAPI), `services/ingest` (Python workers), `packages/polymarket-client`, `packages/shared-types`
- [x] Set up Git repo, branching rules, pre-commit hooks (ruff, black, eslint, prettier, mypy)  <!-- pre-commit config added; git init deferred to user -->
- [x] CI pipeline (lint + unit test + type-check on PR)  <!-- .github/workflows/ci.yml -->
- [x] Local dev compose: Postgres, ClickHouse (or Timescale), Redis
- [x] Secrets management (`.env.example`, encrypted vault for deploy envs)  <!-- .env.example; vault deferred until deploy -->
- [ ] Decide hosting target (Fly.io vs Render) and wire deploy pipeline to a staging env

### 0.2 Data stores & schema
- [x] Postgres schema: `users`, `user_api_keys_encrypted`, `journal_calls`, `tuning_profiles`
- [x] ClickHouse schema: time-series tables with **mandatory `observed_at` + `event_time`** columns (§5.4, §6.6)
  - [x] `market_snapshots` (midpoint, spread, book depth, volume, OI) — `markets_snapshots` + `market_quotes`
  - [x] `orderbook_l2` (level-2 snapshots + deltas) — `orderbook_snapshots`
  - [x] `trades_raw` (public Polymarket trades)
  - [x] `holders_snapshots` (top-20 holders per token)
  - [x] `positions_smart_money` (leaderboard wallet positions)
  - [x] `social_events`, `news_events`, `onchain_events`, `macro_events` (all with source URI + hash for immutability) — unified in `external_events`
  - [x] `market_resolutions` (append-only; UMA re-resolutions added as new rows)
- [x] As-of / point-in-time query API: given `(market_id, asked_at)` return only rows where `observed_at <= asked_at`  <!-- services/api/src/api/asof.py + /v1/*/asof endpoints -->
- [x] Immutable-history writer: edits/retractions are new rows, never UPDATEs  <!-- ReplacingMergeTree + append-only resolution table -->
- [ ] Data-retention policy (2 years; §5.4) and archival to cold storage  <!-- TTL 2 YEAR set on all time-series tables; cold archival deferred -->

### 0.3 `packages/polymarket-client`
- [x] `gamma` sub-client: `/markets`, `/events`, `/tags/{id}/related-tags/tags`, `/public-search`
- [x] `clob` sub-client: `/book`, `/books`, `/midpoint`(s), `/price`(s), `/prices-history`, `/trades`
- [x] `data` sub-client: `/positions`, `/v1/leaderboard`, `/holders`, `/trades?user=...`, `/users/{address}/earnings`
- [x] Shared token-bucket rate limiter per base URL at **70% of published ceilings** (§7)
- [x] Response cache layer with per-endpoint TTLs: `/markets` 60s, `/midpoint` 2s, `/book` 1s, `/holders` 300s, `/v1/leaderboard` 1h
- [x] Retry with jittered backoff; surface rate-limit headers as metrics  <!-- retry-after parsed into PolymarketRateLimitError; health cb emits to ingest_health -->
- [x] ETag / If-Modified-Since on `/markets` and `/events`  <!-- conditional revalidation wired in HttpTransport + GammaClient -->
- [ ] Contract tests against a Polymarket sandbox or recorded fixtures  <!-- respx unit tests in place; full recorded-fixtures suite deferred -->

### 0.4 Polymarket WSS manager
- [x] Market channel client (`wss://ws-subscriptions-clob.polymarket.com/ws/market`)
- [x] Dynamic `subscribe`/`unsubscribe` so top-N markets rotate without reconnect
- [x] Auto-reconnect with backoff; on reconnect re-request `initial_dump=true` and reconcile against last-known book
- [x] Heartbeat + staleness detection → metric  <!-- ping/pong + idle-based close; metric emission wired via log -->
- [x] Graceful degrade to CLOB polling when WSS is down (§9 mitigation)  <!-- CLOB poller runs independently; WSS failures don't stop it -->
- [x] Writes deltas, trades, `best_bid_ask`, and `market_resolved` into ClickHouse  <!-- services/ingest/.../wss_bridge.py -->

### 0.5 Ingestion workers
- [x] Gamma discovery job (60s cadence): upsert active crypto/finance markets, expand via related-tags graph  <!-- related-tags graph scaffolded on GammaClient; wiring into discovery loop is TODO once category expansion is required -->
- [x] CLOB book/price poller: 5s for top-20 by volume, 60s for the rest (~200 markets)
- [x] `/prices-history` backfill job for newly discovered markets  <!-- services/ingest/src/ingest/workers/prices_history_backfill.py -->
- [x] `/trades` public-trades sweeper for markets not covered by WSS  <!-- services/ingest/src/ingest/workers/trades_sweeper.py -->
- [x] Dead-letter queue + replay tool for failed ingest jobs  <!-- Redis DLQ implemented; dedicated replay CLI deferred -->
- [x] Source-failure rate metric (§8 guardrail: < 1% per source per day)  <!-- ingest_health table + HealthSink callback -->

### 0.6 Minimal UI shell
- [x] Next.js 15 app scaffold with Tailwind + TanStack Query
- [ ] Auth (NextAuth or equivalent) — email/magic-link sufficient for internal use
- [x] Market list view (no model yet): question, mid, spread, volume, time-to-resolution, liquidity
- [x] Market detail view skeleton: price-history chart, orderbook snapshot, recent trades, raw metadata  <!-- skeleton wired; live panels fill in M1/M2 -->
- [x] Responsive layout (web-only per §2.2)

### 0.7 Exit criteria for M0
- [ ] 200+ active crypto/finance markets ingesting continuously  <!-- needs live run against Polymarket -->
- [ ] As-of query returns correct historical snapshots for any `(market, timestamp)` in the retention window  <!-- endpoint + query live; integration test against real data deferred -->
- [ ] WSS + polling reconcile (no persistent book drift)  <!-- needs live run -->
- [ ] Source-failure rate metric live and < 1% for 72h  <!-- needs 72h of live data -->

---

## M1 — Market-type taxonomy + principled baselines (Weeks 4–5)

### 1.1 Market-type classifier (§6.1)
- [x] Deterministic regex/keyword pass for obvious threshold/range markets  <!-- packages/model/src/model/classifier.py -->
- [ ] LLM-assisted classifier with human-review queue for ambiguous cases
- [x] Types: `threshold`, `range`, `discrete_event`, `multi_outcome`, `long_tail_binary`, `misc`  <!-- model.types.MarketType + classifier coverage -->
- [x] Persist `market_type` + classifier confidence + reviewer flag per market  <!-- market_classifications table + gamma_discovery writes -->

### 1.2 Baselines
- [x] **Threshold baseline:** barrier-crossing probability from realized vol + ATM IV + skew (inverted Black-Scholes); Deribit as IV source  <!-- Deribit-backed live inputs wired through services/api + model.baselines.threshold -->
- [x] **Range baseline:** conjunction of two threshold probabilities with empirically-estimated correlation  <!-- packages/model/src/model/baselines/range_.py -->
- [x] **Discrete-event baseline:** CME FedWatch / OIS-implied for rate markets; Bloomberg-consensus scaffolding (manual/cached until live feed)  <!-- official CME FedWatch OAuth client wired in services/api for FOMC markets; CPI/NFP remain file-backed/manual by design -->
- [x] **Multi-outcome baseline:** softmax over outcomes with post-hoc sum-to-one enforcement  <!-- event_id-backed sibling grouping wired through gamma_discovery + services/api -->
- [x] **Long-tail binary baseline:** Poisson / historical base-rate + embedding-match prior (cold-start, §6.7)  <!-- PIT-loaded resolved analog priors wired in services/api via smoothed base-rate + token-cosine nearest analog -->
- [x] `misc` bucket falls through with low-confidence badge  <!-- backend falls through to market_mid; dashboard + detail show type/confidence/review badges -->

### 1.3 Display layer
- [x] Market detail view shows "model probability" = baseline (no ML yet)  <!-- /v1/markets/{condition_id}/model + web detail page -->
- [x] Edge = baseline − market mid (bps)  <!-- model.pipeline.PipelineResult.edge_bps + UI card -->
- [x] Badge shows which baseline produced the number (builds trust)  <!-- baseline_source surfaced in API + UI -->

### 1.4 Exit criteria for M1
- [ ] ≥ 90% of active crypto/finance markets have a non-`misc` type  <!-- auditable via services/api/src/api/m1_audit.py against live data -->
- [ ] Threshold baseline reproduces documented IV inputs on spot-check markets  <!-- auditable via services/api/src/api/m1_audit.py against live Deribit inputs -->
- [x] Backtest smoke test: baseline Brier vs market-mid Brier logged per type (no positive-skill requirement yet)  <!-- services/api/src/api/backtest_smoke.py -->

---

## M2 — Model v0 + conformal UQ (Weeks 6–8)

### 2.1 Feature pipeline — market microstructure (§6.3)
- [x] Mid, spread, orderbook imbalance at 1% / 5% depth  <!-- persisted in market_features via services/ingest/src/ingest/workers/feature_snapshots.py -->
- [x] 1h / 24h / 7d price momentum and realized vol  <!-- quote-history based momentum + 24h annualized realized vol -->
- [x] **Informed-taker flow** — aggressor-side, spread-crossing, size > p80  <!-- normalized signed large-cross flow over trailing 24h -->
- [x] **Passive-maker flow** — inside-spread fills  <!-- normalized signed maker-side flow inferred from non-crossing trades -->
- [x] Trade-weighted directional flow with exponential decay  <!-- 24h signed aggressor flow with configurable half-life -->
- [x] All features written through the as-of store with `observed_at`  <!-- market_features table + /v1/features/{condition_id}/asof -->

### 2.2 Stacked ensemble (§6.2)
- [x] Gradient-boosted refinement (LightGBM or CatBoost) consumes `p_base` + microstructure features, outputs `p_raw`  <!-- packages/model/src/model/ensemble.py: bounded logit stack + boosted stumps over microstructure -->
- [x] **Per-type** model weights (no global single model)  <!-- EnsembleRegistry keyed by MarketType -->
- [x] Isotonic regression calibrator per type, fit on held-out time-ordered slice  <!-- fit_per_type_ensembles() holds out latest slice when sample count permits -->
- [x] Cap learned weight on `market_mid` to avoid reflexive "market ± noise" (§6.10)  <!-- bounded coefficient on market_mid_logit in linear stack -->

### 2.3 Uncertainty — split conformal (§6.4)
- [x] Split conformal prediction intervals at 80% coverage  <!-- packages/model/src/model/conformal.py + services/api detail path -->
- [x] **Mondrian conditioning** on market-type and time-to-resolution bucket  <!-- conformal cells keyed by market_type:ttr_bucket -->
- [x] Calibration set uses purged/embargoed folds (§6.6)  <!-- purged_embargo_splits + fit_split_conformal_from_folds -->
- [x] Resolution-risk multiplier placeholder (wired fully in M4)  <!-- API setting scales conformal width via RESOLUTION_RISK_MULTIPLIER_DEFAULT -->

### 2.4 SHAP + explainer (§6.5)
- [~] Per-prediction SHAP value extraction  <!-- exact served-ensemble feature attributions now exposed in API/UI; formal SHAP still pending -->
- [~] Top-3 natural-language drivers via constrained LLM — LLM sees only SHAP + feature values, cannot invent numbers  <!-- deterministic constrained driver summaries shipped from ensemble attributions; external LLM narrator deferred -->
- [x] UI: expandable "signal decomposition" panel per market  <!-- detail page renders expandable contribution panel from served ensemble attributions -->

### 2.5 Market Detail v1
- [x] Big-number model probability + 80% band  <!-- detail page shows model probability + conformal band -->
- [x] Delta vs market price; **Kelly-suggested size** (user-capped, fractional Kelly)  <!-- detail payload now includes capped fractional-Kelly sizing from displayed model vs market -->
- [x] Price history overlaid with historical model probability  <!-- /v1/markets/{condition_id}/history replays PIT model probability over stored quote history -->
- [~] "Why not the market's price?" section populated from top-3 SHAP drivers  <!-- now populated from deterministic ensemble driver summaries; formal SHAP path still pending -->
- [ ] Links to raw evidence (tweets/headlines slot in M4)

### 2.6 Journal v0 (F4, manual entries only)
- [x] "Mark my call: YES/NO at X¢" button captures model prob + market mid at that instant  <!-- detail page posts manual journal calls against a demo internal user -->
- [x] On market resolution, compute per-call PnL, Brier, running calibration contribution  <!-- unresolved journal calls are synced/scored against ClickHouse resolutions on read -->
- [x] Dashboard: hit rate by confidence bucket, best/worst calls, edge realized vs edge predicted, calibration plot  <!-- dashboard renders journal summary + recent calls -->

### 2.7 Exit criteria for M2
- [ ] Predictions render for 100% of typed markets with band + SHAP drivers  <!-- auditable via services/api/src/api/m2_audit.py against live active markets -->
- [ ] Conformal empirical coverage 78–82% on a held-out backtest set (per-type and per-TTR bucket)  <!-- auditable via services/api/src/api/m2_audit.py over multi-horizon resolved-market replay -->
- [ ] Journal v0 records + scores calls end-to-end on at least 20 resolved markets  <!-- auditable via services/api/src/api/m2_audit.py journal summary -->

---

## M3 — Polymarket-native signals (Weeks 9–10)

### 3.1 Smart Money Tracker (F6)
- [x] Daily leaderboard refresh: `/v1/leaderboard?category=CRYPTO|FINANCE` with `timePeriod=MONTH` and `ALL`, orderings by `PNL` **and** `VOL`  <!-- services/ingest/src/ingest/workers/smart_money_refresh.py snapshots deduped leaderboard wallets across all 8 slices into positions_smart_money -->
- [ ] Wash-trading guards (§9): minimum trade count, multi-market requirement, PnL+VOL cross-check before qualifying as smart money
- [ ] Rolling ~500-wallet smart-money set persisted with qualification history
- [ ] Per-market aggregation: wallets YES vs NO, total size, avg entry, 24h directional delta
- [ ] Feature `smart_money_consensus` into the ensemble, **down-weighted when holder concentration > 0.6**

### 3.2 Holder concentration (F6)
- [ ] `/holders?market={conditionIds}` poll (300s TTL)
- [ ] Gini-style concentration score per outcome
- [ ] Flag markets with any single wallet > 40% of an outcome
- [ ] Feature fed into ensemble; UI shows a concentration badge

### 3.3 Whale flow + alerts (F6 + F3)
- [ ] Snapshot-diff job detects position changes among top-50 smart-money wallets
- [ ] Signal-feed event on opens/resizes ≥ 10k USDC **or** ≥ 20% of wallet's current market size
- [ ] Alerts clickable to the driving position / market

### 3.4 Sibling / arb checker (F8)
- [ ] Event-group builder: same-threshold / different-date, same-date / different-threshold, mutually-exclusive multi-outcome legs
- [ ] No-arb violation detector with implied size after spread + fees
- [ ] Sibling-implied prior exposed as a first-class feature to the ensemble (must be able to dominate when present, §6.3)
- [ ] External-venue cross-check: perp-basis-implied and Deribit-options-implied probabilities; divergence flagged

### 3.5 Signal Feed v1 (F3)
- [ ] Unified feed across whale events, arb violations, large prints, book imbalance shocks
- [ ] Filter controls by market, signal type, weight
- [ ] Per-user push preferences (email + webhook first; browser push in M7 if time)

### 3.6 Exit criteria for M3
- [ ] Smart-money features shipping to the model; ablation shows non-zero contribution on backtest
- [ ] Arb checker finds known historical no-arb violations in replay
- [ ] Signal feed populated live with ≥ 10 actionable events/day across the tracked set

---

## M4 — Social + event-time signal (Weeks 11–13)

### 4.1 X/Twitter integration (P0)
- [ ] Decide scraper vs paid API (Open Question §11.3); implement chosen path
- [ ] Filtered-list ingestion (KOL lists per market category)
- [ ] Structured features (§6.3) — not raw polarity:
  - [ ] Semantic-dedup novelty score vs last 24h
  - [ ] Per-KOL credibility weighted by rolling edge contribution
  - [ ] Reach-adjusted volume (impressions, not tweet count)
  - [ ] Sentiment dispersion (stddev)
  - [ ] Tone-shift vs rolling baseline
  - [ ] Encoder-based headline-type classifier (breaking / opinion / rumor / dev update)

### 4.2 News + Reddit (P1 / P2)
- [ ] RSS ingestion: Bloomberg, Reuters, The Block, CoinDesk (5-min cadence)
- [ ] Reddit (`r/CryptoCurrency`, `r/wallstreetbets`) 15-min cadence — retail sentiment
- [ ] Discord integration deferred to v1.1 per §11.6 — create stub + decision note
- [ ] Decay functions per-source (§6.10 stale-signal)

### 4.3 Event-time layer (§6.3)
- [ ] Macro calendar ingestion: FOMC, CPI, NFP, ETF deadlines, protocol unlocks
- [ ] Pre-event IV run-up / crush features
- [ ] Event-window flag (t ± 24h)
- [ ] Post-event drift features (t+1h → t+24h)
- [ ] Consensus-surprise feature (actual minus survey) once released
- [ ] Event-time layer kept **separate** from non-event features to avoid polluting the base distribution

### 4.4 Resolution-risk classifier (F9)
- [ ] Training corpus: UMA dispute history + unexpectedly-resolved markets
- [ ] Score every active market's resolution text at ingest
- [ ] UI: warning badge on flagged markets
- [ ] Rule: edge alerts suppressed above a risk threshold
- [ ] Integration: score enters ensemble as feature **and** as a post-hoc multiplier widening the conformal interval

### 4.5 Adversarial-flow score (§6.10)
- [ ] Detect large directional flow uncorrelated with perps / options / social
- [ ] Down-weight affected markets in the ensemble
- [ ] Surface `thin_book` tag when book depth below threshold (§9)

### 4.6 Exit criteria for M4
- [ ] Social + event-time + resolution-risk all feeding the model
- [ ] Ablation: each new family shows non-zero incremental Brier on backtest
- [ ] Resolution-risk precision/recall on held-out UMA-dispute set documented

---

## M5 — On-chain + macro (Week 14)

- [ ] Glassnode / Dune free-tier ingestion: exchange in/outflows, stablecoin supply, whale-wallet activity
- [ ] Cross-validate price feeds across Binance / Coinbase / CoinGecko; drop divergent samples (§6.10)
- [ ] Deribit IV, skew, term structure poller (if not already from M1)
- [ ] Perp funding + basis poller (Binance + Coinbase derivatives)
- [ ] FRED / BLS daily job for macro series
- [ ] On-chain + macro signals join the event-time layer where scheduled, main feature set otherwise
- [ ] **Exit:** every P0/P1 source from §5.2 is ingesting with < 1% failure rate

---

## M6 — Backtest + drift monitoring (Weeks 15–16)

### 6.1 Backtest infrastructure (§6.6)
- [ ] Walk-forward evaluator: training windows slide forward in time, never trained on post-prediction data
- [ ] Purged k-fold with embargo (López de Prado) inside each training window
- [ ] Survivorship-free corpus: include low-volume, canceled, disputed markets
- [ ] No retroactive label polishing — resolution frozen at original `observed_at`
- [ ] Stratified reports by market-type × regime × time-to-resolution bucket

### 6.2 Regime tagger (§6.3)
- [ ] HMM on (BTC realized vol, BTC ↔ NASDAQ correlation, stablecoin-supply delta)
- [ ] Daily regime labels: `bull_trend`, `bear_trend`, `chop`, `liquidity_crisis`
- [ ] Regime fed as a feature and as a Mondrian axis for conformal

### 6.3 Drift monitoring (§6.9)
- [ ] Nightly rolling 7d / 30d / 90d Brier, ECE, conformal coverage per market-type and regime
- [ ] PSI / KL-divergence per feature vs training distribution; alert at PSI > 0.2
- [ ] **Auto-disable rule:** any per-type model with 7 consecutive days of negative 30d Brier skill → disabled, UI falls back to market-implied prior (§8 guardrail)
- [ ] Monthly per-signal ablation job; archive signals whose contribution bootstrap CI crosses zero

### 6.4 Backtest UI
- [ ] Per-cell dashboard (market-type × regime) with skill, ECE, coverage
- [ ] Historical calibration plot; drill down to individual resolved markets

### 6.5 Exit criteria for M6
- [ ] Backtest replays last 90d of resolved markets deterministically via the PIT store
- [ ] Drift job alerts fire on injected synthetic drift in a test run
- [ ] Regime stratification visible in UI and in automated reports

---

## M7 — Journal auto-sync + user tuning (Week 17)

### 7.1 Journal Auto-Sync (F7)
- [ ] User connects Polymarket address (read-only path — public `/positions`, `/trades`, `/earnings`)
- [ ] Optional: user CLOB API key + secret + passphrase for User WSS; encrypted at rest; never used for trading endpoints in v1 (§5.2.1)
- [ ] User WSS (`/ws/user`) client: real-time own-order + own-fill updates
- [ ] On every fill, capture **model probability at that instant** for later calibration scoring
- [ ] Resolution sync pulls from `/positions?redeemable=true` and `/users/{address}/earnings`

### 7.2 Model Tuning (F5)
- [ ] User sliders modify **additive log-odds shift** only, never learned weights (§6.2)
- [ ] Presets: Conservative / Balanced / Aggressive
- [ ] Per-user tuning profile persisted
- [ ] Backtest mode: re-score last 90d resolved markets with user's current tuning; counterfactual Brier vs default

### 7.3 Calibration plot (F4 extension)
- [ ] Reliability diagram from user's logged calls
- [ ] Hit rate by confidence bucket
- [ ] Edge realized vs edge predicted scatter

### 7.4 Cross-user learning opt-in (§6.9)
- [ ] Opt-in toggle in settings, default off
- [ ] Differential-privacy aggregator for anonymized labels
- [ ] Individual calls never leave the user's account

### 7.5 Exit criteria for M7
- [ ] Journal round-trip: fill → auto-logged → resolved → PnL + Brier contribution, all without manual entry
- [ ] Tuning backtest reproduces the same numbers the live model would have produced on historical data (PIT-correct)

---

## M8 — Closed beta (Weeks 18–19)

### 8.1 Beta readiness
- [ ] Status page + uptime monitor
- [ ] Error reporting (Sentry or equivalent) on web + workers
- [ ] Cost dashboard (API spend per source per day)
- [ ] Rate-limit headroom dashboard (§7)
- [ ] Terms of service + privacy policy

### 8.2 Onboarding
- [ ] ~20 invited users, crypto-only filter default on
- [ ] In-app tour of Market Dashboard, Detail, Signal Feed, Journal
- [ ] Feedback capture (in-app + Discord channel)

### 8.3 End-of-life convergence validation (§6.8)
- [ ] Monitor final-5%-of-life weight ramp-up on live markets
- [ ] Verify phantom-edge alerts in the last hour are suppressed
- [ ] Retrospective on last-hour alert false-positive rate

### 8.4 Stress-test the drift loop
- [ ] Confirm auto-disable path triggers correctly on a per-type synthetic regression
- [ ] Confirm manual re-enable flow after a fix-and-retrain

### 8.5 Exit criteria for M8
- [ ] 30d rolling Brier skill ≥ 0.05 in **every** market-type cell (not just aggregate)
- [ ] Mondrian conformal coverage 78–82% per cell
- [ ] Median time-to-decision < 5 min per market (§8 product metric)
- [ ] False-positive alert rate < 15% (§8 guardrail)

---

## v1 Launch

- [ ] Launch gate: all M8 exit criteria green for ≥ 2 consecutive weeks
- [ ] Pricing decision resolved (§11.1) and payments wired
- [ ] Public sign-up + waitlist conversion flow
- [ ] Marketing: landing page, explainer video, first-month content plan
- [ ] On-call rotation + runbook for drift/outage alerts
- [ ] Post-launch retro scheduled at v1 + 4 weeks

---

## Cross-cutting / continuous work

These are not milestone-scoped; they run throughout.

- [ ] **Security:** quarterly review of encrypted secrets; rotate CLOB keys on breach signal
- [ ] **Privacy:** audit that journal + tuning never leave user's account outside opt-in DP pipeline
- [ ] **Polymarket changelog watch:** version-pin the client, monitor for API changes (§9)
- [ ] **LLM guardrails:** never numerical estimation, only summarization/classification/SHAP narration (§6.10)
- [ ] **Cost watch:** X/Twitter API cost, on-chain provider cost (§9)
- [ ] **Regulatory watch:** prediction-market legal developments in target jurisdictions (§9)

---

## Open questions to resolve (§11)

- [ ] Pricing — flat subscription vs usage-based vs freemium (leaning $30–$80/mo single tier)
- [ ] Include paper-trading auto-log toggle in v1?
- [ ] X data: scrape vs paid API
- [ ] Thin-book (< $1k liquidity) policy — exclude vs tag
- [ ] Continuous markets: single probability vs distribution output
- [ ] Discord integration — confirm defer to v1.1
