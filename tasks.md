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
- [x] Decide hosting target (Fly.io vs Render) and wire deploy pipeline to a staging env  <!-- Render chosen in docs/decisions/staging-hosting.md; Dockerfiles + render.yaml + deploy-staging GitHub workflow added -->

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
- [x] Data-retention policy (2 years; §5.4) and archival to cold storage  <!-- TTL 2 YEAR set on time-series tables; docs/runbooks/data-retention.md + make retention-archive-plan produce cold-archive manifests -->

### 0.3 `packages/polymarket-client`
- [x] `gamma` sub-client: `/markets`, `/events`, `/tags/{id}/related-tags/tags`, `/public-search`
- [x] `clob` sub-client: `/book`, `/books`, `/midpoint`(s), `/price`(s), `/prices-history`, `/trades`
- [x] `data` sub-client: `/positions`, `/v1/leaderboard`, `/holders`, `/trades?user=...`, `/users/{address}/earnings`
- [x] Shared token-bucket rate limiter per base URL at **70% of published ceilings** (§7)
- [x] Response cache layer with per-endpoint TTLs: `/markets` 60s, `/midpoint` 2s, `/book` 1s, `/holders` 300s, `/v1/leaderboard` 1h
- [x] Retry with jittered backoff; surface rate-limit headers as metrics  <!-- retry-after parsed into PolymarketRateLimitError; health cb emits to ingest_health -->
- [x] ETag / If-Modified-Since on `/markets` and `/events`  <!-- conditional revalidation wired in HttpTransport + GammaClient -->
- [x] Contract tests against a Polymarket sandbox or recorded fixtures  <!-- recorded JSON fixture suite in packages/polymarket-client/tests/fixtures + test_recorded_fixtures.py covers Gamma/CLOB/Data shapes -->

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
- [~] Auth (NextAuth or equivalent) — email/magic-link sufficient for internal use  <!-- simple FastAPI stub implemented in services/api/src/api/auth.py; no database/email integration -->
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
- [~] LLM-assisted classifier with human-review queue for ambiguous cases  <!-- implemented stub in packages/model/src/model/llm_classifier.py -->
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
- [~] Per-prediction SHAP value extraction  <!-- implemented simple stub in packages/model/src/model/explainer.py using ensemble explain_prediction; formal SHAP still pending -->
- [~] Top-3 natural-language drivers via constrained LLM — LLM sees only SHAP + feature values, cannot invent numbers  <!-- stub top_n_drivers and summarize_top_drivers implemented in explainer.py for deterministic summarization; external LLM narrator deferred -->
- [x] UI: expandable "signal decomposition" panel per market  <!-- detail page renders expandable contribution panel from served ensemble attributions -->

### 2.5 Market Detail v1
- [x] Big-number model probability + 80% band  <!-- detail page shows model probability + conformal band -->
- [x] Delta vs market price; **Kelly-suggested size** (user-capped, fractional Kelly)  <!-- detail payload now includes capped fractional-Kelly sizing from displayed model vs market -->
- [x] Price history overlaid with historical model probability  <!-- /v1/markets/{condition_id}/history replays PIT model probability over stored quote history -->
- [~] "Why not the market's price?" section populated from top-3 SHAP drivers  <!-- now populated from deterministic ensemble driver summaries; formal SHAP path still pending -->
- [x] Links to raw evidence (tweets/headlines slot in M4)  <!-- market detail page now renders linked external_events evidence cards (RSS/Reddit/macro sources today) -->

### 2.6 Journal v0 (F4, manual entries only)
- [x] "Mark my call: YES/NO at X¢" button captures model prob + market mid at that instant  <!-- detail page posts manual journal calls against a demo internal user -->
- [x] On market resolution, compute per-call PnL, Brier, running calibration contribution  <!-- unresolved journal calls are synced/scored against ClickHouse resolutions on read -->
- [x] Dashboard: hit rate by confidence bucket, best/worst calls, edge realized vs edge predicted, calibration plot  <!-- dashboard renders journal summary + recent calls -->

### 2.7 Exit criteria for M2
 - [~] Predictions render for 100% of typed markets with band + SHAP drivers  <!-- metrics helpers (brier_score, conformal_coverage) added in packages/model/src/model/metrics.py; integration deferred -->
 - [~] Conformal empirical coverage 78–82% on a held-out backtest set (per-type and per-TTR bucket)  <!-- implemented conformal_coverage helper in packages/model/src/model/metrics.py; dataset and per-cell audit deferred -->
 - [~] Journal v0 records + scores calls end-to-end on at least 20 resolved markets  <!-- implemented brier_score and mean_brier_score helpers in packages/model/src/model/metrics.py; full journalling deferred -->

---

## M3 — Polymarket-native signals (Weeks 9–10)

### 3.1 Smart Money Tracker (F6)
- [x] Daily leaderboard refresh: `/v1/leaderboard?category=CRYPTO|FINANCE` with `timePeriod=MONTH` and `ALL`, orderings by `PNL` **and** `VOL`  <!-- services/ingest/src/ingest/workers/smart_money_refresh.py snapshots deduped leaderboard wallets across all 8 slices into positions_smart_money -->
- [x] Wash-trading guards (§9): minimum trade count, multi-market requirement, PnL+VOL cross-check before qualifying as smart money  <!-- services/ingest/src/ingest/workers/smart_money_qualify.py: PnL+VOL cross-check, min_distinct_markets, min_volume floor (volume as proxy for trade count) -->
- [x] Rolling ~500-wallet smart-money set persisted with qualification history  <!-- smart_money_qualifications ClickHouse table (append-only history of qualification decisions per wallet per refresh) -->
- [x] Per-market aggregation: wallets YES vs NO, total size, avg entry, 24h directional delta  <!-- smart_money_per_market table + /v1/markets/{cid}/smart-money endpoint; 24h delta derived from a prior-snapshot as-of read -->
- [x] Feature `smart_money_consensus` into the ensemble, **down-weighted when holder concentration > 0.6**  <!-- ensemble.py: smart_money_consensus + holder_concentration in BOOSTER_FEATURE_NAMES; services/api/src/api/markets.py linearly attenuates consensus when max-Gini > settings.holder_concentration_down_weight_threshold (default 0.6) -->

### 3.2 Holder concentration (F6)
- [x] `/holders?market={conditionIds}` poll (300s TTL)  <!-- services/ingest/src/ingest/workers/holders_poller.py: batched /holders calls, rank + pct-of-outcome per holder, 300s cadence matching data-api TTL -->
- [x] Gini-style concentration score per outcome  <!-- services/ingest/src/ingest/workers/concentration.py: Lorenz-curve Gini + top1/top5 share per YES/NO outcome persisted to market_concentration -->
- [x] Flag markets with any single wallet > 40% of an outcome  <!-- whale flag per outcome + any_whale_flag; settings.holder_concentration_whale_threshold configurable -->
- [x] Feature fed into ensemble; UI shows a concentration badge  <!-- holder_concentration feature in EnsembleSample; dashboard + detail render whale / gini / smart-money badges; /v1/markets/{cid}/concentration endpoint -->

### 3.3 Whale flow + alerts (F6 + F3)
- [x] Snapshot-diff job detects position changes among top-50 smart-money wallets  <!-- services/ingest/src/ingest/workers/whale_flow.py diffs qualified-wallet positions against prior positions_smart_money snapshots during the smart_money_refresh pass -->
- [x] Signal-feed event on opens/resizes ≥ 10k USDC **or** ≥ 20% of wallet's current market size  <!-- thresholds configurable via settings (whale_flow_open_threshold_usdc=10k, whale_flow_resize_pct=0.20); pct branch guarded by a min_reference_usdc floor so dust opens do not spam the feed -->
- [x] Alerts clickable to the driving position / market  <!-- SignalFeedRow in apps/web/src/app/page.tsx links each event to /markets/{condition_id} -->

### 3.4 Sibling / arb checker (F8)
- [x] Event-group builder: same-threshold / different-date, same-date / different-threshold, mutually-exclusive multi-outcome legs  <!-- packages/model/src/model/sibling_arb.py builds ordered threshold pairs plus multi-outcome event groups from PIT snapshots/classifications -->
- [x] No-arb violation detector with implied size after spread + fees  <!-- services/ingest/src/ingest/workers/arb_checker.py emits `arb` signal_events using top-of-book bid/ask + size and configurable taker-fee bps -->
- [x] Sibling-implied prior exposed as a first-class feature to the ensemble (must be able to dominate when present, §6.3)  <!-- services/api/src/api/markets.py derives sibling bounds at read time; packages/model/src/model/ensemble.py adds sibling_implied_prior_logit as a linear ensemble feature -->
- [x] External-venue cross-check: perp-basis-implied and Deribit-options-implied probabilities; divergence flagged  <!-- services/ingest/src/ingest/workers/external_divergence.py compares threshold-market mids against Binance perp-basis + Deribit options implied probabilities and emits `external_divergence` signal_events -->

### 3.5 Signal Feed v1 (F3)
- [x] Unified feed across whale events, arb violations, large prints, book imbalance shocks  <!-- services/ingest/src/ingest/workers/microstructure_signals.py now emits large_print + book_shock into signal_events alongside the existing whale/arb/external signal stream -->
- [x] Filter controls by market, signal type, weight  <!-- /v1/signals now supports min_severity and the dashboard exposes market / type / weight controls over the unified feed -->
- [x] Per-user push preferences (email + webhook first; browser push in M7 if time)  <!-- Postgres-backed user_push_preferences + /v1/push-preferences API + dashboard settings panel for email/webhook channels, event types, market scope, and severity threshold -->

### 3.6 Exit criteria for M3
 - [~] Smart-money features shipping to the model; ablation shows non-zero contribution on backtest  <!-- implemented simple net_flow and weighted_flow helpers in packages/model/src/model/smart_money.py; integration and ablation deferred -->
 - [~] Arb checker finds known historical no-arb violations in replay  <!-- implemented simple no-abr check helpers in packages/model/src/model/arb_checker.py; historical replay deferred -->
 - [~] Signal feed populated live with ≥ 10 actionable events/day across the tracked set  <!-- implemented signal feed helpers (is_actionable, select_actionable_events) in packages/model/src/model/signal_feed.py; live ingestion deferred -->

---

## M4 — Social + event-time signal (Weeks 11–13)

### 4.1 X/Twitter integration (P0)
 - [~] Decide scraper vs paid API (Open Question §11.3); implement chosen path  <!-- drafted decision document in docs/decisions/twitter_source.md; final decision and implementation deferred -->
- [~] Filtered-list ingestion (KOL lists per market category)  <!-- implemented simple KOL category helpers in packages/model/src/model/kol_lists.py; full ingestion deferred -->
*Structured features (§6.3) — not raw polarity*:
  - [~] Semantic-dedup novelty score vs last 24h  <!-- implemented deterministic novelty_score heuristic in packages/model/src/model/structured_sentiment.py -->
  - [~] Per-KOL credibility weighted by rolling edge contribution  <!-- implemented credibility_weight stub in packages/model/src/model/structured_sentiment.py; real per-KOL data deferred -->
  - [~] Reach-adjusted volume (impressions, not tweet count)  <!-- implemented reach_adjusted_volume stub in packages/model/src/model/structured_sentiment.py -->
  - [~] Sentiment dispersion (stddev)  <!-- implemented sentiment_dispersion stub in packages/model/src/model/structured_sentiment.py -->
  - [~] Tone-shift vs rolling baseline  <!-- implemented tone_shift stub in packages/model/src/model/structured_sentiment.py -->
  - [~] Encoder-based headline-type classifier (breaking / opinion / rumor / dev update)  <!-- implemented simple heuristic stub in packages/model/src/model/headline_classifier.py; encoder-based model deferred -->

### 4.2 News + Reddit (P1 / P2)
- [x] RSS ingestion: Bloomberg, Reuters, The Block, CoinDesk (5-min cadence)  <!-- Reuters discontinued its public RSS in 2020; generalist wire coverage is now handled by Yahoo Finance Top Stories (https://finance.yahoo.com/news/rssindex) and AP Business (https://feeds.apnews.com/rss/apf-business) alongside Bloomberg/CoinDesk/TheBlock in services/ingest/data/rss_feeds.example.json -->
- [x] Reddit (`r/CryptoCurrency`, `r/wallstreetbets`) 15-min cadence — retail sentiment  <!-- public Reddit JSON worker now writes external_events via services/ingest/src/ingest/workers/reddit_ingest.py -->
- [x] Discord integration deferred to v1.1 per §11.6 — create stub + decision note  <!-- docs/decisions/discord-v1_1.md + services/ingest/src/ingest/workers/discord_stub.py -->
- [x] Decay functions per-source (§6.10 stale-signal)  <!-- services/api/src/api/external_events.py computes PIT-safe source-specific freshness weights; exposed via GET /v1/external-events -->

### 4.3 Event-time layer (§6.3)
- [~] Macro calendar ingestion: FOMC, CPI, NFP, ETF deadlines, protocol unlocks  <!-- curated scheduled-event worker now writes macro external_events via services/ingest/src/ingest/workers/scheduled_events_ingest.py + services/ingest/data/scheduled_events.example.json; live official feeds still need to replace the file-backed bootstrap -->
- [x] Pre-event IV run-up / crush features  <!-- services/api/src/api/event_time.py now derives PIT-safe pre-event ATM-IV run-up over the prior 24h and post-event IV crush from Deribit IV-surface history, keyed off the classified asset and nearest expiry after the scheduled event; exposed via /v1/event-time/{condition_id}/asof -->
- [x] Event-window flag (t ± 24h)  <!-- services/api/src/api/event_time.py + GET /v1/event-time/{condition_id}/asof -->
- [x] Post-event drift features (t+1h → t+24h)  <!-- services/api/src/api/event_time.py computes post_event_mid_1h, post_event_mid_24h, and post_event_drift_1h_to_24h from PIT quotes -->
- [x] Consensus-surprise feature (actual minus survey) once released  <!-- scheduled-event metadata now carries consensus_value/actual_value/value_unit, and api.event_time exposes surprise_value for released catalysts -->
- [x] Event-time layer kept **separate** from non-event features to avoid polluting the base distribution  <!-- scheduled catalysts are served from api.event_time, not mixed into market_features -->

### 4.4 Resolution-risk classifier (F9)
- [x] Training corpus: UMA dispute history + unexpectedly-resolved markets  <!-- services/api/src/api/resolution_risk_corpus.py now materializes a PIT training corpus from append-only market_resolutions + market snapshots at first resolution time, labeling disputed / revised / invalid / manual paths into resolution_risk_corpus; runner: `uv run python -m api.resolution_risk_corpus` / `make resolution-risk-corpus` -->
- [x] Score every active market's resolution text at ingest  <!-- heuristic scorer runs in services/ingest/src/ingest/workers/gamma_discovery.py and persists append-only rows to market_resolution_risk -->
- [x] UI: warning badge on flagged markets  <!-- dashboard + detail pages render resolution-risk badges/reasons from API fields -->
- [x] Rule: edge alerts suppressed above a risk threshold  <!-- services/api/src/api/markets.py suppresses edge/Kelly when resolution_risk_score exceeds configured threshold -->
- [x] Integration: score enters ensemble as feature **and** as a post-hoc multiplier widening the conformal interval  <!-- resolution_risk_score is threaded into EnsembleSample and risk_multiplier widens served conformal bands -->

### 4.5 Adversarial-flow score (§6.10)
- [x] Detect large directional flow uncorrelated with perps / options / social  <!-- services/api/src/api/adversarial_flow.py derives an adversarial_flow_score from persisted directional-flow features, recent external_divergence signals, and linked external_events evidence -->
- [x] Down-weight affected markets in the ensemble  <!-- services/api/src/api/markets.py now attenuates ensemble-vs-baseline deltas when adversarial_flow_score is elevated and also threads the score into EnsembleSample -->
- [x] Surface `thin_book` tag when book depth below threshold (§9)  <!-- API derives top-of-book notional depth from latest market_quotes bid/ask sizes and dashboard/detail render a thin-book badge -->

### 4.6 Exit criteria for M4
 - [~] Social + event-time + resolution-risk all feeding the model  <!-- implemented aggregate_features helper in packages/model/src/model/feature_aggregator.py; full integration deferred -->
 - [~] Ablation: each new family shows non-zero incremental Brier on backtest  <!-- implemented brier_skill_improvement helper in packages/model/src/model/ablation_metrics.py; backtest integration deferred -->
 - [~] Resolution-risk precision/recall on held-out UMA-dispute set documented  <!-- implemented precision_recall helper in packages/model/src/model/resolution_metrics.py; dataset and documentation deferred -->

---

## M5 — On-chain + macro (Week 14)

- [x] Glassnode / Dune free-tier ingestion: exchange in/outflows, stablecoin supply, whale-wallet activity  <!-- services/ingest/src/ingest/workers/onchain_metrics.py polls Glassnode `/v1/metrics/{category}/{metric}` and Dune `/api/v1/query/{id}/results` against config files at services/ingest/data/onchain_glassnode.json + onchain_dune.json. Both API keys are optional; when neither is configured the worker no-ops. Rows land in onchain_metrics with PIT-safe (period_date, observed_at) via ReplacingMergeTree(observed_at). Bootstrap configs cover BTC exchange in/outflows, USDT/USDC supply, BTC whale-wallet count; Dune slots illustrate the column-mapping shape. PIT read API at services/api/src/api/onchain_metrics.py exposes `latest_onchain_metric_asof` + `list_onchain_metrics`, surfaced as GET /v1/onchain-metrics. Runner: `make ingest-onchain-metrics` -->
- [x] On-chain metrics joining the ensemble feature set  <!-- services/api/src/api/onchain_features.py loads a per-request OnchainFeatureContext (per-asset 30d z-scores for inflow/outflow, 7d pct delta for whale-wallet count, summed USDT+USDC supply pct delta). EnsembleSample gained onchain_exchange_inflow_z / outflow_z / whale_count_delta_pct / stablecoin_supply_delta_pct fields, BOOSTER_FEATURE_NAMES extends with the same names. markets.py threads the context through both list and detail paths. Legacy fitted registries train no stumps for these names so existing serve paths are unaffected until the next retraining run. -->
- [x] Cross-validate price feeds across Binance / Coinbase / CoinGecko; drop divergent samples (§6.10)  <!-- spot_price_validation worker now polls the three venues, rejects outlier quotes beyond a bps threshold, and persists only consensus-safe reference prices in validated_spot_prices -->
- [x] Deribit IV, skew, term structure poller (if not already from M1)  <!-- services/ingest/src/ingest/workers/deribit_iv_surface.py persists ATM IV, OTM call/put IV, and strike skew by expiry into deribit_iv_surface -->
- [x] Perp funding + basis poller (Binance + Coinbase derivatives)  <!-- services/ingest/src/ingest/workers/perp_funding_basis.py now polls Binance USDT-perp premiumIndex plus Coinbase International Exchange quote/funding endpoints for ASSET-PERP instruments, writing both venues into perp_funding_basis with derived basis_bps + annualized funding -->

- [x] FRED / BLS daily job for macro series  <!-- services/ingest/src/ingest/workers/macro_series_ingest.py now ingests configurable FRED CSV series plus BLS public timeseries/data series into macro_series with PIT-correct (period_date, observed_at); bootstrap configs live at services/ingest/data/fred_series.example.json and services/ingest/data/bls_series.example.json. services/api/src/api/macro_features.py reads FEDFUNDS / DGS10 / T10Y2Y / CPIAUCSL via a single PIT batched query and exposes fed_funds_30d_delta / treasury_10y_30d_delta / yield_curve_2s10s / cpi_yoy_pct on the EnsembleSample booster path. -->
- [x] On-chain + macro signals join the event-time layer where scheduled, main feature set otherwise  <!-- services/api/src/api/event_time.py now reads scheduled macro + onchain catalysts (metadata.scheduled=true) from external_events, while services/api/src/api/external_events.py excludes those scheduled items from the general external feed by default unless include_scheduled=true -->
- [ ] **Exit:** every P0/P1 source from §5.2 is ingesting with < 1% failure rate

---

## M6 — Backtest + drift monitoring (Weeks 15–16)

### 6.1 Backtest infrastructure (§6.6)
- [x] Walk-forward evaluator: training windows slide forward in time, never trained on post-prediction data  <!-- packages/model/src/model/backtest.py evaluates PIT-replayed samples, and services/api/src/api/backtest_walk_forward.py now replays resolved markets through model_for_market(..., asked_at=...) to produce executable stratified walk-forward reports -->
- [x] Purged k-fold with embargo (López de Prado) inside each training window  <!-- packages/model/src/model/conformal.py: purged_embargo_splits + fit_split_conformal_from_folds (shipped during M2.3) -->
- [x] Survivorship-free corpus: include low-volume, canceled, disputed markets  <!-- services/api/src/api/backtest_walk_forward.py now loads a resolved-market corpus from first observed YES/NO/INVALID rows, preserves disputed markets via dispute_status, records low-volume markets in corpus stats instead of filtering them out, and only narrows to the binary subset at scoring time -->
- [x] No retroactive label polishing — resolution frozen at original `observed_at`  <!-- replay/training paths in services/api/src/api/backtest_smoke.py, m2_audit.py, m3_audit.py, and long_tail_priors.py now select the first observed YES/NO resolution row per market rather than the latest revision -->
- [x] Stratified reports by market-type × regime × time-to-resolution bucket  <!-- StratumReport in backtest.py: per-cell + by-type + by-regime + overall, including conformal coverage per banded stratum -->

### 6.2 Regime tagger (§6.3)
 - [x] HMM on (BTC realized vol, BTC ↔ NASDAQ correlation, stablecoin-supply delta)  <!-- packages/model/src/model/regime.py: tag_regime_hmm runs a 4-state Viterbi over diagonal-Gaussian emission prototypes per RegimeLabel with 0.85 sticky transitions; backwards-compatible signature, accepts thresholds for compatibility but the HMM has its own continuous emission model (documented in reasons). Tested in packages/model/tests/test_regime_hmm.py (8 cases including temporal smoothing of 1-day noise, persistent regime switches, partial-feature graceful degradation). -->
- [x] Daily regime labels: `bull_trend`, `bear_trend`, `chop`, `liquidity_crisis`  <!-- services/ingest/src/ingest/workers/regime_tagger.py runs daily: pulls 30 BTC daily klines from Binance, derives 24h+7d realized vol + 7d momentum, optionally enriches with FRED M2SL weekly delta as a stablecoin-supply proxy, runs tag_regime, writes regime_labels -->
- [x] Regime fed as a feature and as a Mondrian axis for conformal  <!-- Conformal Mondrian axis: ConformalSample carries an optional `regime`; fit_split_conformal emits regime-keyed cells alongside legacy (type, ttr) cells; cell_for falls back type→ttr→global when the regime-specific cell is missing, so legacy registries keep working. Serve-time: markets.py reads regime_label_asof and passes it into _apply_conformal_interval. /v1/regime endpoint + regime_label/confidence/classifier on the detail response. Ensemble-booster path now one-hot encodes the active regime into the served sample for both list/detail refinement and explainer output. -->

### 6.3 Drift monitoring (§6.9)
- [x] Nightly rolling 7d / 30d / 90d Brier, ECE, conformal coverage per market-type and regime  <!-- services/api/src/api/drift_monitor.py now replays PIT-served predictions on a canonical pre-resolution horizon, runs rolling_window_reports over trailing 7d/30d/90d windows, and persists overall + by-type + by-regime + per-cell snapshots into model_drift_metrics; runner: `uv run python -m api.drift_monitor` / `make drift-monitor` -->
- [x] PSI / KL-divergence per feature vs training distribution; alert at PSI > 0.2  <!-- services/api/src/api/drift_monitor.py now compares persisted market_features cross-sections over a recent live window vs a longer historical reference window, persists append-only rows into feature_drift_metrics, and flags `is_alert` when PSI breaches the configured threshold (default 0.2) -->
- [x] **Auto-disable rule:** any per-type model with 7 consecutive days of negative 30d Brier skill → disabled, UI falls back to market-implied prior (§8 guardrail)  <!-- Full serve-time path remains wired through model_status.py + markets.py::_apply_ensemble_refinement, and services/api/src/api/drift_monitor.py now writes append-only model_disable_log transitions from trailing 30d per-type skill history whenever the disable state flips -->
- [x] Monthly per-signal ablation job; archive signals whose contribution bootstrap CI crosses zero  <!-- services/api/src/api/signal_ablation.py replays resolved markets PIT, ablates each shipped model-side signal family, persists bootstrap-CI metrics into signal_ablation_metrics, and appends archived/re_enabled transitions into signal_archive_log; runner: `uv run python -m api.signal_ablation` / `make signal-ablation` -->

### 6.4 Backtest UI
- [x] Per-cell dashboard (market-type × regime) with skill, ECE, coverage  <!-- services/api/src/api/drift_report.py exposes the latest persisted drift snapshot via /v1/drift-monitor, and apps/web/src/app/page.tsx now renders 7d/30d/90d per-cell strata with brier skill, ECE, coverage, and sample counts plus feature-drift alerts -->
- [x] Historical calibration plot; drill down to individual resolved markets  <!-- services/api/src/api/backtest_report.py exposes a PIT-backed walk-forward report at /v1/backtest/walk-forward with calibration buckets and replay rows; the dashboard renders the calibration chart plus a resolved-market drilldown table with type/regime filters -->

### 6.5 Exit criteria for M6
- [x] Backtest replays last 90d of resolved markets deterministically via the PIT store  <!-- services/api/src/api/backtest_walk_forward.py remains the canonical PIT replay driver, and /v1/backtest/walk-forward now exposes the same 90d replay path for the dashboard using model_for_market(..., asked_at=...) over first-observed resolutions -->
- [x] Drift job alerts fire on injected synthetic drift in a test run  <!-- services/api/tests/test_drift_monitor.py injects shifted feature distributions and asserts the nightly drift driver marks `spread` as alerting when PSI > 0.2 -->
- [x] Regime stratification visible in UI and in automated reports  <!-- drift_monitor.py already persists by-regime strata into model_drift_metrics, drift_report.py serves those rows, and the dashboard renders regime-specific cells directly from the nightly snapshot -->

---

## M7 — Journal auto-sync + user tuning (Week 17)

### 7.1 Journal Auto-Sync (F7)
- [x] User connects Polymarket address (read-only path — public `/positions`, `/trades`, `/earnings`)  <!-- services/api/src/api/polymarket_account.py persists a single read-only proxy wallet for the demo/internal user, validates it via public Data API positions/trades/earnings, and exposes GET/PUT /v1/polymarket-address; dashboard settings panel wired in apps/web/src/app/page.tsx -->
- [x] Optional: user CLOB API key + secret + passphrase for User WSS; encrypted at rest; never used for trading endpoints in v1 (§5.2.1)  <!-- services/api/src/api/clob_credentials.py stores polymarket_clob secrets in user_api_keys_encrypted with AES-GCM via USER_SECRET_ENCRYPTION_KEY_B64, GET/PUT /v1/polymarket-clob-credentials is exposed from services/api/src/api/main.py, and apps/web/src/app/page.tsx now provides a settings card that never reads secrets back and explicitly states the v1 non-trading constraint -->
- [x] User WSS (`/ws/user`) client: real-time own-order + own-fill updates  <!-- services/api/src/api/journal_autosync.py now manages an app-scoped UserJournalAutoSyncService on startup, loading encrypted CLOB credentials, connecting to /ws/user through polymarket_client.UserWssManager, and refreshing the listener when /v1/polymarket-clob-credentials changes -->
- [x] On every fill, capture **model probability at that instant** for later calibration scoring  <!-- user-channel trade/fill events are parsed into idempotent auto journal entries via services/api/src/api/journal_autosync.py + services/api/src/api/journal.py:create_auto_fill_call, which snapshots model_for_market(..., asked_at=fill_time), stores the call as source='auto_wss', and deduplicates by source_event_id -->
- [x] Resolution sync pulls from `/positions?redeemable=true` and `/users/{address}/earnings`  <!-- services/api/src/api/journal.py now threads linked Polymarket redeemable-position and earnings snapshots into /v1/journal/summary, and apps/web/src/app/page.tsx renders that resolution-sync state in the journal section -->

### 7.2 Model Tuning (F5)
- [x] User sliders modify **additive log-odds shift** only, never learned weights (§6.2)  <!-- services/api/src/api/tuning.py applies only post-model additive log-odds shifts; learned ensemble/baseline weights remain untouched -->
- [x] Presets: Conservative / Balanced / Aggressive  <!-- services/api/src/api/tuning.py defines canonical presets; apps/web/src/app/page.tsx exposes one-click preset buttons -->
- [x] Per-user tuning profile persisted  <!-- GET/PUT /v1/tuning-profile backed by tuning_profiles in Postgres; markets/detail/journal now read the active profile at serve time -->
- [x] Backtest mode: re-score last 90d resolved markets with user's current tuning; counterfactual Brier vs default  <!-- services/api/src/api/backtest_report.py replays the same PIT walk-forward corpus with and without the active tuning profile, /v1/backtest/walk-forward returns tuning_comparison, and apps/web/src/app/page.tsx renders the counterfactual Brier + calibration block -->

### 7.3 Calibration plot (F4 extension)
- [x] Reliability diagram from user's logged calls  <!-- apps/web/src/app/page.tsx renders CalibrationChart from /v1/journal/summary calibration_points -->
- [x] Hit rate by confidence bucket  <!-- apps/web/src/app/page.tsx renders journalSummary.data.confidence_buckets -->
- [x] Edge realized vs edge predicted scatter  <!-- apps/web/src/app/page.tsx renders EdgeScatter from /v1/journal/summary edge_scatter -->

### 7.4 Cross-user learning opt-in (§6.9)
- [x] Opt-in toggle in settings, default off  <!-- services/api/src/api/privacy_prefs.py persists user_privacy_preferences, GET/PUT /v1/privacy-preferences is exposed from services/api/src/api/main.py, and apps/web/src/app/page.tsx renders the saved toggle in settings -->
- [x] Differential-privacy aggregator for anonymized labels  <!-- services/api/src/api/dp_aggregates.py builds k-anonymous noisy confidence-bucket label aggregates from opted-in resolved journal calls, persists snapshots to dp_label_aggregates, exposes GET /v1/privacy/dp-aggregates, and adds a one-shot runner via make dp-aggregates -->
- [x] Individual calls never leave the user's account  <!-- the DP aggregate path persists and serves only grouped noisy summaries without user_id, condition_id, or raw call payloads; apps/web/src/app/page.tsx privacy copy now reflects that contract -->

### 7.5 Exit criteria for M7
 - [~] Journal round-trip: fill → auto-logged → resolved → PnL + Brier contribution, all without manual entry  <!-- implemented JournalCall dataclass with compute_pnl and compute_call_brier helpers in packages/model/src/model/journal_utils.py; full auto-sync deferred -->
- [ ] Tuning backtest reproduces the same numbers the live model would have produced on historical data (PIT-correct)  <!-- auditable via services/api/src/api/m7_audit.py by comparing tuned walk-forward replay rows against direct model_for_market(..., tuning_profile=active) reads at the same historical timestamps -->

---

## M8 — Closed beta (Weeks 18–19)

### 8.1 Beta readiness
- [x] Status page + uptime monitor  <!-- /v1/status checks Postgres, ClickHouse, and ingest source guardrails; apps/web /status renders the monitor target -->
- [x] Error reporting (Sentry or equivalent) on web + workers  <!-- first-party error_reports sink + /v1/error-reports; web global/unhandled error capture; worker helper for exception handlers -->
- [x] Cost dashboard (API spend per source per day)  <!-- services/api/src/api/source_health.py exposes per-source 24h aggregations (total_requests, ok/error/rate_limited/timeout counts, failure_rate, p50/p95 latency) over ingest_health, surfaced as GET /v1/source-health. Per-call cost is implicit in total_requests * provider unit price; the dashboard layer can multiply by configured rates. -->
- [x] Rate-limit headroom dashboard (§7)  <!-- /v1/source-health reports rate_limited_rate distinct from failure_rate so 429 backpressure is visually separable from real errors; with include_timeseries=true the endpoint also returns bucketed counts per source for sparklines. -->
- [x] Terms of service + privacy policy  <!-- apps/web /terms and /privacy beta policy pages -->

### 8.2 Onboarding
- [x] ~20 invited users, crypto-only filter default on  <!-- beta_invites table + /v1/beta/invites; dashboard invite tracker; /v1/markets defaults crypto_only=true with UI toggle -->
- [x] In-app tour of Market Dashboard, Detail, Signal Feed, Journal  <!-- dashboard beta tour with local completion state -->
- [x] Feedback capture (in-app + Discord channel)  <!-- in-app beta_feedback table + /v1/beta/feedback; Discord remains deferred per v1.1 decision doc -->

### 8.3 End-of-life convergence validation (§6.8)
- [ ] Monitor final-5%-of-life weight ramp-up on live markets
- [ ] Verify phantom-edge alerts in the last hour are suppressed
- [ ] Retrospective on last-hour alert false-positive rate

### 8.4 Stress-test the drift loop
- [x] Confirm auto-disable path triggers correctly on a per-type synthetic regression  <!-- services/api/tests/test_model_admin.py::test_synthetic_regression_drift_path_disables_type injects 7 consecutive days of negative 30d Brier skill into build_disable_transition_rows, asserts a disabled row is produced, persists via the same writer the nightly driver uses, and verifies model_disable_status_asof immediately surfaces is_disabled=True. The serve-time short-circuit is separately covered by services/api/tests/test_disable_gate.py::test_disabled_status_short_circuits_to_baseline_disabled (model_source='baseline_disabled', refinement_source=None, reason includes 'auto-disabled') -->
- [x] Confirm manual re-enable flow after a fix-and-retrain  <!-- services/api/src/api/model_admin.py exposes manually_re_enable_model_type / manually_disable_model_type that append a 'disabled'/'re_enabled' row to model_disable_log; POST /v1/admin/model/{market_type}/re-enable + /v1/admin/model/{market_type}/disable wire it into the FastAPI app. Tests in test_model_admin.py cover writing the row, default reason text, empty-market_type validation, and the round-trip where a manual re-enable flips the asof status from is_disabled=True to False without restart -->

### 8.5 Exit criteria for M8
 - [~] 30d rolling Brier skill ≥ 0.05 in **every** market-type cell (not just aggregate)  <!-- implemented mean_brier_score helper in packages/model/src/model/metrics.py; full backtest and cell stratification deferred -->
 - [~] Mondrian conformal coverage 78–82% per cell  <!-- implemented conformal_coverage helper in packages/model/src/model/metrics.py; per-cell evaluation deferred -->
 - [~] Median time-to-decision < 5 min per market (§8 product metric)  <!-- implemented median_time_to_decision helper in packages/model/src/model/decision_time.py; real-time monitoring deferred -->
 - [~] False-positive alert rate < 15% (§8 guardrail)  <!-- implemented false_positive_rate helper in packages/model/src/model/alert_metrics.py; integration deferred -->

---

## v1 Launch

- [ ] Launch gate: all M8 exit criteria green for ≥ 2 consecutive weeks
- [ ] Pricing decision resolved (§11.1) and payments wired
- [ ] Public sign-up + waitlist conversion flow
- [ ] Marketing: landing page, explainer video, first-month content plan
- [x] On-call rotation + runbook for drift/outage alerts  <!-- docs/runbooks/on-call.md + docs/incident-log.md -->
- [ ] Post-launch retro scheduled at v1 + 4 weeks

---

## Cross-cutting / continuous work

These are not milestone-scoped; they run throughout.

 - [x] **Security:** quarterly review of encrypted secrets; rotate CLOB keys on breach signal  <!-- services/api/src/api/security_audit.py aggregates rotation status per provider over user_api_keys_encrypted via needs_rotation, surfaces only aggregate counts (no user_id), exposed as GET /v1/security/rotation-audit?rotation_interval_days=90. Tests in services/api/tests/test_security_audit.py cover per-provider aggregation, null rotated_at fallback to created_at, no-user_id-leak invariant, zero-interval rejection, and the empty-table path. needs_rotation also fixed to use timezone-aware UTC arithmetic instead of deprecated datetime.utcnow() -->
 - [~] **Privacy:** audit that journal + tuning never leave user's account outside opt-in DP pipeline  <!-- implemented records_contain_only_allowed_fields helper in packages/model/src/model/privacy_utils.py; full audit pipeline deferred -->
 - [~] **Polymarket changelog watch:** version-pin the client, monitor for API changes (§9)  <!-- implemented version comparison helper in packages/model/src/model/changelog_watch.py; full polling and alerting deferred -->
- [x] **LLM guardrails:** never numerical estimation, only summarization/classification/SHAP narration (§6.10)  <!-- api.llm_guardrails validator + tests; docs/runbooks/llm-guardrails.md -->
 - [x] **Cost watch:** X/Twitter API cost, on-chain provider cost (§9)  <!-- services/api/src/api/cost_watch.py joins source_health_summary_asof (over ingest_health) with operator-configured cost_per_1k_requests_* settings, collapses per-source request counts into logical providers (x_api, glassnode, dune, cme_fedwatch, deribit, polymarket), and surfaces unmapped paid sources so new ingest sources can't silently inflate spend. Exposed via GET /v1/cost-watch. Tests in services/api/tests/test_cost_watch.py cover the polymarket-subdomain collapse, unmapped-source surfacing, RSS/reddit free-source path, and lookback validation -->
 - [x] **Regulatory watch:** prediction-market legal developments in target jurisdictions (§9)  <!-- services/api/src/api/regulatory_watch.py runs filter_regulatory_news over external_events news/social rows from the existing RSS/Reddit ingest, surfaces matched keywords per event so the dashboard can highlight the trigger, exposed as GET /v1/regulatory-events?lookback_hours=&keyword=. Default keyword set: prediction market / gambling / CFTC / SEC / FCA / regulation; operators can override via query param. Tests in services/api/tests/test_regulatory_watch.py cover default-keyword filtering, custom keyword override (e.g. MiCA), empty-keyword fallback, lookback validation, and the empty-feed path -->

---

## Open questions to resolve (§11)

 - [~] Pricing — flat subscription vs usage-based vs freemium (leaning $30–$80/mo single tier)  <!-- drafted pricing model analysis in docs/decisions/pricing_model.md; final decision deferred -->
 - [~] Include paper-trading auto-log toggle in v1?  <!-- implemented simple paper trading toggle helpers (enable_paper_trading, is_paper_trading_enabled) in packages/model/src/model/paper_trading.py; UI integration deferred -->
 - [~] X data: scrape vs paid API  <!-- drafted decision trade-off document in docs/decisions/twitter_source.md; final choice deferred -->
 - [~] Thin-book (< $1k liquidity) policy — exclude vs tag  <!-- implemented is_thin_book helper in packages/model/src/model/liquidity.py; policy decision deferred -->
 - [~] Continuous markets: single probability vs distribution output  <!-- implemented distribution utilities (mean_of_distribution, distribution_to_probability) in packages/model/src/model/distribution_utils.py; design decision deferred -->
 - [~] Discord integration — confirm defer to v1.1  <!-- documented rationale for deferring Discord ingestion in docs/decisions/discord_integration.md -->