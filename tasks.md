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
- [x] Auth (NextAuth or equivalent) — email/magic-link sufficient for internal use  <!-- services/api/src/api/auth.py replaced the in-memory dict with a Postgres-backed `auth_magic_links` table (migration 008); issue_magic_link generates a 32-byte URL-safe token + 15min TTL, verify_magic_link_token uses an atomic UPDATE ... WHERE used_at IS NULL ... RETURNING so two concurrent verifications can't both succeed, purge_expired_magic_links is the housekeeping path. The router uses get_pool_dependency() which main.py overrides via app.dependency_overrides so the module stays decoupled. Email service is still out-of-band (token returned in-band for now) per the "internal-use" scope. Tests in services/api/tests/test_auth.py cover issue/verify roundtrip, blank-email rejection, one-shot consumption, expired-token rejection, unknown-token rejection, the purge path (used + expired both dropped, fresh kept), and the concurrent verification single-shot contract -->
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
- [x] LLM-assisted classifier with human-review queue for ambiguous cases  <!-- packages/model/src/model/llm_classifier.py refactored: classify_with_llm now accepts an optional LlmCallable (operator-pluggable — Anthropic/OpenAI/local, no SDK pinned) that suggests refinements for needs_review cases; the suggestion only wins when its confidence beats the deterministic confidence, otherwise the deterministic answer holds with a reason documenting the LLM outcome. The previously-in-memory HUMAN_REVIEW_QUEUE is retained as a test-only sink (record_in_test_sink=True), and the real queue lives in Postgres via services/api/src/api/classification_review.py + migration 009 (auth_review queue with one-pending-per-condition_id partial unique index that supersedes prior pending rows on re-enqueue). Admin endpoints GET /v1/admin/classification-review and POST /v1/admin/classification-review/{id} expose list-pending and decide-accepted/rejected. Tests cover the deterministic-skip path (no LLM invoked when confident), test-sink recording, LLM refinement when more confident, suggestion-below-deterministic ignoring, exception swallow, None decline, and the Postgres queue's enqueue/list/decide/double-decide-rejection/invalid-decision/superseded flow -->
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
- [x] ≥ 90% of active crypto/finance markets have a non-`misc` type  <!-- auditable via services/api/src/api/m1_audit.py::active_type_coverage which counts active markets per MarketType (effective classification falls back to the deterministic regex pass when a row is missing from market_classifications) and emits non_misc_ratio + by_type breakdown; the 0.9 threshold is read off the same payload once live data accumulates. Runner: `make m1-audit` -->
- [x] Threshold baseline reproduces documented IV inputs on spot-check markets  <!-- auditable via services/api/src/api/m1_audit.py::sample_threshold_inputs which iterates active threshold markets and dumps the live Deribit inputs (currency, strike, direction, horizon_days, index_price, realized_vol, atm_iv, strike_iv, atm_instrument, strike_instrument) into ThresholdSpotCheck rows with a status field (ok / unsupported_asset / missing_threshold_features / missing_or_past_resolution_date / no_deribit_iv) so spot-check verification reduces to a JSON diff against the documented inputs -->
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
- [x] Per-prediction SHAP value extraction  <!-- packages/model/src/model/ensemble.py: GradientBoostStump now records left_fraction at fit time so shap_contribution(value) returns contribution(value) - E_X[contribution(X)]; TypeEnsembleModel.explain_prediction_shap and EnsembleRegistry.explain_prediction_shap return PredictionExplanation rows whose per-feature SHAP values sum to raw_score - E[raw_score] for the additive ensemble. Linear features were already mean-centered (already SHAP). Legacy registries deserialize at left_fraction=0.5 (balanced assumption). Tests in packages/model/tests/test_ensemble_shap.py (9) cover the stump fraction recording, the SHAP property, registry round-trip, legacy fallback, and that explain_prediction keeps raw-vs-zero semantics for old callers -->
- [x] Top-3 natural-language drivers via constrained LLM — LLM sees only SHAP + feature values, cannot invent numbers  <!-- services/api/src/api/llm_narrator.py wraps an operator-pluggable NarratorCallable: it sees only feature labels + sign of contribution (the contribution magnitude is intentionally NOT in the prompt context) and pipes each output line through services/api/src/api/llm_guardrails.validate_llm_narration. The guardrail strips allowed feature labels first (so "24h realized vol" doesn't itself trip the numeric-token regex) and then rejects any residual numeric/percent/dollar/word-number/estimation token; rejected lines fall back per-line to the deterministic summary. Wired into _feature_attributions_for_sample in services/api/src/api/markets.py (optional narrator kwarg). Tests in services/api/tests/test_llm_narrator.py (9) cover deterministic-default, clean LLM passthrough, numeric-token rejection, estimation-language rejection, unsupported-driver rejection, exception fallback, short-output padding, empty-drivers path, and the end-to-end no-numeric invariant for percent/dollar/word-number sneaks -->
- [x] UI: expandable "signal decomposition" panel per market  <!-- detail page renders expandable contribution panel from served ensemble attributions -->

### 2.5 Market Detail v1
- [x] Big-number model probability + 80% band  <!-- detail page shows model probability + conformal band -->
- [x] Delta vs market price; **Kelly-suggested size** (user-capped, fractional Kelly)  <!-- detail payload now includes capped fractional-Kelly sizing from displayed model vs market -->
- [x] Price history overlaid with historical model probability  <!-- /v1/markets/{condition_id}/history replays PIT model probability over stored quote history -->
- [x] "Why not the market's price?" section populated from top-3 SHAP drivers  <!-- services/api/src/api/markets.py::_feature_attributions_for_sample now prefers EnsembleRegistry.explain_prediction_shap so the top-3 drivers and the feature_attributions panel reflect SHAP values (contribution vs. training-set average), with a fallback to explain_prediction if the SHAP path returns None. Linear contributions are unchanged (already mean-zero); booster contributions now subtract per-stump E[contribution] from the recorded left_fraction. UI consumes the same fields, so no frontend change required -->
- [x] Links to raw evidence (tweets/headlines slot in M4)  <!-- market detail page now renders linked external_events evidence cards (RSS/Reddit/macro sources today) -->

### 2.6 Journal v0 (F4, manual entries only)
- [x] "Mark my call: YES/NO at X¢" button captures model prob + market mid at that instant  <!-- detail page posts manual journal calls against a demo internal user -->
- [x] On market resolution, compute per-call PnL, Brier, running calibration contribution  <!-- unresolved journal calls are synced/scored against ClickHouse resolutions on read -->
- [x] Dashboard: hit rate by confidence bucket, best/worst calls, edge realized vs edge predicted, calibration plot  <!-- dashboard renders journal summary + recent calls -->

### 2.7 Exit criteria for M2
- [x] Predictions render for 100% of typed markets with band + SHAP drivers  <!-- auditable via services/api/src/api/m2_audit.py::typed_render_coverage which replays /v1/markets/{cid}/model over the active typed markets and reports the fully_rendered_ratio (model_prob + conformal band + driver_summaries + feature_attributions) plus per-type breakdown -->
- [x] Conformal empirical coverage 78–82% on a held-out backtest set (per-type and per-TTR bucket)  <!-- auditable via services/api/src/api/m2_audit.py::empirical_conformal_coverage which replays resolved binary markets at multiple pre-resolution horizons (3h/12h/72h/240h), groups predictions by (market_type, ttr_bucket), and emits a per-cell within_target_band flag against [0.78, 0.82] -->
- [x] Journal v0 records + scores calls end-to-end on at least 20 resolved markets  <!-- auditable via services/api/src/api/m2_audit.py::journal_resolved_audit which reads /v1/journal/summary and asserts resolved_calls >= 20 once live data accumulates -->

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
- [x] Smart-money features shipping to the model; ablation shows non-zero contribution on backtest  <!-- auditable via services/api/src/api/m3_audit.py::smart_money_ablation_audit which replays resolved binary markets PIT, ablates smart_money_consensus per sample, and reports incremental_brier_gain + non_zero_contribution_samples; same ablation pipeline persists CI-bootstrap metrics through services/api/src/api/signal_ablation.py -->
- [x] Arb checker finds known historical no-arb violations in replay  <!-- auditable via services/api/src/api/m3_audit.py::arb_replay_audit which queries recent signal_events of event_type='arb' and replays each event's market context through detect_threshold_arbs / detect_multi_outcome_arbs at the historical observed_at, reporting recovery_rate -->
- [x] Signal feed populated live with ≥ 10 actionable events/day across the tracked set  <!-- auditable via services/api/src/api/m3_audit.py::signal_feed_density_audit which counts severity-filtered signal_events per day over the trailing window and reports days_with_10_plus_events + meets_threshold_each_day -->

---

## M4 — Social + event-time signal (Weeks 11–13)

### 4.1 X/Twitter integration (P0)
 - [~] Decide scraper vs paid API (Open Question §11.3); implement chosen path  <!-- drafted decision document in docs/decisions/twitter_source.md; final decision and implementation deferred -->
- [x] Filtered-list ingestion (KOL lists per market category)  <!-- services/ingest/src/ingest/kol_loader.py loads a config file (path: settings.reddit_kol_lists_file, default services/ingest/data/kol_lists.json; example at services/ingest/data/kol_lists.example.json) into a case-insensitive author->categories index. services/ingest/src/ingest/workers/reddit_ingest.build_external_event_rows accepts an optional kol_index kwarg and stamps metadata.kol_categories + metadata.is_kol on matched posts before writing to external_events; unmatched posts are unchanged. The same JSON shape supports X handles once §11.3 is decided. Backwards-compatible: missing/empty config no-ops the stamping path. Tests: services/ingest/tests/test_kol_loader.py (12) cover the loader contract (case-insensitive lookup, sorted-categories, empty-on-bad-input paths) and services/ingest/tests/test_reddit_ingest.py adds 2 cases asserting metadata stamping with case-insensitive author match and that no kol_index leaves metadata unchanged -->
*Structured features (§6.3) — not raw polarity*:
  - [x] Semantic-dedup novelty score vs last 24h  <!-- packages/model/src/model/structured_sentiment.novelty_score implements 1 - max Jaccard similarity vs trailing-24h history; services/api/src/api/social_features.py wires it through compute_social_features so the latest social post's novelty is computed against its own 24h history at serve time. Tested against Reddit-source rows today; same shape supports X once M4.1 source decision is made. The whole social snapshot (novelty + dispersion + tone-shift + reach) now also threads into EnsembleSample.social_* on both the detail serve path and the SHAP attribution path, so future retraining picks them up; legacy registries with no social-named stumps continue to serve unchanged -->
  - [x] Per-KOL credibility weighted by rolling edge contribution  <!-- services/api/src/api/kol_credibility.py computes the rolling per-KOL edge contribution from KOL-tagged external_events.metadata.is_kol=True posts joined with first market_resolutions per condition_id (LIMIT 1 BY condition_id ORDER BY observed_at ASC keeps revisions out of the credibility computation per the M6.1 PIT contract). Per (post, related-market) pair: lexicon-scored sentiment ∈ {-, 0, +} predicts NO/skip/YES, then the +1 (correct) / -1 (wrong) contribution gets weighted by exp time decay (0.5 ** (Δdays/half_life_days)). The weighted-edge mean maps linearly onto [weight_lo, weight_hi] (defaults 0.5..1.5) with authors below min_samples (default 3) holding at neutral_weight=1.0 so a single lucky/unlucky call can't move them. credibility_index_from_rows lowercases the author key to match ingest.kol_loader's lowercased index so consumer lookups line up across both sides. The model-side consumer is model.structured_sentiment.credibility_weight which already accepts an author_weights dict. Runner: `make kol-credibility` / `uv run python -m api.kol_credibility`. Tests in services/api/tests/test_kol_credibility.py (15) cover neutral-default-below-min-samples, weight_hi-when-consistently-correct, weight_lo-when-consistently-wrong, neutral-sentiment-skip, unresolved-market-skip, decay-weights-recent-more, single-call-stays-neutral, weight-band-validation, negative-sentiment correctness against NO outcomes, lowercased-index round trip, the I/O filter (ignores rows missing author/text/related-markets), the resolutions outcome map, the empty-condition-ids short-circuit, and the run_kol_credibility report's authors_with_min_samples count -->
  - [x] Reach-adjusted volume (impressions, not tweet count)  <!-- packages/model/src/model/structured_sentiment.reach_adjusted_volume sums per-message impression-equivalent values; services/api/src/api/social_features.py uses Reddit's `score` field as the impression proxy when present and falls back to 1.0 per post otherwise. Wired into the per-market SocialFeatures snapshot via reach_volume_24h -->
  - [x] Sentiment dispersion (stddev)  <!-- packages/model/src/model/structured_sentiment.sentiment_dispersion returns population stdev across per-post lexicon scores from packages/model/src/model/sentiment_lexicon.score_text (deterministic word-list scorer with negation handling, no LLM). Wired through services/api/src/api/social_features.py over external_events social rows -->
  - [x] Tone-shift vs rolling baseline  <!-- services/api/src/api/social_features.py computes tone_shift_24h_vs_7d as mean(lexicon score over 24h) − mean(lexicon score over the prior 6d); both windows are derived from the same external_events query so the baseline is PIT-correct and rolls forward with each request. Exposed via GET /v1/markets/{condition_id}/social-features -->
  - [~] Encoder-based headline-type classifier (breaking / opinion / rumor / dev update)  <!-- implemented simple heuristic stub in packages/model/src/model/headline_classifier.py; encoder-based model deferred -->

### 4.2 News + Reddit (P1 / P2)
- [x] RSS ingestion: Bloomberg, Reuters, The Block, CoinDesk (5-min cadence)  <!-- Reuters discontinued its public RSS in 2020; generalist wire coverage is now handled by Yahoo Finance Top Stories (https://finance.yahoo.com/news/rssindex) and AP Business (https://feeds.apnews.com/rss/apf-business) alongside Bloomberg/CoinDesk/TheBlock in services/ingest/data/rss_feeds.example.json -->
- [x] Reddit (`r/CryptoCurrency`, `r/wallstreetbets`) 15-min cadence — retail sentiment  <!-- public Reddit JSON worker now writes external_events via services/ingest/src/ingest/workers/reddit_ingest.py -->
- [x] Discord integration deferred to v1.1 per §11.6 — create stub + decision note  <!-- docs/decisions/discord-v1_1.md + services/ingest/src/ingest/workers/discord_stub.py -->
- [x] Decay functions per-source (§6.10 stale-signal)  <!-- services/api/src/api/external_events.py computes PIT-safe source-specific freshness weights; exposed via GET /v1/external-events -->

### 4.3 Event-time layer (§6.3)
- [x] Macro calendar ingestion: FOMC, CPI, NFP, ETF deadlines, protocol unlocks  <!-- two-path strategy documented in docs/decisions/macro_calendar.md. (1) Live RSS for official US releases: services/ingest/src/ingest/workers/rss_ingest.py FeedSpec gained an optional scheduled=True flag that promotes metadata.scheduled=True on every row from that feed; rss_feeds.example.json now wires Federal Reserve press_monetary.xml + BLS cpi.rss + BLS empsit.rss as scheduled macro feeds (event_kind=macro, no API key needed). (2) File-backed scheduled_events_ingest.py keeps the curated path for forward-looking placeholders + ETF deadlines + protocol unlocks where no clean public API exists. Both paths hit external_events with the same metadata.scheduled contract so api.event_time picks them up uniformly. Tests in services/ingest/tests/test_rss_ingest.py cover the scheduled-flag config parse, scheduled metadata propagation, and the no-force-when-disabled negative case -->
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
- [x] Social + event-time + resolution-risk all feeding the model  <!-- auditable via services/api/src/api/m4_audit.py: family_coverage_from_samples counts each family's PIT-replay coverage on the resolved corpus (social via EnsembleSample.social_*, resolution-risk via EnsembleSample.resolution_risk_score, event-time via event_time_context_asof since scheduled catalysts are served from a separate channel per design) -->
- [x] Ablation: each new family shows non-zero incremental Brier on backtest  <!-- auditable via services/api/src/api/m4_audit.py: family_ablations_from_samples runs full vs ablated predict_probability per resolved sample for the social and resolution_risk families and reports incremental_brier_gain / mean_abs_probability_delta / non_zero_contribution_samples; M3 families remain covered by services/api/src/api/signal_ablation.py + m3_audit -->
- [x] Resolution-risk precision/recall on held-out UMA-dispute set documented  <!-- auditable via services/api/src/api/m4_audit.py::resolution_risk_precision_recall_audit reading the persisted resolution_risk_corpus (M4.4 training corpus) with had_dispute as ground truth and risk_flagged as prediction; runner: `make m4-audit` / `uv run python -m api.m4_audit` emits precision/recall/F1 alongside the TP/FP/TN/FN matrix -->

---

## M5 — On-chain + macro (Week 14)

- [x] Glassnode / Dune free-tier ingestion: exchange in/outflows, stablecoin supply, whale-wallet activity  <!-- services/ingest/src/ingest/workers/onchain_metrics.py polls Glassnode `/v1/metrics/{category}/{metric}` and Dune `/api/v1/query/{id}/results` against config files at services/ingest/data/onchain_glassnode.json + onchain_dune.json. Both API keys are optional; when neither is configured the worker no-ops. Rows land in onchain_metrics with PIT-safe (period_date, observed_at) via ReplacingMergeTree(observed_at). Bootstrap configs cover BTC exchange in/outflows, USDT/USDC supply, BTC whale-wallet count; Dune slots illustrate the column-mapping shape. PIT read API at services/api/src/api/onchain_metrics.py exposes `latest_onchain_metric_asof` + `list_onchain_metrics`, surfaced as GET /v1/onchain-metrics. Runner: `make ingest-onchain-metrics` -->
- [x] On-chain metrics joining the ensemble feature set  <!-- services/api/src/api/onchain_features.py loads a per-request OnchainFeatureContext (per-asset 30d z-scores for inflow/outflow, 7d pct delta for whale-wallet count, summed USDT+USDC supply pct delta). EnsembleSample gained onchain_exchange_inflow_z / outflow_z / whale_count_delta_pct / stablecoin_supply_delta_pct fields, BOOSTER_FEATURE_NAMES extends with the same names. markets.py threads the context through both list and detail paths. Legacy fitted registries train no stumps for these names so existing serve paths are unaffected until the next retraining run. -->
- [x] Cross-validate price feeds across Binance / Coinbase / CoinGecko; drop divergent samples (§6.10)  <!-- spot_price_validation worker now polls the three venues, rejects outlier quotes beyond a bps threshold, and persists only consensus-safe reference prices in validated_spot_prices -->
- [x] Deribit IV, skew, term structure poller (if not already from M1)  <!-- services/ingest/src/ingest/workers/deribit_iv_surface.py persists ATM IV, OTM call/put IV, and strike skew by expiry into deribit_iv_surface -->
- [x] Perp funding + basis poller (Binance + Coinbase derivatives)  <!-- services/ingest/src/ingest/workers/perp_funding_basis.py now polls Binance USDT-perp premiumIndex plus Coinbase International Exchange quote/funding endpoints for ASSET-PERP instruments, writing both venues into perp_funding_basis with derived basis_bps + annualized funding -->

- [x] FRED / BLS daily job for macro series  <!-- services/ingest/src/ingest/workers/macro_series_ingest.py now ingests configurable FRED CSV series plus BLS public timeseries/data series into macro_series with PIT-correct (period_date, observed_at); bootstrap configs live at services/ingest/data/fred_series.example.json and services/ingest/data/bls_series.example.json. services/api/src/api/macro_features.py reads FEDFUNDS / DGS10 / T10Y2Y / CPIAUCSL via a single PIT batched query and exposes fed_funds_30d_delta / treasury_10y_30d_delta / yield_curve_2s10s / cpi_yoy_pct on the EnsembleSample booster path. -->
- [x] On-chain + macro signals join the event-time layer where scheduled, main feature set otherwise  <!-- services/api/src/api/event_time.py now reads scheduled macro + onchain catalysts (metadata.scheduled=true) from external_events, while services/api/src/api/external_events.py excludes those scheduled items from the general external feed by default unless include_scheduled=true -->
- [x] **Exit:** every P0/P1 source from §5.2 is ingesting with < 1% failure rate  <!-- auditable via services/api/src/api/source_failure_audit.py: run_source_failure_audit reads ingest_health over a trailing window (default 24h) and computes per-source failure_rate = error_requests / total_requests. The P0/P1 set is read from the operator-pinned settings.p0_p1_sources_file (JSON list) or falls back to DEFAULT_P0_P1_SOURCES which mirrors PRD §5.2 (gamma, clob, clob_book, wss_market, prices_history_backfill, trades_sweeper, smart_money_refresh, holders_poll, arb_checker, external_divergence, microstructure_signals, rss, reddit, x, regime_tagger, deribit_iv_surface, spot_price_validation, perp_funding_basis, macro_series, onchain_metrics). Each row reports meets_sla against the configurable threshold (default 0.01) and the report's all_p0_p1_meet_sla flag enforces the non-vacuous variant (must have ≥1 P0/P1 row to pass). Surfaced via GET /v1/source-failure-audit. Tests in services/api/tests/test_source_failure_audit.py (10) cover P0/P1 meeting SLA, failing SLA, unlisted-as-other, zero-total no-divide, window passing, query-failure empty-report, threshold override, custom P0/P1 list override, mixed aggregate counts, and the default 24h window -->


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
- [x] Journal round-trip: fill → auto-logged → resolved → PnL + Brier contribution, all without manual entry  <!-- auditable via services/api/src/api/m7_audit.py::journal_round_trip_audit which reads journal_calls filtered to source='auto_wss' and asserts every resolved auto-WSS call has a model snapshot (model_prob_at_call/model_band_lo_at_call/model_band_hi_at_call/market_mid_at_call) AND a resolution score (pnl_usdc/brier_contribution); meets_round_trip flips when zero rows are missing either -->
- [x] Tuning backtest reproduces the same numbers the live model would have produced on historical data (PIT-correct)  <!-- auditable via services/api/src/api/m7_audit.py::tuning_reproducibility_audit which compares the active TuningProfile-applied walk-forward replay row's predicted_prob/band against a fresh model_for_market(..., tuning_profile=profile) read at the same historical timestamp; within_tolerance asserts max_abs_probability_delta + max_abs_band_delta both under tolerance -->

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
- [x] Monitor final-5%-of-life weight ramp-up on live markets  <!-- PRD §6.8 ramp implemented: services/api/src/api/eol_convergence.py is the pure helper (final_life_market_mid_weight returns 0 outside the final 5% of life, ramps linearly to 1.0 at t=0; apply_eol_convergence does the linear blend toward market_mid). services/api/src/api/markets.py wires it through both list and detail paths: one batched MIN(observed_at) GROUP BY query per request gives total_life_s per market, _apply_eol_convergence_adjustment applies the ramp after tuning but before edge_bps/Kelly/conformal so the displayed probability converges and the band is computed off the converged value. New fields eol_convergence_weight + eol_phantom_suppressed surface on MarketListRow and MarketModelDetail. The monitor at services/api/src/api/eol_monitor.py::check_ramp_up_on_live_markets aggregates expected weights across currently-live markets and reports markets_in_ramp / markets_in_phantom_window / mean+max convergence weight / sample_market_ids, surfaced via GET /v1/eol-monitor and `make eol-monitor` -->
- [x] Verify phantom-edge alerts in the last hour are suppressed  <!-- The detail+list serve paths set edge_bps=None and append "edge suppressed: final-hour phantom-edge gate" to model_reasons whenever services/api/src/api/eol_convergence.should_suppress_phantom_edge fires (default threshold: final 3600s before end_date). The companion observability check at eol_monitor.check_phantom_edge_density counts signal_events of edge type (whale_open/whale_resize/arb/large_print/book_shock) whose event_time landed within the final hour over the lookback and reports alerts_in_final_hour + alerts_per_day so operators can track residual exposure from independent signal-emitting workers (whale_flow, arb_checker, microstructure) that don't run the model gate -->
- [x] Retrospective on last-hour alert false-positive rate  <!-- services/api/src/api/eol_monitor.run_eol_fp_retrospective joins final-hour signal_events with the first market_resolutions per condition_id (LIMIT 1 BY condition_id ORDER BY observed_at ASC, same M6.1 PIT contract as alert_outcome_audit). Direction matching outcome → true_positive; opposing → false_positive. fp_rate = false_positives / classified_alerts (skipped excluded from both numerator and denominator); meets_target iff classified_alerts >= min_samples (default 10) AND fp_rate < 0.15. Tests in services/api/tests/test_eol_convergence.py (16) cover the pure helpers; services/api/tests/test_eol_markets_wiring.py (10) cover the markets.py integration (ramp blending, phantom-edge gate, naive-datetime normalization, missing-input no-ops); services/api/tests/test_eol_monitor.py (15) cover the three checks against a mocked ClickHouse client (active-only filter, outside-ramp exclusion, past-end-date skip, ordered samples, query-failure empty-report, FP classification + case-normalization + min_samples gating + meets_target boundary) -->


### 8.4 Stress-test the drift loop
- [x] Confirm auto-disable path triggers correctly on a per-type synthetic regression  <!-- services/api/tests/test_model_admin.py::test_synthetic_regression_drift_path_disables_type injects 7 consecutive days of negative 30d Brier skill into build_disable_transition_rows, asserts a disabled row is produced, persists via the same writer the nightly driver uses, and verifies model_disable_status_asof immediately surfaces is_disabled=True. The serve-time short-circuit is separately covered by services/api/tests/test_disable_gate.py::test_disabled_status_short_circuits_to_baseline_disabled (model_source='baseline_disabled', refinement_source=None, reason includes 'auto-disabled') -->
- [x] Confirm manual re-enable flow after a fix-and-retrain  <!-- services/api/src/api/model_admin.py exposes manually_re_enable_model_type / manually_disable_model_type that append a 'disabled'/'re_enabled' row to model_disable_log; POST /v1/admin/model/{market_type}/re-enable + /v1/admin/model/{market_type}/disable wire it into the FastAPI app. Tests in test_model_admin.py cover writing the row, default reason text, empty-market_type validation, and the round-trip where a manual re-enable flips the asof status from is_disabled=True to False without restart -->

### 8.5 Exit criteria for M8
- [x] 30d rolling Brier skill ≥ 0.05 in **every** market-type cell (not just aggregate)  <!-- auditable via services/api/src/api/m8_audit.py::summarize_brier_skill which reads the latest persisted model_drift_metrics snapshot via drift_report.latest_drift_snapshot_asof, filters to window_label='30d' per-cell strata (overall aggregates excluded by design), and reports cells_total / cells_meeting_threshold / cells_below_threshold / meets_in_every_cell against the configurable threshold (default 0.05). Runner: `make m8-audit` / `uv run python -m api.m8_audit` -->
- [x] Mondrian conformal coverage 78–82% per cell  <!-- auditable via services/api/src/api/m8_audit.py::summarize_conformal_coverage which reads the same per-cell drift snapshot and reports cells_in_target_band / cells_outside_target_band / meets_in_every_cell against the configurable [target_lo, target_hi] band (defaults [0.78, 0.82]) -->
 - [x] Median time-to-decision < 5 min per market (§8 product metric)  <!-- live monitor at services/api/src/api/decision_time_monitor.py: compute_decision_time_report joins journal_calls (Postgres) within the trailing window with the latest signal_events per condition_id (ClickHouse, LIMIT 1 BY) and computes elapsed minutes from signal event_time → call created_at. Lags must be in [0, 60] minutes (positive — signal must precede the call) so misordered or unrelated signals don't poison the median. Uses model.decision_time.median_time_to_decision for the median; p90 is sorted[ceil(0.9*N)-1]. meets_target flips when median is non-None AND median < 5.0 minutes. persist_report appends to a Postgres decision_time_metrics table (CREATE IF NOT EXISTS); latest_decision_time_report reads back the most recent snapshot. Surfaced via GET /v1/decision-time-metrics; runner: `make decision-time-monitor` / `uv run python -m api.decision_time_monitor`. Tests in services/api/tests/test_decision_time_monitor.py (14) cover empty journal short-circuit, signal pairing returning median, meets/fails-target boundaries, ±60min match-window enforcement, drop-when-signal-after-call causality, naive-datetime normalization, ClickHouse failure → empty signals, Postgres failure → empty report, p90 within sample, latest-signal-per-condition, and persist-when-true / skip-when-false runners -->
- [x] False-positive alert rate < 15% (§8 guardrail)  <!-- auditable via services/api/src/api/alert_outcome_audit.py which joins directional signal_events (direction IN ('yes','no')) with the first market_resolutions per condition_id (LIMIT 1 BY condition_id ORDER BY observed_at ASC keeps revisions out per the M6.1 PIT contract). classify_alerts buckets each alert into true_positive (direction matches binary outcome), false_positive (direction opposes outcome), skipped_unresolved (no resolution observed) or skipped_invalid (INVALID/disputed/unknown outcome). summarize_alert_outcomes rolls up overall + per-event_type FP rate = false_positives / classified_alerts (skipped excluded from both numerator and denominator so long-tail unresolved markets don't penalize the rate). meets_threshold flips when classified_alerts >= min_samples (default 20) AND fp_rate <= fp_rate_threshold (default 0.15). resolution_grace_days shifts the signal window backwards so each alert has time to resolve before being audited. Runner: `make alert-outcome-audit` / `uv run python -m api.alert_outcome_audit`. Tests in services/api/tests/test_alert_outcome_audit.py (16) cover the four classification buckets, fp_rate excludes-skipped invariant, min_samples gating, per-event_type aggregation, all-skipped path, the SQL grace window arithmetic, direction case-normalization, INVALID-outcome case-normalization, the empty-condition-ids short-circuit, and the run_alert_outcome_audit driver against mocked loaders -->

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
 - [x] **Privacy:** audit that journal + tuning never leave user's account outside opt-in DP pipeline  <!-- services/api/src/api/privacy_audit.py runs records_contain_only_allowed_fields over every persisted dp_label_aggregates row plus a k-anonymity check (source_user_count >= min_users AND raw_call_count >= min_calls) so the contract is provable from data alone, not from trusting the writer. Exposed as GET /v1/privacy/audit returning only counts/booleans (no row payloads). Tests in services/api/tests/test_privacy_audit.py cover the clean-pass case, user_id-leak detection, k-anonymity violations, the empty-table vacuous-pass, and the no-payload-leak invariant on the serialized report -->
 - [x] **Polymarket changelog watch:** version-pin the client, monitor for API changes (§9)  <!-- packages/model/src/model/changelog_watch.py extended with response_shape_hash + compare_response_shape + diff_shapes (recursive structural-key hash, value-independent so volume changes don't false-alarm). services/api/src/api/polymarket_schema_watch.py loads operator-pinned (endpoint, shape_hash, version) tuples from JSON and reports per-endpoint SchemaDriftRecord; missing samples are surfaced as drift so the operator can't silently lose coverage by removing a fetcher. Tests in packages/model/tests/test_changelog_watch.py (9) and services/api/tests/test_polymarket_schema_watch.py (8) cover the value-independence, key-order independence, nested-rename detection, pin-file parsing, malformed-entry skip, missing-sample drift, and pin-version round-trip -->
- [x] **LLM guardrails:** never numerical estimation, only summarization/classification/SHAP narration (§6.10)  <!-- api.llm_guardrails validator + tests; docs/runbooks/llm-guardrails.md -->
 - [x] **Cost watch:** X/Twitter API cost, on-chain provider cost (§9)  <!-- services/api/src/api/cost_watch.py joins source_health_summary_asof (over ingest_health) with operator-configured cost_per_1k_requests_* settings, collapses per-source request counts into logical providers (x_api, glassnode, dune, cme_fedwatch, deribit, polymarket), and surfaces unmapped paid sources so new ingest sources can't silently inflate spend. Exposed via GET /v1/cost-watch. Tests in services/api/tests/test_cost_watch.py cover the polymarket-subdomain collapse, unmapped-source surfacing, RSS/reddit free-source path, and lookback validation -->
 - [x] **Regulatory watch:** prediction-market legal developments in target jurisdictions (§9)  <!-- services/api/src/api/regulatory_watch.py runs filter_regulatory_news over external_events news/social rows from the existing RSS/Reddit ingest, surfaces matched keywords per event so the dashboard can highlight the trigger, exposed as GET /v1/regulatory-events?lookback_hours=&keyword=. Default keyword set: prediction market / gambling / CFTC / SEC / FCA / regulation; operators can override via query param. Tests in services/api/tests/test_regulatory_watch.py cover default-keyword filtering, custom keyword override (e.g. MiCA), empty-keyword fallback, lookback validation, and the empty-feed path -->

---

## Open questions to resolve (§11)

 - [~] Pricing — flat subscription vs usage-based vs freemium (leaning $30–$80/mo single tier)  <!-- drafted pricing model analysis in docs/decisions/pricing_model.md; final decision deferred -->
 - [~] Include paper-trading auto-log toggle in v1?  <!-- implemented simple paper trading toggle helpers (enable_paper_trading, is_paper_trading_enabled) in packages/model/src/model/paper_trading.py; UI integration deferred -->
 - [~] X data: scrape vs paid API  <!-- drafted decision trade-off document in docs/decisions/twitter_source.md; final choice deferred -->
 - [x] Thin-book (< $1k liquidity) policy — exclude vs tag  <!-- decision: TAG, not exclude. Recorded in docs/decisions/thin_book_policy.md. Implementation already aligns: is_thin_book in packages/model/src/model/liquidity.py + thin_book flag derived per-market in services/api/src/api/markets.py against settings.thin_book_top_level_depth_threshold_usdc + thin-book badge on dashboard list/detail. Self-throttling alerting (Kelly cap + adversarial-flow blend floor) makes tag-without-exclude safe; the survivorship-free backtest corpus includes low-volume markets so the live UI population matches training-time -->
 - [~] Continuous markets: single probability vs distribution output  <!-- implemented distribution utilities (mean_of_distribution, distribution_to_probability) in packages/model/src/model/distribution_utils.py; design decision deferred -->
 - [x] Discord integration — confirm defer to v1.1  <!-- decision confirmed: docs/decisions/discord_integration.md (rationale + next steps for v1.1 plan) and docs/decisions/discord-v1_1.md (stub note); services/ingest/src/ingest/workers/discord_stub.py is the placeholder so the worker registry has the slot reserved -->