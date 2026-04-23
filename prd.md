# PolyPredictor — Product Requirements Document

**Status:** Draft v0.3
**Owner:** Fredrik Drakelid
**Last updated:** 2026-04-22

---

## 1. Overview

PolyPredictor is a decision-support application that helps users make more accurate predictions on [Polymarket](https://polymarket.com), with an initial focus on **cryptocurrency and finance markets**. The product aggregates signals from social media, news, on-chain data, and market microstructure, then synthesizes them into a probability estimate, a confidence band, and an edge-vs-market readout for each tracked market.

The core thesis: Polymarket prices already incorporate public information, but they lag or mis-weight signal from fragmented sources (Crypto Twitter, Discord, on-chain flows, Fed calendar, funding rates, etc.). A tool that aggregates these signals and grades them against market prices can surface edge that humans miss in real time.

PolyPredictor is a **research and decision tool**, not an autonomous trading bot. The user remains in the loop for every position.

---

## 2. Goals and Non-Goals

### 2.1 Goals
- Surface Polymarket crypto/finance markets where aggregated signal diverges from market price by a statistically meaningful margin.
- Give users a defensible probability estimate with transparent reasoning ("why does the model think 62% vs market's 48%?").
- Reduce time-to-decision from hours of manual research to under 5 minutes per market.
- Track prediction accuracy over time (Brier score, log loss, calibration) so users can trust — and improve — their edge.
- Provide a single pane of glass that replaces 8+ browser tabs (Twitter, TradingView, CoinGecko, Polymarket, Glassnode, CME FedWatch, Discord).

### 2.2 Non-Goals (v1)
- No automated trade execution. Users copy orders to Polymarket manually.
- No markets outside crypto/finance categories (politics, sports, culture come later).
- No social / copy-trading features. Single-user tool at launch.
- No mobile app in v1 — responsive web only.
- No custody of funds. PolyPredictor never holds user capital.

---

## 3. Target Users

### 3.1 Primary persona — "The Informed Degen"
- Active Polymarket trader, $5k–$250k bankroll, places 5–30 trades/week.
- Already following Crypto Twitter, monitors on-chain data, trades perps/spot.
- Pain: drowning in information, can't systematically weight signals, misses edges to faster / more disciplined traders.
- Wants: fast signal aggregation, calibrated probability estimates, a memory of past calls.

### 3.2 Secondary persona — "The Systematic Researcher"
- Quant-leaning, builds spreadsheets to model markets, values reproducibility.
- Pain: data pipelines are tedious; social signal is noisy and hard to encode.
- Wants: exportable data, transparent model logic, ability to override weights.

### 3.3 Out of scope for v1
- Casual retail users who trade sub-$100 positions (conversion is hard, LTV is low).
- Institutional desks (compliance, API access, multi-user — come later if at all).

---

## 4. Problem Statement

A Polymarket trader evaluating "Will BTC close above $150k on June 30, 2026?" today must manually:
1. Check current BTC price, implied vol, funding, perp basis (4 tabs).
2. Scan Crypto Twitter for breaking news in the last 24h (1 tab + 30 min).
3. Check on-chain: exchange inflows, whale wallets, stablecoin supply (2–3 tabs).
4. Check macro calendar: FOMC, CPI, ETF flows (2 tabs).
5. Read the Polymarket orderbook for liquidity and recent trades (1 tab).
6. Synthesize in their head, compare to the 63¢ market price, decide.

This takes 20–60 minutes per market and relies on the trader's ability to weight signals without bias. Most lose to the market. PolyPredictor compresses steps 1–6 into a single screen with an explicit model-vs-market delta and full source attribution.

---

## 5. Product Requirements

### 5.1 Market coverage (v1)

Track all **active** Polymarket markets in these categories:
- **Crypto:** price targets (BTC, ETH, SOL, majors), ETF flows and approvals, protocol launches, hack/depeg events.
- **Finance / macro:** Fed rate decisions, CPI prints, NFP, recession calls, S&P 500 price levels, earnings beats (MAG7).

Out of scope v1: altcoin long-tail (<$500M mcap), sports, politics, entertainment.

### 5.2 Signal sources

| Source | Role | Latency | Priority |
|---|---|---|---|
| Polymarket Gamma API | Market/event discovery, metadata, tags | 1 min | P0 |
| Polymarket CLOB API | Orderbook, midpoint, price history, trades | 1–5 s | P0 |
| Polymarket Data API | Positions, trader leaderboard, top holders | 1 min | P0 |
| Polymarket WSS (market + user channels) | Real-time book, trades, user fills | <1 s | P0 |
| X/Twitter (filtered lists) | Breaking news, KOL sentiment | 1–5 min | P0 |
| CoinGecko / Binance / Coinbase | Spot price, volume, funding | 1 min | P0 |
| Deribit | Implied vol, skew, term structure | 1 min | P0 |
| Glassnode / Dune (where free-tier allows) | On-chain flows, stablecoin supply | 10–60 min | P1 |
| FRED / BLS / CME FedWatch | Macro calendar, rate expectations | Daily | P1 |
| Reddit (r/CryptoCurrency, r/wallstreetbets) | Retail sentiment | 15 min | P2 |
| Discord (Alameda-ish channels via user-provided tokens) | Alpha leakage | 5 min | P2 |
| RSS: Bloomberg, Reuters, The Block, CoinDesk | News headlines | 5 min | P1 |

Each source is rate-limited, cached, and gracefully degrades when unavailable.

### 5.2.1 Polymarket API integration (deep dive)

Polymarket exposes three public REST APIs plus a WebSocket gateway. PolyPredictor treats Polymarket as both a **market price source** and a **first-class signal source** — the orderbook, trader leaderboard, and holder distribution are themselves predictive features, not just display data.

**Gamma API — `https://gamma-api.polymarket.com`** (discovery & metadata)
- `GET /markets`, `GET /events`: paginated catalog of all markets and events. Filter by `tag_slug` (`crypto`, `finance`, `economics`) and `active=true, closed=false` to scope v1 coverage.
- `GET /tags/{id}/related-tags/tags`: category graph — used to auto-expand market coverage as Polymarket adds new crypto/finance tags.
- `GET /public-search`: full-text market search for the user's search bar.
- Rate limit: 300 req / 10s on `/markets`, 500 req / 10s on `/events`. Ingestion runs every 60s with ETag/If-Modified-Since.

**CLOB API — `https://clob.polymarket.com`** (market microstructure)
- `GET /book?token_id=...` and `GET /books`: L2 orderbook for each outcome token. Used for **orderbook imbalance**, **bid/ask spread**, and **effective liquidity** features.
- `GET /midpoint` / `GET /midpoints`: mid price — our primary "market-implied probability" anchor.
- `GET /price` / `GET /prices`: best bid/ask per token. Used to compute slippage-adjusted entry price for Kelly sizing.
- `GET /prices-history?interval=1h|6h|1d&fidelity=60`: OHLC time series. Powers the price-history chart and the **price-momentum feature** in the model.
- `GET /trades` (public, by market): recent executed trades — used to detect large prints and sustained directional flow.
- Rate limits: 1,500 req / 10s on `/book`, `/price`, `/midpoint`; 1,000 req / 10s on `/prices-history`. Comfortable for ~200 tracked markets on a 5s poll for the top 20 and 60s poll for the rest.

**Data API — `https://data-api.polymarket.com`** (participant analytics)
- `GET /positions?user=0x...`: all open positions for an address. Used for the **Smart Money Tracker** (feature F6) — we follow the top 500 crypto/finance leaderboard addresses and aggregate what they're holding into a "smart consensus" per market.
- `GET /v1/leaderboard?category=CRYPTO|FINANCE&timePeriod=MONTH&orderBy=PNL`: ranked traders. Seeds the smart-money wallet list; refreshed daily.
- `GET /holders?market={conditionIds}`: top 20 holders per outcome token. Drives **holder concentration** (Gini-style score — a market dominated by one whale is a different risk profile than one with broad distribution) and **whale flow** deltas (holder changes across snapshots).
- `GET /trades?user=0x...` / `GET /users/{address}/earnings`: for the user's own journal auto-sync (feature F7).

**WebSockets — `wss://ws-subscriptions-clob.polymarket.com`**
- **Market channel** (`/ws/market`, type `market`): subscribe to `assets_ids` (token IDs). Receives orderbook deltas, trade executions, `best_bid_ask`, and `market_resolved` events. Enables the sub-second **signal feed** for price moves and market resolutions without polling. `initial_dump=true` seeds the book on connect.
- **User channel** (`/ws/user`, type `user`): authenticated with CLOB API key / secret / passphrase. Delivers the user's own order and trade updates in real time — feeds journal auto-sync without polling `/trades`.
- Dynamic `operation: subscribe|unsubscribe` lets us rotate the top 200 markets without reconnecting.

**Polymarket-derived features used by the model**
| Feature | Source | Why it matters |
|---|---|---|
| Mid-price + bid/ask spread | CLOB `/midpoint`, `/price` | Base market-implied probability and transaction cost |
| Orderbook imbalance | CLOB `/book` | Short-term directional pressure |
| Effective liquidity at 1%, 5% depth | CLOB `/book` | Whether the edge is actually tradable at size |
| 1h / 24h / 7d price momentum | CLOB `/prices-history` | Trend factor |
| **Informed-taker flow** (aggressor side, spread-crossing, size > p80) | CLOB `/trades` + `/book` | Trades crossing the spread are informational; passive fills are noise — they must be weighted differently |
| **Passive maker flow** (inside-spread fills) | CLOB `/trades` + `/book` | Flat signal most of the time; material only in sudden imbalance |
| Smart-money holdings | Data `/positions` + `/v1/leaderboard` | Crowd-adjusted prior weighted by track record |
| Holder concentration | Data `/holders` | Down-weights edges that exist only because one whale is cornering the market |
| Volume / open interest deltas | Gamma `/markets` | Liquidity growth as a conviction proxy |
| **Sibling-market term structure** | Gamma `/events` + CLOB `/midpoints` | Overlapping thresholds/dates form a no-arb surface; violations are free priors (see F8) |
| **Resolution-risk score** | Gamma `/markets` (resolution text) + UMA history | Ambiguous wording invalidates the probability entirely — model displays a warning badge and suppresses edge alerts above a threshold (see F9) |
| **Adversarial-flow score** | CLOB `/trades` + related-venue prices | Flags large directional flow with no corroboration from perps / options / social — candidate manipulation, down-weighted |

**Authentication posture (v1)**
- Read-only by default. All Gamma and market-data CLOB endpoints are public — no auth needed.
- User-specific Data API endpoints (`/positions?user=...` for *other* wallets) are public by address — no user credential required to track leaderboard traders.
- The user's **own** journal auto-sync (F7) and User WSS require the user's Polymarket CLOB API key + secret + passphrase, stored encrypted at rest and never used for trading endpoints in v1.

### 5.3 Core features (v1)

**F1 — Market Dashboard**
- List view of tracked markets, sortable by `|model − market|` edge, volume, time-to-resolution.
- Each row: question, market price, model probability, edge (bps), signal strength, liquidity, time remaining.
- Default filter: crypto + finance, edge > 300 bps, liquidity > $10k.

**F2 — Market Detail View**
- Big number: model probability with 80% confidence band.
- Delta vs market price, with Kelly-suggested size (user-capped).
- **Signal breakdown panel:** each source's contribution (sentiment score, weight, direction), expandable to raw evidence (tweets, headlines, chart snapshots).
- Price history chart overlaid with model probability over time.
- Orderbook snapshot + recent trades.
- "Why not the market's price?" natural-language explainer generated from top 3 contributing signals.

**F3 — Signal Feed**
- Chronological stream of high-weight events across all tracked markets (e.g., "Fed Chair hawkish remark detected, -40 bps on 'Fed cuts in June' market").
- Clickable to the source (tweet, headline, on-chain tx).
- Optional push / email / webhook alerts on threshold breaches.

**F4 — Prediction Journal**
- Every time user marks a market as "my call: YES/NO at X¢," the app logs it.
- On resolution, computes per-call PnL (paper or actual), Brier score, and contribution to running calibration curve.
- Dashboard shows: hit rate by confidence bucket, calibration plot, best/worst calls, edge realized vs edge predicted.

**F5 — Model Tuning**
- Slider-based adjustment of source weights (e.g., "I trust on-chain more than Twitter").
- Presets: Conservative / Balanced / Aggressive.
- Backtest mode: apply current weights to last 90 days of resolved markets, show counterfactual Brier.

**F6 — Smart Money Tracker** (Polymarket-native)
- Seeded from the Polymarket Data API leaderboard (`category=CRYPTO`, `category=FINANCE`, `timePeriod=MONTH` and `ALL`), maintaining a rolling list of ~500 top traders by PnL and volume.
- For each tracked market, display: how many smart-money wallets hold YES vs NO, total size, average entry, and directional change over the last 24h.
- "Whale alert" events fire in the signal feed when a top-50 wallet opens or materially resizes a position (≥10k USDC or ≥20% of their current market size).
- Holder concentration score per market (`/holders`): flags markets where a single wallet holds >40% of an outcome — the edge is real but the exit may not be.
- Smart-money consensus feeds into the ensemble as its own weighted signal, down-weighted when concentration is high (to avoid copying a whale who is themselves wrong).

**F7 — Journal Auto-Sync** (Polymarket-native)
- User connects their Polymarket address (read-only; or optionally CLOB API keys for the authenticated User WSS channel).
- Every fill on Polymarket is automatically logged as a call in the journal, with the model's probability *at the time of the fill* captured for later calibration scoring.
- Resolved markets pull from `/positions` with `redeemable=true` and from `/users/{address}/earnings` to close out PnL without manual entry.
- Eliminates the "I forgot to log it" gap that kills most trading journals.

**F8 — Arb / Cross-Market Checker**
- Continuously joins sibling Polymarket markets into event groups: overlapping thresholds ("BTC > $140k" vs "BTC > $150k" same date), overlapping dates (same threshold, June vs July), and mutually exclusive multi-outcome legs (which must sum to ≤1).
- Detects no-arb violations in real time (e.g., P(BTC > $140k) < P(BTC > $150k)) and surfaces them as a distinct alert class, with the implied arb size after spread and fees.
- Even where there's no exploitable arb, the **sibling-implied prior** feeds the main model — a cheap, model-free Bayesian anchor that's almost impossible to beat when available.
- Also cross-checks Polymarket price vs external venues (perps implied odds via basis, options-implied probability via Deribit), flagging wide divergences.

**F9 — Resolution-Risk Flag**
- Every market's resolution text (from Gamma `/markets`) is scored by a dedicated classifier for ambiguity: ill-defined thresholds, unclear data sources, dispute-prone phrasing, dependency on a single oracle, reliance on unpublished data.
- Historical UMA dispute outcomes train the classifier — markets that were disputed or resolved unexpectedly form the positive class.
- Markets with high resolution-risk get a warning badge in the UI, edge alerts are suppressed above a risk threshold, and the displayed model probability is widened (higher uncertainty) since the payout itself is noisy.

### 5.4 Non-functional requirements

- **Latency:** dashboard refresh < 2s. Signal-to-alert < 60s for P0 sources.
- **Accuracy target (v1):** beat Polymarket mid-price by ≥5% **Brier skill score** on resolved crypto/finance markets over a rolling 30-day window, stratified by market-type and time-to-resolution bucket.
- **Availability:** 99.5% uptime. Graceful degradation when individual sources fail.
- **Data retention:** 2 years of market + signal history for backtesting.
- **Point-in-time correctness:** every feature record carries an `observed_at` timestamp distinct from `event_time`. Historical queries (training, backtest, calibration) use as-of semantics — the system MUST NOT be able to read data that did not exist at the asked-about wall-clock time. Edits/retractions (deleted tweets, revised news headlines, UMA re-resolutions) are stored as new rows, never updated in place.
- **Privacy:** user journal and tuning are private by default. No data sold or shared. Aggregate cross-user learning (see §6.8) is opt-in and differentially privatized before use.
- **Security:** API keys encrypted at rest. No custody of Polymarket wallets. Read-only integrations only in v1.

---

## 6. Model & Methodology

### 6.1 Market-type taxonomy

One generic ensemble cannot fit every market shape. PolyPredictor classifies each Polymarket market at ingest time into one of five structural types, each with its own baseline and specialized features. Classification is deterministic where possible (regex / keyword on resolution text) and LLM-assisted with human review for ambiguous cases.

| Type | Examples | Principled baseline | Specialized features |
|---|---|---|---|
| **Threshold** | "BTC > $150k by Jun 30, 2026" | Barrier-crossing probability from realized vol + ATM IV + skew (inverted Black-Scholes). This is the single strongest prior for crypto price-target markets. | Time to expiry, moneyness, implied-vol term structure, skew, realized-vs-implied vol gap |
| **Range** | "BTC between $100k and $120k on Jun 30" | Conjunction of two threshold probabilities with empirically-estimated correlation | Same as threshold + range width relative to 1σ move |
| **Discrete event** | "Fed cuts 25bps at next FOMC", "CPI prints < 2.8%" | CME FedWatch / OIS-implied probabilities; Bloomberg consensus survey for macro prints | Consensus point estimate, dispersion, days-to-release, pre-event IV |
| **Multi-outcome** | "Which L2 hits $10B TVL first?" | Softmax over outcomes, sum-to-one enforced post-hoc | Per-outcome base rates, head-to-head historical comps |
| **Long-tail binary** | "Will Coinbase be hacked in 2026?", "Will MicroStrategy buy more BTC?" | Poisson / base-rate from historical frequency of analogous events | Catalyst count, rate-of-mention on social, sibling-market priors |

A sixth "miscellaneous" bucket catches markets that don't fit; they fall through to the generic ensemble with a low-confidence badge.

### 6.2 Ensemble construction

For each market type, the final probability is a **stacked ensemble**:
1. The type-specific **principled baseline** above produces `p_base`.
2. A gradient-boosted model (LightGBM / CatBoost) takes `p_base` plus all applicable features (see §6.3) and outputs a refined probability `p_raw`.
3. `p_raw` is passed through **isotonic regression** calibrators fit on a held-out, time-ordered slice per market type.
4. The calibrated `p_cal` is then combined with the conformal uncertainty layer (§6.5) to produce the final output: point estimate + prediction interval.

Weights are **learned per market type**, not globally. User-facing sliders in F5 adjust an additive log-odds shift on top of the learned model, not the learned weights themselves — this keeps the model stable under user tuning.

### 6.3 Feature engineering

Features fall into eight families. Every feature is timestamped with `observed_at` for point-in-time correctness (§6.6).

**Market microstructure (Polymarket CLOB)**
- Mid, spread, orderbook imbalance at multiple depths
- 1h / 24h / 7d price momentum and volatility
- **Informed-taker flow:** aggressor-side, spread-crossing trades, size > p80 (trades that *took* liquidity are informational)
- **Passive-maker flow:** inside-spread fills (mostly noise, informational only under imbalance shocks)
- Trade-weighted directional flow with decay

**Participant (Polymarket Data API)**
- Smart-money consensus (weighted by each wallet's rolling PnL and trade count, multi-market requirement to qualify; cross-checked against PnL *and* volume rankings to mitigate wash-trading — §9)
- Holder concentration (Gini-style score); down-weights smart-money signal when concentration > 0.6
- Whale flow delta over last 24h

**Market-implied (external)**
- Options-implied probability from Deribit ATM + skew (primary baseline for threshold markets)
- Perp funding and basis as a directional conviction proxy
- CME FedWatch / OIS for rate-decision markets
- Realized-vs-implied volatility gap

**Social sentiment (structured, not raw polarity)**
The predictive content of sentiment lives in structure, not polarity:
- **Novelty:** semantic dedup vs last 24h — repeated news already priced in is zero signal
- **Source credibility:** per-KOL track record, weighted by past edge contribution (accounts whose calls correlated with subsequent price moves)
- **Reach-adjusted volume:** impressions, not tweet count
- **Dispersion:** stddev of sentiment — high agreement is sometimes a contrarian signal (consensus = priced-in)
- **Tone shift:** delta from rolling baseline, not absolute sentiment
- **Language-model classifier** (tuned encoder, not raw LLM) for headline type: breaking news / opinion / rumor / dev update

**Event-time (scheduled catalysts)**
Scheduled events (FOMC, CPI, NFP, ETF deadlines, protocol unlocks) have reliable pre/post dynamics learned from history:
- Pre-event IV run-up and crush; pre-event Polymarket drift
- Event-window flag (t ± 24h around catalyst)
- Post-event drift window (t+1h to t+24h)
- Consensus surprise (actual minus survey) once released
These are modeled as an **event-time adjustment** layer, not blended into normal features — otherwise event days pollute the non-event feature distribution.

**Regime**
Crypto has distinct regimes (bull trend / bear trend / chop / liquidity crisis). The model tags each day with a regime label (HMM on BTC realized vol + correlation to NASDAQ + stablecoin supply) and the gradient-boosted model receives regime as a feature so it can learn regime-specific residual patterns. Evaluation is also stratified by regime — a model that works in trending markets but breaks in chop is not acceptable.

**Cross-market (sibling & arb)**
- **Sibling-implied prior:** when overlapping markets exist (§F8), the no-arb bounds they impose on this market's probability form a model-free prior. This is often the highest-quality feature available and when present should dominate.
- **External-venue cross-check:** probability implied by Deribit options, perp basis, and related Polymarket markets; divergence is both a signal and a sanity check.

**Resolution-risk**
Score from the F9 classifier; enters the model both as a feature (ambiguous markets behave differently) and as a post-hoc uncertainty multiplier on the output interval.

### 6.4 Uncertainty quantification

Point probabilities are insufficient — users need defensible confidence bands to size correctly. PolyPredictor uses **split conformal prediction**:
- Calibration set: last N resolved markets per type, held out from training, selected with purged/embargoed folds (§6.6) to prevent leakage.
- For each new prediction, compute a nonconformity score (absolute calibrated-probability error) and derive an 80% prediction interval from the empirical quantile of calibration scores.
- Under exchangeability, this gives a **distribution-free coverage guarantee** — the band covers the true outcome 80% of the time regardless of the underlying model.
- Resolution-risk score widens the interval multiplicatively.
- Regime and time-to-resolution are conditioned on (Mondrian conformal) so the coverage holds per-regime and per-bucket, not just on average.

Intervals are displayed prominently. Copy emphasizes: **wide band = don't size regardless of point estimate**.

### 6.5 Transparency & explanations

Every probability comes with:
- **Signal decomposition:** feature contributions via SHAP values — shows *what moved the probability*, not just what data is available.
- **Top-3 drivers** rendered as natural language via a constrained LLM (the LLM sees only the SHAP output and feature values; it cannot invent numbers).
- **Prediction interval** with coverage and Mondrian conditioning (§6.4).
- **Links to raw evidence:** tweets, headlines, on-chain txs, sibling markets, related-venue prices.
- **Resolution-risk badge** when applicable.

Black-box outputs are a non-starter. If the user can't defend the call, they won't size it.

### 6.6 Backtesting & point-in-time correctness

Financial ML is uniquely prone to lookahead bias. PolyPredictor's backtest infrastructure enforces point-in-time correctness end-to-end:

- **Feature store with `observed_at` separate from `event_time`.** All feature reads go through an as-of query API. Historical queries cannot return data that didn't exist at the asked-about timestamp.
- **Immutable history.** Deleted tweets, revised headlines, UMA re-resolutions are stored as new rows with new `observed_at`. The backtest sees what the model would have seen live.
- **Walk-forward evaluation.** Training windows slide forward in time; the model is never trained on data from after the prediction's timestamp. Performance is reported per-window, not just on average.
- **Purged k-fold with embargo.** For cross-validation within a training window, folds are purged of samples whose resolution overlaps the validation period, and embargoed to account for information leakage across close-in-time markets (López de Prado's method).
- **Survivorship-free corpus.** Backtest set includes markets that had low volume, were canceled, or were disputed — not just the clean ones.
- **No retroactive label polishing.** A market's resolution is fixed as of its resolution timestamp. If UMA later revised it, that revision is a separate event.

Backtest outputs (Brier skill score, calibration curve, profit-weighted accuracy) are reported stratified by market-type, regime, and time-to-resolution bucket. Aggregate-only metrics are not acceptable.

### 6.7 Cold start for new markets

Polymarket adds new markets daily; many have zero price history and no direct training analog. Strategy:
1. **Embedding match.** Encode the market title + resolution text + tags; find nearest resolved markets by cosine similarity. Weighted average of their realized outcomes becomes a prior.
2. **Market-type baseline.** Threshold/range markets can be priced immediately from options IV — no market history needed.
3. **Sibling-implied prior** where available (§F8).
4. **Explicit low-confidence badge.** Until the market has ≥50 book snapshots or 24h of data, edge alerts are suppressed and the prediction interval is doubled.

### 6.8 End-of-life convergence

As `time_to_resolution → 0`, the model's probability must formally converge to either (a) the realized outcome if observable, or (b) the market price if not. Mechanism: a monotonically increasing weight on the market price in the last 5% of a market's life, reaching 1.0 at `t = 0`. Without this, last-hour noise creates phantom edges and alerts degrade.

### 6.9 Feedback loops & drift monitoring

- **User-call feedback (opt-in).** Journaled calls + outcomes are a noisy but large label source. Aggregated across consenting users with differential privacy, they enrich the training set — especially for low-resolution markets. Explicit consent required; individual calls never leave a user's account.
- **Rolling Brier / calibration monitor.** A nightly job computes 7d, 30d, and 90d Brier skill score, calibration error, and coverage per market-type and regime. Alerts fire when any metric crosses a threshold (e.g., 30d skill drops below 0 — the model is losing to the market).
- **Feature drift detection.** PSI / KL-divergence on feature distributions vs training; alert when drift > threshold.
- **Per-signal incremental value.** Monthly ablation run measures each signal's contribution (train with/without). Signals whose contribution is within bootstrap CI of zero are archived.
- **Retraining cadence.** Weekly for the gradient-boosted model; daily for isotonic calibrators; on-demand after drift alerts.

### 6.10 Avoiding pitfalls

- **Reflexivity:** Polymarket price is both an input feature and the benchmark. The model must not devolve into "market ± small noise." Regularization: the ensemble's learned weight on `market_mid` is capped, and the principled baseline (§6.1) is computed independently and enters as a separate term.
- **Stale signal:** every source has a decay function; a 4-hour-old tweet weighs less than one from 10 minutes ago.
- **Survivorship:** backtest includes markets that had low volume, were canceled, or disputed (§6.6).
- **LLM hallucination:** LLMs are used for summarization, classification (resolution-risk, market-type, headline-type), and SHAP narration — never for numerical probability estimation.
- **Adversarial flow:** large sudden directional trades uncorrelated with external venues (perps, options, related markets) or social chatter are flagged as candidate manipulation and down-weighted.
- **Regime shift:** evaluation is stratified by regime; a model that only worked in a bull regime gets flagged by the monitoring job before users notice.
- **Data-provider silent corruption:** cross-validate price feeds across Binance / Coinbase / CoinGecko; drop divergent samples.

---

## 7. Technical Architecture (high-level)

- **Frontend:** Next.js 15 + React, Tailwind, TanStack Query. Responsive web, no app.
- **Backend:** Python (FastAPI) for the model + ingestion, Node (serverless) for thin API layer.
- **Data store:** Postgres for user data + journal; ClickHouse (or Timescale) for time-series signal + price history.
- **Streaming:** Redis pub/sub for real-time dashboard updates; websocket to client.
- **Jobs:** scheduled ingestion workers per source; dead-letter queue for failures.
- **ML:** sentiment models hosted on modest GPU or CPU-quantized; ensemble weights in a small regression / gradient-boosted model.
- **Hosting:** Fly.io or Render for early versions; migrate to AWS if scale demands.

APIs that matter: Polymarket (Gamma + CLOB + Data + WSS — see §5.2.1), X API (paid tier required for real-time), Binance/Coinbase public, Deribit public, FRED.

**Polymarket client design**
- Single `polymarket-client` package with three sub-clients (`gamma`, `clob`, `data`) sharing a rate-limit bucket per base URL, so bursts on `/book` don't starve `/positions`.
- Token-bucket limiter sized to 70% of published ceilings (leaves headroom for retries and bursts).
- Response cache keyed by endpoint + params, TTL per endpoint class: `/markets` 60s, `/midpoint` 2s, `/book` 1s, `/holders` 300s, `/v1/leaderboard` 1h.
- WSS manager with auto-reconnect + resubscribe, heartbeat monitoring, and jittered backoff. On reconnect, re-request `initial_dump=true` and reconcile deltas against last-known book.
- All Polymarket responses flow through the same time-series writer into ClickHouse so model features (imbalance, momentum, holder deltas) are backtest-reproducible.

---

## 8. Success Metrics

**North star:** user-realized edge (avg bps above market on marked calls, over 30d rolling), weighted by size committed.

**Model-quality metrics** (stratified by market-type and time-to-resolution bucket — no aggregate-only reporting):
- **Brier skill score** vs Polymarket mid: `1 − BS_model / BS_market`. Target: ≥ 0.05 (5% improvement) on 30d rolling resolved set. Must be positive in every market-type cell, not just in aggregate.
- **Calibration error** (Expected Calibration Error across 10 reliability buckets). Target: < 5%.
- **Conformal coverage.** Target: empirical 80% interval covers the outcome 78–82% of the time, per market-type and per regime (Mondrian coverage, not just marginal).
- **Profit-weighted accuracy.** Only predictions users sized on, weighted by Kelly-suggested size. The metric that correlates with user outcome.
- **Per-signal incremental Brier.** Monthly ablation — contribution of each signal family, with bootstrap CI. Signals with CI crossing zero get archived.

**Product metrics:**
- Time-to-decision per market (target: median < 5 min).
- DAU / WAU ratio (target: > 50% — this should be a daily tool).
- % of sessions ending in a logged call (target: > 40%).

**Guardrail metrics** (alerting):
- Source failure rate (< 1% per source per day).
- False-positive alert rate (< 15% — alerts the user later marks as noise).
- **Model drift alerts.** 30d Brier skill score ≥ 0 across all market-type cells. If any cell goes negative for 7 consecutive days, the model for that cell is disabled and the market-implied prior is shown instead.
- **Feature drift (PSI).** Per-feature PSI vs training distribution; alert at > 0.2.

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| X/Twitter API cost or access changes | High | High | Multi-source design; Reddit + news + Discord can partly substitute. Cache aggressively. |
| Polymarket API changes or rate limits | Medium | High | All three Polymarket APIs fronted by a versioned client with per-endpoint cache TTLs and a 70% rate-limit budget. WSS drops degrade gracefully to polling. Monitor Polymarket changelog; version-pin client release. |
| Smart-money leaderboard gaming (wash trading to rank) | Medium | Medium | Cross-check leaderboard entries against `/v1/leaderboard?orderBy=PNL` *and* `VOL`; require minimum trade count and multi-market presence before a wallet qualifies as smart money. |
| Model underperforms the market | Medium | Critical | Ship with clear "edge not found" messaging; focus v1 on specific market types where we have measurable edge before broadening. Model-drift monitor (§6.9) disables any per-type model with negative 7d Brier skill and falls back to market-implied prior. |
| Lookahead bias / backtest leakage producing phantom edge | Medium | Critical | Point-in-time correctness as a hard NFR (§5.4); walk-forward + purged k-fold with embargo (§6.6); immutable history for revised data. No metric is trusted without a PIT reproduction. |
| Regime shift (bull → bear, liquidity crisis) invalidates model | Medium | High | Regime-tagged features and stratified evaluation (§6.3). Monitor alerts on regime-level Brier degradation. Conformal intervals widen automatically under covariate drift. |
| Adversarial flow / whale manipulation in thin markets | Medium | Medium | Adversarial-flow feature (§6.3) down-weights large directional trades uncorrelated with external venues or social chatter. Thin-liquidity markets below a book-depth threshold are tagged "thin book" and alerts suppressed. |
| Resolution-criteria disputes invalidating predictions | Medium | High | F9 Resolution-Risk Flag; model probability widened for ambiguous markets; edge alerts suppressed above risk threshold. UMA dispute history feeds the classifier. |
| Regulatory pressure on prediction markets / US access | Medium | High | Product is informational, not an intermediary. Geofence nothing, but track legal developments. |
| LLM hallucination in explainers | Medium | Medium | LLMs constrained to summarize signals already flagged by deterministic pipeline. Never invent numbers. |
| On-chain / social data provider costs balloon | Medium | Medium | Free / self-hosted tiers first (Dune free, own X scraper via paid tier only when needed). |
| User over-trusts the tool | Low | High | Confidence bands prominent; calibration plot visible in journal; copy emphasizes "decision support, not signal." |

---

## 10. Milestones & Timeline (tentative)

- **M0 — Polymarket foundations + PIT feature store (Weeks 1–3):** Gamma + CLOB market-data ingestion, WSS market channel, ClickHouse schema with `observed_at` / `event_time` separation, immutable-history writer, as-of query API. Market list UI. No model yet.
- **M1 — Market-type taxonomy + principled baselines (Weeks 4–5):** Classifier for the 5 market types (§6.1). Threshold-market baseline using Deribit IV; FedWatch baseline for rate markets; embedding-match prior for long-tail binary. Display baselines as "model probability" before any ML layer.
- **M2 — Model v0 + conformal UQ (Weeks 6–8):** Gradient-boosted refinement on CLOB features (midpoint, spread, imbalance, momentum, informed vs passive flow). Isotonic calibration. Split conformal prediction intervals with Mondrian conditioning. Market detail view. Journal v0 (manual entries).
- **M3 — Polymarket-native signals (Weeks 9–10):** Data API — smart-money tracker (F6), holder concentration, whale alerts. Sibling/arb checker (F8). Leaderboard refresh job.
- **M4 — Social + event-time signal (Weeks 11–13):** X integration with structured features (novelty, source credibility, dispersion, tone shift). Event-time adjustment layer for scheduled catalysts. Resolution-risk classifier (F9). Signal feed unified across Polymarket, social, and catalysts.
- **M5 — On-chain + macro (Week 14):** Glassnode/Dune, FRED, FOMC/CPI calendar integrated into event-time layer.
- **M6 — Backtest + drift monitoring (Weeks 15–16):** Walk-forward backtest with purged/embargoed k-fold, survivorship-free corpus, per-cell stratified reports. Drift-monitoring job (Brier, calibration, PSI). Per-signal ablation job. Regime tagger.
- **M7 — Journal auto-sync + user tuning (Week 17):** User WSS channel, journal auto-sync (F7), backtest UI, weight tuning (additive log-odds shifts), calibration plot.
- **M8 — Closed beta (Weeks 18–19):** ~20 users, crypto-only first, stress-test the drift-monitoring loop. End-of-life convergence validated on live markets.
- **v1 launch:** when 30-day Brier skill score ≥ 0.05 is positive in **every** market-type cell (not just aggregate), with Mondrian conformal coverage 78–82%, on resolved crypto/finance markets.

---

## 11. Open Questions

1. Pricing model — flat subscription, usage-based on alerts, or free with paid advanced signals? (Leaning: $30–$80/mo subscription, single tier.)
2. Should v1 include a paper-trading mode that auto-logs calls without user action? (Probably yes, as a toggle.)
3. Do we scrape X directly or pay for API access? Cost vs ToS risk tradeoff.
4. How do we handle markets with < $1k liquidity — exclude, or show with a "thin book" tag?
5. Does the model output a single probability, or a distribution (for continuous markets like "BTC close price")?
6. Is Discord integration worth the support burden given per-server auth complexity? (Lean: defer to v1.1.)

---

## 12. Glossary

- **Edge:** absolute difference between model probability and market price, in basis points (bps).
- **Brier score:** mean squared error of probabilistic predictions; lower is better.
- **Brier skill score:** `1 − BS_model / BS_market`. Positive means the model beats the market; 0.05 is our v1 bar.
- **Calibration (ECE):** whether a model's 70% predictions actually resolve YES 70% of the time. Expected Calibration Error is the weighted average deviation across reliability bins.
- **Isotonic regression:** a monotone post-hoc calibration layer that maps raw model outputs to calibrated probabilities using a held-out set.
- **Conformal prediction:** a distribution-free framework that produces prediction intervals with a coverage guarantee under exchangeability. We use split conformal.
- **Mondrian conformal:** conformal intervals conditioned on a categorical variable (market-type, regime) so coverage holds per-cell, not just marginally.
- **Point-in-time (PIT) correctness:** historical queries return only the data that existed at the asked-about wall-clock time — the cornerstone of leakage-free backtesting.
- **Purged k-fold / embargo:** cross-validation scheme that removes training samples whose resolution overlaps the validation period and adds a buffer to prevent leakage across close-in-time markets (López de Prado).
- **Walk-forward evaluation:** training windows slide forward in time; the model never trains on data from after the prediction timestamp.
- **Market-type:** the structural form of a Polymarket market (threshold, range, discrete event, multi-outcome, long-tail binary). Determines the principled baseline used.
- **Regime:** crypto market state (bull, bear, chop, liquidity crisis). Model is evaluated per-regime, not just in aggregate.
- **Resolution-risk:** score for how likely a market is to resolve ambiguously or contentiously; widens the prediction interval and suppresses edge alerts.
- **Sibling market / no-arb prior:** overlapping markets (different thresholds, dates) whose prices impose bounds on each other; violations yield arb opportunities and model-free priors.
- **Informed-taker flow:** spread-crossing, aggressor-side trades above a size percentile — the portion of order flow that carries directional information.
- **PSI (Population Stability Index):** a feature-drift metric comparing live distribution vs training distribution.
- **Kelly size:** optimal position size given edge and bankroll; we cap at fractional Kelly.
- **Polymarket CLOB:** Polymarket's central limit order book API (`clob.polymarket.com`) — orderbook, prices, trades.
- **Polymarket Gamma API:** Polymarket's catalog API (`gamma-api.polymarket.com`) — markets, events, tags, search.
- **Polymarket Data API:** Polymarket's analytics API — positions, leaderboard, holders, earnings.
- **Condition ID:** 0x-prefixed 64-hex identifier for a Polymarket market (used to join across the three APIs).
- **Token ID (asset ID):** identifier for a specific outcome token within a market (YES vs NO have distinct token IDs); the subscription key for the CLOB WebSocket market channel.
- **Proxy wallet:** the 0x address Polymarket uses to represent a user on-chain; the key for `/positions`, `/trades`, `/holders` lookups.
- **Holder concentration:** share of an outcome token held by the top N wallets — a proxy for how exit-friendly a position is.
- **Reflexivity:** when the price of the thing being predicted influences the thing itself.
