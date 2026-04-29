# PolyPredictor — Codebase Analysis

_Generated 2026-04-28. Scope: full repository, including `apps/web`, `services/api`, `services/ingest`, `packages/model`, `packages/polymarket-client`, infra (Docker, render.yaml, Makefile, GitHub Actions), and supporting docs. Excludes `node_modules` and other generated artifacts._

> **2026-04-29 update — critical items 3–6 resolved.** Spot-checking the original audit's claims against the live code revealed that fixes #3, #4, #5 were already in place (the audit was reading stale evidence). Item #6 was partially complete; the remaining stack-hash dedupe and `X-Forwarded-For` handling have now been added with test coverage. See §12 for resolution detail.

---

## TL;DR

PolyPredictor is a serious, well-scoped product with strong fundamentals: a clear PRD, milestone-driven `tasks.md`, comprehensive ruff/mypy-strict tooling, ~95 Python test files, and a clean point-in-time storage architecture (Postgres + ClickHouse + Redis with `observed_at` discipline). The Polymarket HTTP client (`packages/polymarket-client`) is the cleanest module in the repo — token-bucket rate limiting, retry/backoff, conditional caching, typed errors.

But there's a cluster of **critical-severity issues that block production**, plus pervasive deployment gaps that mean most of the M1+ feature work is dark in staging:

1. **No authentication on any credential/admin/journal/billing route.** Magic-link auth exists but isn't wired into a session — every "secured" endpoint runs as the demo user.
2. **Stripe webhook signature is optional.** With `STRIPE_WEBHOOK_SECRET` unset, anyone can POST `/v1/billing/webhook` and mark any email as `active`.
3. **`USER_SECRET_ENCRYPTION_KEY_B64` silently falls back to a 32-byte zero key** outside `APP_ENV=production`. Combined with pydantic-settings `extra="ignore"`, a typo in the env var name will boot prod with trivially-broken AES-GCM.
4. **Most ingest workers never run in production.** `render.yaml` deploys only `gamma_discovery` and `clob_poller`; the 20+ workers behind `make ingest-*` (smart-money, microstructure, RSS, X, Reddit, Glassnode, Dune, Deribit, drift monitor, EOL monitor, etc.) are dark — meaning the M2/M3/M4 features they feed are dead in prod.
5. **`/v1/error-reports` is an unauthenticated, unrate-limited DOS vector** auto-fired by every browser-side rejection.
6. **Glassnode API keys are sent as URL query parameters** and end up in DLQ payloads via `repr(exc)`.

Everything else — god objects, design-system fragmentation, dead modules, test gaps — is real but secondary. The critical list above is what should be fixed before any external user touches the system.

---

## 1. Architecture Overview

```
polypredictor/
├── apps/web/                 Next.js 15 App Router, React 18, @tanstack/react-query
├── services/
│   ├── api/                  FastAPI backend (74 modules, 67 test files)
│   └── ingest/               Async ingestion workers (34 workers, 27 test files)
├── packages/
│   ├── model/                Probabilistic baselines, ensemble, conformal, regime
│   └── polymarket-client/    Async Polymarket SDK (Gamma, CLOB, Data, WSS)
├── docs/                     Decision logs + runbooks (data retention, on-call, LLM guardrails)
├── prd.md                    Product spec (Draft v0.3)
├── tasks.md                  Milestone-driven task list (M0–M8)
├── docker-compose.yml        Local dev: PG + CH + Redis + api + web + 2 workers
├── render.yaml               Staging deploy: api + web + 2 workers + PG + Redis
└── .github/workflows/        CI (lint+test+build), deploy hooks
```

**Storage split:**
- **Postgres** (asyncpg, pool 1–5 connections — ⚠ undersized): users, auth_magic_links, journal_calls, tuning_profiles, push/privacy/paper prefs, beta invites, error_reports, classification_review, dp_label_aggregates, billing_subscriptions, magic_links, provider_credentials. 10 forward-only migrations.
- **ClickHouse** (clickhouse-connect, async): all time-series with mandatory `observed_at` + `event_time` columns, ReplacingMergeTree for idempotency, 2-year TTL. 17 migrations.
- **Redis**: cache + DLQ + rate-limit budgets.

**External APIs:** Polymarket Gamma/CLOB/Data/WSS, Deribit, CME FedWatch (OAuth), Binance/Coinbase/CoinGecko, FRED, BLS, Glassnode, Dune, X (Twitter), Reddit, Stripe.

**Modeling pipeline:** Per-market-type baselines (`threshold` via Black-Scholes inversion, `range`, `discrete_event` via FedWatch, `multi_outcome` softmax, `long_tail_binary` Poisson + embedding-match) → `ensemble.py` blends with per-type registry weights → `conformal.py` adds split-conformal prediction intervals → `regime.py` adjusts for market regime.

The architecture is sound; the problems are at the seams.

---

## 2. Critical Issues (block production)

### 2.1 Authentication is structural, not enforced
**`services/api/src/api/auth.py:174–198`** issues + verifies magic-link tokens correctly (race-free atomic `UPDATE…RETURNING` at `auth.py:117–123` — verified). But verification only returns the email; **there is no session, no JWT, no cookie, no `Depends(current_user)` anywhere in the route layer.**

Every "secured" endpoint resolves identity by calling `users.ensure_demo_user(...)` (`users.py:12–26`), which always returns the same demo user. That means the following are world-writable:

| Route | File:line | Impact |
|---|---|---|
| `PUT /v1/polymarket-clob-credentials` | `main.py:1270` | Overwrites the demo user's CLOB keys |
| `PUT /v1/provider-credentials/{provider}` | `main.py:1386` | Overwrites Glassnode/Dune/X/Reddit keys |
| `POST /v1/admin/model/{type}/disable` | `main.py:1788` | Disables the model for a market type |
| `POST /v1/admin/classification-review/{id}` | `main.py:1761` | Approves/rejects market classifications |
| `POST /v1/journal/calls` | `main.py:1573` | Creates trades in the demo user's journal |
| `POST /v1/billing/checkout` | `main.py:1891` | Initiates checkout for an arbitrary email |
| `GET /v1/waitlist` (admin) | `main.py:1853` | Reveals waitlist contents |

**Fix:** issue a session token (signed cookie or JWT) in `verify_magic_link`, add `Depends(get_current_user)` to every credential/admin/journal/billing route, and make `ensure_demo_user` test-only.

### 2.2 Magic-link token leaks in HTTP response body
**`auth.py:48`** — `MagicLinkIssued.token: str` is returned by `POST /v1/auth/magic-link`. The comment at `auth.py:45–47` acknowledges this is an in-band shortcut for "internal use." In production this is a credential leak; if a user's email account is later compromised, every issued token is recoverable from server logs / proxy logs / browser history.

**Fix:** delete `token` from the response model; require email delivery for production (add a `MAGIC_LINK_DELIVERY=email|stdout` setting, default `email` in production).

### 2.3 Encryption key falls back to all-zeros — ✅ RESOLVED
~~Earlier audit claim:~~ when `app_env != "production"`, `_get_encryption_key()` returns `b"0" * 32`.

**Actual state (verified 2026-04-29):** `clob_credentials.py:153–171` already raises `ValueError` when the key is missing, invalid base64, or the wrong length. `validate_secret_encryption_key` (line 167) skips validation only in `{development, test, local}` — every other `app_env` value, including `production`, hard-fails. `main.py:64` calls it at the top of the lifespan handler before any state is initialized. The `b"0" * 32` fallback no longer exists. The `extra="ignore"` typo concern in `settings.py:15` remains valid as a separate hardening item — see §4.4.

### 2.4 Stripe webhook signature is optional — ✅ RESOLVED
**Actual state (verified 2026-04-29):** `billing.py:228–232` requires the secret unconditionally — `if not secret: raise ValueError("STRIPE_WEBHOOK_SECRET must be configured")` followed by `if not _verify_stripe_signature(...): raise ValueError("invalid Stripe webhook signature")`. The earlier-audited `if secret and not _verify(...)` shape no longer exists. The "billing reads env directly" concern doesn't apply to webhook handling — `handle_stripe_webhook` reads `settings.stripe_webhook_secret` via the typed Settings object. The mock-checkout path at `billing.py:128–143` still falls back to a deterministic mock when `STRIPE_SECRET_KEY` is absent, which is the intended dev/staging behavior.

### 2.5 Glassnode API key in URL query — ✅ RESOLVED
**Actual state (verified 2026-04-29):** `onchain_metrics.py:296–314` already passes the API key via `headers={"X-API-KEY": api_key}` and only uses query params for `a` (asset), `i` (interval), `f` (format). The Dune client at line 325 uses `X-Dune-API-Key` header. No leakage path through `repr(exc)` or DLQ payloads.

### 2.6 `/v1/error-reports` is an unauthenticated DOS vector — ✅ RESOLVED
**Actual state (verified 2026-04-29):**
- **Per-IP rate limit** — `_MinuteRateLimiter` at `main.py:120–138`, default 30 req/min via `error_reports_rate_limit_per_minute` setting. Already in place.
- **Payload size cap** — `main.py:255–262` rejects with HTTP 413 when `Content-Length > error_reports_max_body_bytes` (default 64 KB). Already in place.
- **Stack-hash dedupe** — added 2026-04-29. `_StackDedupe` at `main.py:140–164` keeps a TTL set of stack fingerprints (default 1 hour via `error_reports_dedupe_window_seconds`). The handler at `main.py:300–318` computes a SHA-256 fingerprint of the stack (or `source|severity|message|url` if no stack) and short-circuits with `{"deduped": true}` on repeat. Lazy eviction caps the dict at ~4096 entries.
- **Client IP from `X-Forwarded-For`** — added 2026-04-29. `_client_ip()` at `main.py:170–183` reads the first comma-separated entry of `X-Forwarded-For` before falling back to `request.client.host`. Without this, every request on Render appeared to come from the load-balancer IP and the rate limiter was effectively global.
- **Tests** — `services/api/tests/test_error_reports.py` extended with 8 new cases covering dedupe behavior, window=0 disables dedup, empty key never dedupes, fingerprint stability with/without stack, IP precedence, plus two end-to-end TestClient tests for the deduped repeat and the 413 oversize path.

The remaining Sherman-Williams concern is per-process state: `_MinuteRateLimiter` and `_StackDedupe` are in-memory and won't coordinate across uvicorn workers. Tracked as part of §3.1.

### 2.7 ClickHouse insert failures silently lose data
**`services/ingest/src/ingest/clickhouse.py:90–100`** — `BatchInserter.flush` catches all exceptions, logs, and **drops the batch**. The comment promises "DLQ will capture truly broken payloads at the caller level" but **no caller actually pushes failed CH inserts to a DLQ**. Net result: a 30-second ClickHouse outage = 30 seconds of silently-dropped data across every worker that uses the inserter.

**Fix:** push failed batches to the same DLQ that handles individual record failures, with a `batch_failure` discriminator. Add a metric.

### 2.8 Most ingest workers never run in production
**`render.yaml`** deploys: `api`, `web`, `ingest-discovery` (gamma), `ingest-clob`. **Nothing else runs**. The Makefile lists 20+ `make ingest-*` targets that only execute when a human types them.

Workers that exist in code, have tests, but **never run in staging**:
- `feature_snapshots` (M2 model input — critical)
- `smart_money_refresh`, `smart_money_qualify`, `holders_poller`, `concentration`, `whale_flow` (M3 Polymarket-native signals)
- `microstructure_signals`, `arb_checker`, `external_divergence` (M3 alpha signals)
- `rss_ingest`, `reddit_ingest`, `x_ingest` (M4 social/news)
- `scheduled_events_ingest`, `deribit_iv_surface`, `spot_price_validation`, `macro_series_ingest`, `perp_funding_basis`, `onchain_metrics` (M4/M5 macro/onchain)
- `wss_bridge` — **the WebSocket reconciliation worker doesn't run anywhere**, neither in compose nor render
- Periodic jobs: `drift_monitor`, `signal_ablation`, `eol_monitor`, `decision_time_monitor`, `resolution_risk_corpus`, `dp_aggregates`, `kol_credibility`, `alert_outcome_audit`, `m1_audit`–`m8_audit`, `ensemble_retrain`

**Fix:** add Render `worker` services for the long-running workers, `cron` services for the periodic audits, and corresponding `RENDER_DEPLOY_HOOK_*` secrets in `.github/workflows/deploy-staging.yml`. Without this, the M1–M8 exit criteria in `tasks.md` are physically unverifiable.

---

## 3. High-severity issues

### 3.1 In-memory state breaks horizontal scaling
- `app.state.journal_auto_sync`, `app.state.ch`, `app.state.pg` (`main.py:73–82`) are per-process. `update_polymarket_clob_credentials` (`main.py:1289`) and `update_provider_credentials` (`main.py:1403`) refresh the auto-sync only on the handling worker — a second uvicorn worker is stale until restart.
- `cme_fedwatch.py:73` — in-process token + result cache (`self._cache: dict[...]`). No Redis fallback. Token churn under load.
- `paper_trading.py` (model package) — module-level mutable global `_paper_trading_enabled`, no `Final` annotation, no persistence.

**Fix:** move shared state to Redis. Use a pub/sub channel (or short TTL keys) for credential-refresh fan-out.

### 3.2 Postgres pool max_size=5 will starve
**`services/api/src/api/postgres.py:15`** — `min_size=1, max_size=5`. With billing webhooks, journal POSTs, dashboard polling at 5 s intervals, and the lifespan-attached auto-sync service all sharing this pool, expect `asyncpg.exceptions.PoolTimeoutError` under any real concurrency.

**Fix:** raise to ~20, expose as `POSTGRES_POOL_MAX_SIZE` setting.

### 3.3 Tables created at request time
**`billing.py:112` and `paper_trading.py:47`** both call `_ensure_table` at request time, racing with `migrations.py:59` which auto-applies migrations on startup.

**Fix:** add the tables as new idempotent migrations under `services/ingest/migrations/postgres/` and delete the runtime `CREATE TABLE`.

### 3.4 `clob_poller.py` has no try/except in the main loops
**`services/ingest/src/ingest/workers/clob_poller.py:108–147`** — `_top_loop` and `_tail_loop` `gather` per-token poll calls without a top-level `try/except`. A `_persist` failure (line 70) propagates up and wedges the entire concurrent batch via `return_exceptions=False` (line 125). No backoff; if ClickHouse is down, every iteration hammers it again immediately.

**Fix:** wrap loop bodies in `try/except`, add bounded exponential backoff (`asyncio.sleep(min(60, 2**fail_count))`), reset on success.

### 3.5 No graceful shutdown in any long-running worker
None of `gamma_discovery`, `clob_poller`, `wss_bridge`, `x_ingest` register `SIGTERM` handlers. Docker / Render send SIGTERM on shutdown — workers die ungracefully, in-flight DLQ pushes lost, partial batches dropped.

**Fix:** add a single `_install_signal_handlers()` helper in `services/ingest/src/ingest/__init__.py`, call from each worker's entrypoint, set an `asyncio.Event` and let loops drain on the next iteration.

### 3.6 `error_reporting.py` is theatrical
**`services/ingest/src/ingest/error_reporting.py`** (1.5 KB) defines `report_worker_error`. **Zero workers call it.** The Postgres `error_reports` table is fed only by the frontend via `/v1/error-reports`.

**Fix:** wire `report_worker_error` into `gamma_discovery`, `clob_poller`, `wss_bridge`, and the long-running ingest workers' top-level exception handlers, or delete it.

### 3.7 Polymarket client doesn't retry 429
**`packages/polymarket-client/src/polymarket_client/http.py:209,217`** — `PolymarketRateLimitError` is raised directly with the parsed `retry-after`, but the retry loop never catches it. PRD §7 implies 429 should respect retry-after and retry up to `max_retries`.

**Fix:** in the retry loop at `http.py:249`, catch `PolymarketRateLimitError`, `await asyncio.sleep(retry_after_s)`, continue.

### 3.8 WSS book-hash drift detection is documented but not implemented
**`packages/polymarket-client/src/polymarket_client/wss.py:294–308`** — the docstring at lines 85–90 promises drift detection: on reconnect, compare the post-`initial_dump` book hash against `_BookState.last_hash` and warn on divergence. `last_hash` is updated on every message (line ~308) but **never read or compared**. Drift is invisible.

**Fix:** implement the comparison. Also at `wss.py:227–228`, `await asyncio.gather(*pending, return_exceptions=True)` after cancellation so reconnect storms don't leak tasks.

### 3.9 Math overflow risk in threshold baseline
**`packages/model/src/model/baselines/threshold.py:222,226`** — `math.exp(2*mu_hat*a)` raises `OverflowError` for extreme `r * T` with tiny `sigma`, before the `np.clip` at line 231 can save it. This is a real CPython bug, not theoretical: a long-dated threshold market with a near-zero implied vol will trip it.

**Fix:** swap `math.exp` for `float(np.exp(...))` (numpy returns `inf`, which the existing clip handles correctly).

---

## 4. Architecture & code-quality debt

### 4.1 God objects in `services/api`
| File | LOC | Notes |
|---|---|---|
| `main.py` | **1,872** | 52 inline route handlers, all glued to the `app` singleton. Only `auth_q.router` (line 115) is mounted via `APIRouter`. |
| `markets.py` | **1,951** | 30 module-level helpers (lines 162–818) + `list_markets` (1039) + `model_for_market` (1546), each several hundred lines. Holds Pydantic models, baselines composition, sibling-arb logic, concentration blending, smart-money rollup. |
| `journal.py` | 531 | Mixes manual call creation, CLOB auto-fill, summary aggregation, decimal/Brier/Kelly math, and confidence buckets. |
| `signal_ablation.py` | 687 | Library + CLI (`_main()` at line 730). |
| `drift_monitor.py` | 470 | Library + CLI (`_main()` at line 501, `main()` at 510). |
| `m1_audit.py` … `m8_audit.py` | 215–506 | All have `_main()`/CLI shape; **none are routed** in `main.py`. They live in the package as if they were endpoints but execute only via `make m{n}-audit`. |

**Routes that belong in their own router** (currently in `main.py`):
- `/v1/billing/*` (1885–1944) → `billing.py` `APIRouter`
- `/v1/journal/*` (1541–1659) → `journal.py` `APIRouter`
- `/v1/polymarket-clob-credentials`, `/v1/provider-credentials/*` (1256–1404) → `provider_credentials.py`
- `/v1/admin/*` (1703–1812) → new `admin.py` router (with auth dependency)
- `/v1/eol-monitor`, `/v1/decision-time-metrics`, `/v1/source-failure-audit` (1994–2062) → diagnostics router; today they import `dataclasses.asdict` and `math` *inside the handler* (lines 2000–2001, 2027, 2051) — a smell of a hot-fix that never got cleaned.

**Fix order:** start with billing (smallest, most security-sensitive), then journal, then credentials. Target `main.py` < 800 lines, lifespan + `app = FastAPI(...)` + `app.include_router(...)` only.

### 4.2 `markets.py` decomposition
Suggested split into:
- `markets/baselines.py` — per-type baseline composition
- `markets/sibling_arb.py` — sibling/multi-outcome logic (currently inline)
- `markets/concentration.py` — holder/concentration helpers (already exists as a module — collapse the duplicate logic in `markets.py`)
- `markets/list.py` — `list_markets` and `MarketListRow`
- `markets/model.py` — `model_for_market` and `MarketModelDetail`
- `markets/history.py` — `MarketHistoryPoint` and the time-series query

### 4.3 Frontend design system fragmentation
The new `apps/web/src/app/page.tsx` (~2,500 lines) and `apps/web/src/components/Settings.tsx` (~2,000 lines) use inline styles + `var(--green/--violet/--amber/--bg/--surface/...)` tokens. Every other route uses Tailwind with a different palette (`slate-*`, `sky-*`, `emerald-*`, hardcoded `bg-[#0b0d10]`).

| Route | Skin | Gap |
|---|---|---|
| `/landing` | `gray-950` + `sky-*` | Large |
| `/signup` | `sky-*` Tailwind, plain inputs | Large |
| `/markets/[conditionId]` | 1,000+ lines Tailwind, **duplicates** `DetailPanel` from `page.tsx:1441+` | **Largest — dual implementation** |
| `/status` | `bg-[#0b0d10]` + `slate-*` | Medium |
| `/privacy`, `/terms` | Same `slate-*` skin | Small/medium |
| `/global-error.tsx` | `slate-*`/`emerald-*`, raw `<a>` for navigation | Medium |

**Fix:** lift `--bg/--surface/--green/--amber/--red/--violet/--muted` into Tailwind theme tokens (or a single CSS file consumed by both inline and Tailwind paths). Pick one detail-panel implementation — recommend deleting `markets/[conditionId]/page.tsx` and routing to the dashboard split-pane instead.

### 4.4 Configuration discipline
- `services/api/src/api/settings.py:15` — `extra="ignore"` silently drops typo'd env vars. Set `extra="forbid"` and ride the boot failures during dev.
- `settings.py:26` — `postgres_password: str = "polypredictor_dev"` is a real default in source. Should be `None` with a startup assertion when `app_env == "production"`.
- `settings.py:159–161` — `get_settings()` is `lru_cache(maxsize=1)`. Combined with `billing.py` reading env directly, settings changes during tests don't propagate consistently.
- `settings.py:143,148` — migration paths default to relative paths. Works in docker, breaks if cwd changes.

### 4.5 Each Dockerfile copies the other service's source
- `services/api/Dockerfile:18` copies `services/ingest`. Wasted layer + larger image; `uv sync --package polypredictor-api` does not require it.
- `services/ingest/Dockerfile:17` copies `services/api` symmetrically.

`apps/web/Dockerfile` is single-stage on `node:20-slim` and ships dev deps + pnpm cache + source. A multi-stage build with Next's `output: "standalone"` should drop image size by ~60% and shave cold-start time.

---

## 5. Model correctness review

The probabilistic core is generally well-coded. Spot-checks confirmed:
- `ensemble.py:303,379` — `expit(score)` correct, `_safe_logit` (line 822-826) clips before `log` — no log-of-zero.
- `conformal.py:305` — `rank = ceil((n+1)*coverage) - 1` is the standard split-conformal upper-quantile.
- `regime.py:158–164` — `_scaled_confidence` is mathematically robust.
- `backtest.py:268–272,402` — `purged_embargo_splits` correctly purges by `asked_at` ± window/embargo. **No look-ahead leak observed in the backtest path.**

Soft issues:
- `regime.py:206` — `realized_vol_24h = abs(last_return) * sqrt(365)` is a 1-day annualized vol from a single observation; very noisy. Defaults at line 56 (`crisis_realized_vol=1.20`) need that noisiness, but document it.
- `regime.py:404` — HMM `_softmax_chosen` formula (`0.5+0.45*(posterior-1/N)`) caps at 0.84 with 4 states, never reaches the 0.95 cap. Probably intentional, but worth a comment.
- `ensemble.py:514–518` — split-index logic is hard to read; for `len=8, min_cal=12` it falls into the "use full set for both" branch. The math is consistent but the branching reads like a hot-fix.
- `conformal.py:303` — silent `0.5` half-width fallback when no calibration scores. Returning `[0,1]` interval = effectively no signal. Acceptable but emit a `sample_count=0` flag in the registry so the consumer can decide.

Hardcoded magic numbers that should be config: `ensemble.py:_EPS=1e-6`, retry/scheduler params (`ensemble.py:497–502`), all of `regime.py:_HMM_PROTOTYPES` / `_HMM_TRANSITIONS` (lines 246–295). PRD §6.3 says regime thresholds should be settings-driven.

---

## 6. Test coverage gaps

**`services/api`** — tested 67/74 modules. Gaps (no `test_*.py`):
- `billing.py` — **no test**, despite parsing webhooks and being v1-launch-critical
- `paper_trading.py` — **no test**
- `provider_credentials.py` — **no test**
- `users.py` — **no test** (this is the auth-boundary module)
- `postgres.py`, `clickhouse.py` — no test (config drift would silently break prod)
- `journal_autosync.py` — module has a test but the lifespan-wired service (`main.py:73`) isn't covered

**`services/ingest`** — 26 worker files, 27 test files. **Untested workers:**
- `wss_bridge.py` — highest-risk untested worker; handles untrusted external payloads and has no DLQ
- `clob_poller.py` — production-critical long-lived process

**`packages/model`** — coverage on the heavy modules (ensemble has 3 test files, conformal has 2) but missing for `pipeline.py`, `arb_checker.py`, `cost_watch.py`, `decision_time.py`, `signal_feed.py`, `liquidity.py`, `metrics.py`, `privacy_utils.py`, `smart_money.py`, `journal_utils.py`, `security_utils.py`, `explainer.py`, `alert_metrics.py`, `regulatory_watch.py`, `distribution_utils.py`, `headline_classifier.py`, `kol_lists.py`, `sentiment_lexicon.py`, `structured_sentiment.py`, `ablation_metrics.py`, `resolution_metrics.py`, `regulatory_watch.py`. A few of these are tiny re-exports; others (`headline_classifier.py` at 12 KB, `structured_sentiment.py` at 7.5 KB) are real model code.

**`packages/polymarket-client`** — has `test_http.py`, `test_cache.py`, `test_rate_limiter.py`, `test_wss.py`, `test_gamma.py`, `test_recorded_fixtures.py`. **No `test_clob.py`, `test_data.py`, or `test_client.py`.** WSS reconnect / heartbeat / book-hash paths are typically thin in `test_wss.py`.

**`apps/web`** — **no tests at all.** No Vitest, no Playwright, no Jest. The `pnpm` workflow runs only `lint`, `typecheck`, `build` in CI.

---

## 7. Dead / abandoned code

- **`_smoke_dual_order.py`** at repo root — leftover scaffolding from a ClickHouse 24.8 dual-`ORDER BY` query incident. Move to `scripts/` or delete.
- **`packages/model/src/model/paper_trading.py`** — re-exported from `model/__init__.py` and shadowed by the real `services/api/src/api/paper_trading.py`. The model-side version is never imported outside `__init__.py`. Dead.
- **`packages/model/src/model/feature_aggregator.py`** — exposes `aggregate_features`, only re-exported, no consumers. Dead. Both files use legacy `typing.Dict/Mapping/Any` and contain LLM-placeholder docstrings.
- **`services/ingest/src/ingest/error_reporting.py`** — defined but called by zero workers. Either wire or delete (see §3.6).
- **`services/api/src/api/clickhouse.py:14`** — sync `_sync_client()` lives alongside the async one. Routes use only the async client. Dead.
- **`services/api/src/api/m1_audit.py`–`m8_audit.py`** — all have `_main()` / CLI shape; none are routed in `main.py`. They're CLI tools mis-located inside the package. Either expose as `/v1/admin/audit/{m}` (with auth) or move to `scripts/`.
- **`apps/web/src/lib/api.ts:914,941`** — `fetchEolMonitor` and `fetchDecisionTimeMetrics` are exported but unused by any component. Either wire them into the Diagnostics panel in `Settings.tsx` or remove.

---

## 8. CI / deployment gaps

**`.github/workflows/ci.yml`** is solid: Python (`uv sync` → ruff check + ruff format check + mypy + pytest) and Web (lint + typecheck + build). What's missing:
- **No coverage upload** (`pytest --cov`, codecov).
- **No web tests** — no Vitest/Playwright/Jest setup at all.
- **No Docker build smoke test.** A regression in any Dockerfile only surfaces on Render.
- **No security scan** — no `pip-audit`, `npm audit`, or `trivy`.
- **No DB migration test.** SQL files in `services/ingest/migrations/` never run in CI; a syntax error there only surfaces on first staging deploy.
- **No `pre-commit` job.** Local committers can `--no-verify` past the hooks.

**`render.yaml`** gaps:
- Web service has **no `healthCheckPath`** (line 60–72). Add `/` or expose `/api/healthz` via a Next route.
- **No `preDeployCommand`** to run migrations. Today migrations auto-apply on API startup (`main.py:65–71`), so a bad migration crashes the boot loop instead of failing the deploy. Move to `preDeployCommand: uv run python -m api.migrations` so failures are visible before traffic shifts.
- **No `cron` jobs** for the periodic audits, drift monitor, EOL monitor, decision-time monitor (see §2.8).
- `CORS_ALLOW_ORIGINS` is not exported (`settings.py:20` will default to `localhost:3000`, blocking the staging web service's browser fetches).

---

## 9. Prioritized remediation plan

Severity scale: **🔴 critical** (blocks production) · **🟡 high** (production-readiness) · **🟢 medium** (quality/debt).

### 🔴 Before any external user
1. Wire session auth: issue cookie/JWT in `verify_magic_link`, add `Depends(current_user)` to every credential/admin/journal/billing route. (`auth.py`, `main.py:1256–1812`)
2. Drop `token` from the magic-link response body. (`auth.py:48`)
3. ✅ ~~Encryption-key hard-fail.~~ Already done — `clob_credentials.py:153–171`, lifespan call at `main.py:64`. (Optional follow-up: flip `settings.py:15` to `extra="forbid"` so typo'd env vars fail loudly.)
4. ✅ ~~Stripe webhook signature mandatory.~~ Already done — `billing.py:228–232`.
5. ✅ ~~Glassnode key in header, not URL.~~ Already done — `onchain_metrics.py:306`.
6. ✅ Rate-limit + size-cap + stack-hash dedupe on `/v1/error-reports`. Resolved 2026-04-29. `main.py:140–164, 170–183, 300–318`; tests in `test_error_reports.py`.

### 🟡 Production-readiness (next 1–2 sprints)
7. Deploy the missing ingest workers as Render `worker` services, add Render `cron` for the periodic audits, add corresponding deploy-hook secrets in `deploy-staging.yml`.
8. Add try/except + bounded backoff to `clob_poller._top_loop`/`_tail_loop`, and a `SIGTERM` handler shared across the long-lived workers. (`clob_poller.py:108–147`, new `services/ingest/src/ingest/__init__.py:_install_signal_handlers`)
9. Push failed CH batches to the DLQ instead of dropping them. (`clickhouse.py:90`)
10. Bump the Postgres pool to ~20 and expose as a setting. (`postgres.py:15`)
11. Replace runtime `_ensure_table` calls with proper migrations. (`billing.py:112`, `paper_trading.py:47`, new migrations)
12. Honor 429 retry-after in the Polymarket client. (`http.py:209,249`)
13. Implement the WSS book-hash drift check; await pending tasks after cancel. (`wss.py:227,294–308`)
14. Replace `math.exp` with `np.exp` in the threshold baseline. (`threshold.py:222,226`)
15. Add a multi-stage `apps/web/Dockerfile` with Next.js standalone output.
16. Add `healthCheckPath` and `preDeployCommand` to `render.yaml`; export `CORS_ALLOW_ORIGINS`.

### 🟢 Code quality / cleanup (continuous)
17. Split `main.py` into `APIRouter`s, starting with `billing`, then `journal`, then credentials, then admin.
18. Decompose `markets.py` along the lines proposed in §4.2.
19. Extract design tokens into Tailwind theme; pick one detail-panel implementation (recommend deleting `markets/[conditionId]/page.tsx`).
20. Move `m1–m8 audit` modules to a `scripts/` package or expose as authenticated `/v1/admin/audit/{m}` endpoints.
21. Delete dead code: `_smoke_dual_order.py`, `model/paper_trading.py`, `model/feature_aggregator.py`, `clickhouse._sync_client`, unused `lib/api.ts` exports.
22. Wire `error_reporting.report_worker_error` into long-running workers, or delete it.
23. Memoize `MarketCard`; lazy-load `Settings.tsx` and `DetailPanel` via `next/dynamic`.
24. Fix the `useEffect([liveTs])` interval thrash in `apps/web/src/app/page.tsx:2236–2239`.
25. Add per-mutation `onError` UI in `Settings.tsx` (currently silent for `saveAddress`/`saveClob`/`savePushPrefs`/`savePrivacy`/`savePaper`).
26. Add tests for `billing.py`, `paper_trading.py`, `provider_credentials.py`, `users.py`, `wss_bridge.py`, `clob_poller.py`. Add web tests (Vitest + Playwright happy-path).
27. Add CI: coverage upload, Docker build smoke test, pip-audit/trivy, migration apply-and-rollback test.

---

## 10. What's actually good

To keep the audit honest:
- **`packages/polymarket-client`** is the cleanest module in the repo. Token-bucket rate limiter, jittered retry, ETag/If-Modified-Since on Gamma, `aclose`-correct httpx, structured cache with eviction. Solid.
- **`auth.verify_magic_link_token`** uses an atomic `UPDATE … WHERE used_at IS NULL … RETURNING` (auth.py:117–123). Race-free single-shot consumption — verified.
- **Migration discipline.** ReplacingMergeTree on time-series tables for idempotency, mandatory `observed_at`/`event_time`, 2-year TTL. PIT correctness is taken seriously throughout (`backtest.py:268–272,402` confirmed leak-free).
- **Tooling.** ruff + mypy strict + pre-commit + pytest 8 with `asyncio_mode=auto` + asyncpg/clickhouse-connect typed. The repo is lint-clean and typecheck-clean.
- **Test breadth.** 95 Python test files. The model package's heavy modules (`ensemble`, `conformal`, `regime`) all have multiple dedicated tests.
- **`writers.py`** is 1,407 lines but is pure tuple-shaping — no SQL, no I/O. Not a god module despite size.
- **DLQ wiring.** `dlq.py` is small, correct, swallows secondary failures, and is wired into 12+ workers.
- **The new dashboard.** `apps/web/src/app/page.tsx` and `Settings.tsx` (rebuilt today) typecheck clean, build clean, no warnings.

---

## 11. Document inventory used

- `prd.md` (Draft v0.3, 2026-04-22) — product spec
- `tasks.md` — milestone tracker (M0–M8)
- `docs/decisions/*.md` — staging hosting (Render), pricing model, twitter source, thin-book policy, macro calendar, discord integration, continuous-markets output
- `docs/runbooks/*.md` — data retention, LLM guardrails, on-call
- `docs/incident-log.md` — incident log (currently 229 bytes — empty stub)
- `readmefrontend.md` — frontend integration guide for the design implementation
- `.env.example` — 180 settings entries documented
- `Makefile` — 30+ targets

---

_End of analysis. Generated by parallel deep-dive across `services/api`, `services/ingest`, `packages/model`, `packages/polymarket-client`, `apps/web`, and infra. File:line references throughout._

---

## 12. Resolution log

### 2026-04-29 — Critical items 3–6
Spot-check against live code revealed three of the four were already implemented. Verified each by re-reading the named file:line and confirming the audit's described shape no longer exists.

| # | Item | Status | Evidence |
|---|---|---|---|
| 3 | Encryption-key hard-fail | Already done | `clob_credentials.py:153–171` raises `ValueError` on missing/invalid/wrong-length key. `main.py:64` calls `validate_secret_encryption_key(settings)` at the top of the lifespan handler. The `b"0" * 32` fallback the audit cited no longer exists. |
| 4 | Stripe webhook signature mandatory | Already done | `billing.py:228–232` raises `ValueError("STRIPE_WEBHOOK_SECRET must be configured")` unconditionally, then verifies the signature. The earlier `if secret and not _verify(...)` shape is gone. |
| 5 | Glassnode key out of URL | Already done | `onchain_metrics.py:306` uses `headers={"X-API-KEY": api_key}`. Dune client at line 325 uses `X-Dune-API-Key` header. |
| 6 | `/v1/error-reports` hardening | Done — partial → complete | Rate limit + size cap were already wired (`main.py:120–138, 255–262`). Added 2026-04-29: `_StackDedupe` class (`main.py:140–164`), `_client_ip` helper for `X-Forwarded-For` (`main.py:170–183`), `_error_report_fingerprint` SHA-256 fingerprint (`main.py:185–204`), handler integration (`main.py:300–318`), `error_reports_dedupe_window_seconds` setting (default 3600s). Tests: 8 new cases in `services/api/tests/test_error_reports.py`. `pytest test_error_reports.py` 11/11 passing; ruff + mypy clean. |

**Lesson noted for future audits:** the parallel-agent deep-dive cited specific line numbers but in three of four cases the code at those line numbers had already been hardened — the recommendations would have been correct against an older revision. Future audits should re-verify recommendations against `git log -p <file>` before recording them as outstanding work. Updated the §2 sub-headers to mark these resolved rather than deleting the original framing, so the audit's reasoning chain stays auditable.

**Outstanding from the original 🔴 list:** items 1 (session auth) and 2 (drop magic-link token from response body) — both architectural changes that need design conversation before implementation. The optional `extra="forbid"` flip on `settings.py:15` is a clean follow-up but would require an env-var inventory pass to avoid breaking dev setups.

### 2026-04-29 (later) — Item #2 + foundation for #1

Spot-check on `auth.py` revealed most of the session-cookie machinery was already present (`create_session_token`, `verify_session_token`, `set_session_cookie`, `current_user_email`, `current_admin_email`, admin allow-list via `admin_emails`). Wrote `[docs/decisions/session-auth.md](docs/decisions/session-auth.md)` to scope the remaining work and got approval.

Landed in this session:

| Item | Status | Detail |
|---|---|---|
| **#2** Magic-link in-band disclosure | ✅ Resolved | New `auth_magic_link_in_band: bool = False` setting (`settings.py:25–29`); `auth.py:request_magic_link` now consults it instead of `app_env != "production"`. Default false closes the staging leak. `.env.example` documents `AUTH_MAGIC_LINK_IN_BAND=true` for local dev. Tests added: `test_request_magic_link_omits_token_when_in_band_disabled`, `..._returns_token_when_in_band_enabled`. |
| **#1 foundation (1/3)** Per-user upsert helper | ✅ Landed | New `ensure_user_by_email(pool, email, display_name=None)` in `users.py`; existing `ensure_demo_user` now delegates. Lowercase + strip + `@` validation on input. 8 new tests in `services/api/tests/test_users.py` covering idempotency, isolation, blank/malformed rejection, display-name update-on-conflict, and the demo-user wrapper. |
| **#1 foundation (2/3)** Soft per-route gate | ✅ Landed | New `auth_enforce_user_routes: bool = False` setting (`settings.py:31–35`) and `current_user_email_or_demo` Depends helper (`auth.py:283–315`). When the flag is off, the helper resolves to the demo email so the dashboard keeps working before the magic-link login UI ships; when on, it requires a valid session cookie (401 otherwise). A *valid* cookie always wins regardless of the flag. 5 new tests in `test_auth.py` covering all four flag × cookie permutations. |
| **#1 application (3/3)** Gating routes + migrating 5 modules | ✅ Landed | All 10 previously-ungated user routes (`GET/PUT /v1/{paper-trading, privacy-preferences, push-preferences, polymarket-address, tuning-profile}`) now use `Depends(auth_q.current_user_email_or_demo)`. The 5 underlying modules (`paper_trading`, `privacy_prefs`, `push_prefs`, `tuning`, `polymarket_account`) accept `email: str` and call `ensure_user_by_email` instead of `ensure_demo_user`. The 4 system-level callers (`markets list`, `backtest walk-forward`, `market model`, `journal`) that read tuning to apply log-odds shifts now pass `email=settings.journal_demo_user_email` explicitly, making the previously-implicit "system uses demo user" behavior visible. New tests: `[test_user_route_gates.py](services/api/tests/test_user_route_gates.py)` exercises every gated route × {enforce on, enforce off, valid cookie} — 15 cases, all passing. Updated tests for the migrated modules to monkeypatch `ensure_user_by_email` instead of `ensure_demo_user`. |
| **#1 application (4/4)** Per-user isolation for credentials + journal | ✅ Landed | The remaining 3 modules — `clob_credentials`, `provider_credentials`, `journal` — now accept `email: str` everywhere they used to call `ensure_demo_user`. Their routes (`/v1/polymarket-clob-credentials`, `/v1/provider-credentials`, `/v1/journal/{calls,summary}`) already gate strictly via `Depends(current_user_email)`, so the data layer now isolates per authenticated user automatically. **Zero remaining `ensure_demo_user` callsites in application code** — `[grep ensure_demo_user services/api/src](services/api/src/api/)` only matches the function definition and its docstrings. System-level paths that legitimately operate on the demo tenant (`journal_autosync` for the user-channel WSS, `load_provider_credentials("stripe")` from billing, the 4 markets/journal tuning callers) pass `settings.journal_demo_user_email` explicitly so the system-tenant boundary is auditable from a grep. |

`pytest services/api/tests/` — **476/476 passing** (461 baseline + 15 new route-gate tests, with the credentials/journal migration test updates folded in). ruff clean on every changed file. mypy clean on the changed Python source files except for 4 pre-existing errors in code paths I didn't touch (`polymarket_account._float_or_none`, `m7_audit.replay_walk_forward_samples` tuple-unpack — same pre-existing 163-error baseline noted in §10).

### How to flip the auth enforcement on

After the dashboard ships a magic-link login flow:

1. Set `AUTH_ENFORCE_USER_ROUTES=true` (and `AUTH_MAGIC_LINK_IN_BAND=false`) in the environment.
2. Routes that already gate via `Depends(current_user_email)` continue to require a session cookie.
3. Routes gated via `Depends(current_user_email_or_demo)` flip from "fall through to demo" to "401 without cookie."
4. Modules that accept `email: str` now receive the *authenticated* email and isolate data per-user via `ensure_user_by_email`.

Until then, the codebase enforces auth everywhere it can while keeping the dashboard's existing happy-path working — the flag is the single switch the operator owns.
