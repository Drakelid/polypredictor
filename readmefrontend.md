# PolyPredictor Frontend Data Integration Guide

Fetch this design file, read its readme, and implement the relevant aspects of the design. https://api.anthropic.com/v1/design/h/3WlS6GKl_wOaq6OCJrfO8Q?open_file=PolyPredictor.html
Implement: PolyPredictor.html

## API Base URLs

Use the correct base URL for the runtime:

- Browser running on host: `http://localhost:8000`
- Server-side frontend code inside Docker Compose: `http://api:8000`

Do not hardcode `localhost` in server-side container code. In Docker, `localhost` points to the frontend container, not the API container.

## Data Freshness Rule

Live dashboard reads use normal `/v1/...` endpoints.

Historical or replay views must use `/asof` endpoints with an ISO timestamp. This preserves point-in-time correctness and prevents future data leakage.

## Health And Status

### `GET /healthz`

Lightweight load-balancer health check.

### `GET /v1/status`

Human/operator status rollup.

Response:

```ts
type SystemStatus = {
  state: "operational" | "degraded" | "down" | "unknown";
  checked_at: string;
  components: Array<{
    name: string;
    state: "operational" | "degraded" | "down" | "unknown";
    detail: string;
    last_observed_at: string | null;
  }>;
};
```

## Markets

### `GET /v1/markets`

Main market dashboard list.

Query params:

- `crypto_only`: boolean string, defaults to `true`

Response item:

```ts
type MarketRow = {
  condition_id: string;
  question: string;
  slug: string;
  category: string | null;
  tags?: string[];
  volume_usdc: number;
  liquidity_usdc: number;
  mid: number | null;
  model_prob: number | null;
  model_source?: string;
  refinement_source?: string | null;
  edge_bps: number | null;
  baseline_source?: string;
  market_type?: string;
  confidence?: number;
  needs_review?: boolean;
  time_to_resolution_s: number | null;
  smart_money_consensus?: number | null;
  smart_money_sample_wallets?: number | null;
  smart_money_dominant?: string | null;
  concentration_score?: number | null;
  concentration_whale_flag?: boolean | null;
  resolution_risk_score?: number | null;
  resolution_risk_level?: string | null;
  resolution_risk_flagged?: boolean;
  adversarial_flow_score?: number | null;
  adversarial_flow_flagged?: boolean;
  thin_book?: boolean;
};
```

### `GET /v1/markets/{condition_id}/model`

Full model detail for a market.

Response:

```ts
type MarketModel = {
  condition_id: string;
  question: string;
  market_type: string;
  confidence: number;
  needs_review: boolean;
  classifier: string;
  mid: number | null;
  model_prob: number | null;
  model_source: string;
  distribution_samples?: [number, number][] | null;
  refinement_source: string | null;
  baseline_source: string;
  tuning_profile: string | null;
  tuning_preset: string | null;
  tuning_log_odds_shift: number;
  edge_bps: number | null;
  uncertainty_multiplier: number;
  band_lo: number | null;
  band_hi: number | null;
  band_coverage: number | null;
  uncertainty_source: string | null;
  resolution_risk_multiplier: number;
  kelly_side: string | null;
  kelly_fraction: number | null;
  kelly_uncapped_fraction: number | null;
  kelly_fractional_multiplier: number;
  kelly_cap: number;
  reasons: string[];
  model_reasons: string[];
  classifier_reasons: string[];
  driver_summaries: string[];
  feature_attributions: FeatureAttribution[];
  smart_money_consensus?: number | null;
  smart_money_sample_wallets?: number | null;
  smart_money_dominant?: string | null;
  concentration_score?: number | null;
  concentration_whale_flag?: boolean | null;
  concentration_yes_top1_pct?: number | null;
  concentration_no_top1_pct?: number | null;
  resolution_risk_score?: number | null;
  resolution_risk_level?: string | null;
  resolution_risk_flagged?: boolean;
  resolution_risk_reasons?: string[];
  adversarial_flow_score?: number | null;
  adversarial_flow_flagged?: boolean;
  adversarial_flow_reasons?: string[];
  thin_book?: boolean;
};

type FeatureAttribution = {
  feature_name: string;
  label: string;
  feature_value: number | null;
  score_contribution: number;
  transformed_value: number;
};
```

### `GET /v1/markets/{condition_id}/history`

Model probability and market-mid time series.

Query params:

- `window_hours`: number, default used by frontend is `168`
- `max_points`: number, default used by frontend is `96`

Response item:

```ts
type MarketHistoryPoint = {
  event_time: string;
  market_mid: number;
  model_prob: number | null;
};
```

### `GET /v1/markets/{condition_id}/concentration`

Holder concentration and whale flags.

Response:

```ts
type ConcentrationSummary = {
  condition_id: string;
  yes_gini: number | null;
  no_gini: number | null;
  max_gini: number | null;
  yes_top1_pct: number | null;
  no_top1_pct: number | null;
  yes_top5_pct: number | null;
  no_top5_pct: number | null;
  yes_whale_flag: boolean;
  no_whale_flag: boolean;
  any_whale_flag: boolean;
  yes_holders_count: number;
  no_holders_count: number;
  observed_at: string;
};
```

### `GET /v1/markets/{condition_id}/smart-money`

Smart-money positioning summary.

Response:

```ts
type SmartMoneyMarket = {
  condition_id: string;
  latest: {
    yes_wallets: number;
    no_wallets: number;
    yes_size_usdc: number;
    no_size_usdc: number;
    yes_avg_entry: number | null;
    no_avg_entry: number | null;
    net_size_usdc: number;
    consensus_score: number;
    sample_wallets: number;
    dominant_outcome: string;
    observed_at: string;
  };
  prior_24h: SmartMoneyMarket["latest"] | null;
  directional_delta_usdc: number | null;
  consensus_delta_24h: number | null;
};
```

### `GET /v1/markets/{condition_id}/social-features`

Social/news-derived features for one market. Use this for market-detail signal context.

## Point-In-Time Endpoints

Use these for historical replay, backtests, timeline hover states, and any chart where the selected time is in the past.

### `GET /v1/markets/{condition_id}/asof?at={iso_datetime}`

Response:

```ts
type AsOfMarket = {
  condition_id: string;
  question: string;
  active: boolean;
  closed: boolean;
  volume_usdc: number;
  liquidity_usdc: number;
  open_interest_usdc: number;
  end_date: string | null;
  token_ids: string[];
  event_time: string;
  observed_at: string;
};
```

### Other As-Of Reads

- `GET /v1/quotes/{token_id}/asof?at={iso_datetime}`
- `GET /v1/features/{condition_id}/asof?at={iso_datetime}`
- `GET /v1/event-time/{condition_id}/asof?at={iso_datetime}`

## Signals

### `GET /v1/signals`

Signal feed for alerts and market activity.

Query params:

- `lookback_hours`: number
- `limit`: number
- `event_type`: repeatable string
- `condition_id`: string
- `min_severity`: number

Response item:

```ts
type SignalEvent = {
  event_id: string;
  event_type: string;
  condition_id: string;
  severity: number;
  actor: string;
  direction: string;
  size_delta_usdc: number | null;
  prior_size_usdc: number | null;
  current_size_usdc: number | null;
  payload: Record<string, unknown>;
  event_time: string;
  observed_at: string;
};
```

Expected signal usage:

- Global signal feed panel
- Market-detail signal timeline
- Alert badges on market cards
- Filtering by `event_type`, `condition_id`, and `min_severity`

## External Events

### `GET /v1/external-events`

News, social, macro, on-chain, and other external evidence.

Query params:

- `lookback_hours`: number
- `limit`: number
- `event_kind`: repeatable string
- `source`: repeatable string
- `condition_id`: string

Response item:

```ts
type ExternalEvent = {
  event_kind: string;
  source: string;
  source_uri: string;
  source_id: string;
  related_markets: string[];
  author: string;
  title: string;
  body: string;
  url: string;
  metadata: Record<string, unknown>;
  event_time: string;
  observed_at: string;
  age_seconds: number;
  decay_half_life_minutes: number;
  freshness_weight: number;
};
```

Expected event usage:

- Evidence cards
- Market-detail news/social feed
- Source freshness indicators
- External divergence explanations

## Source Health And Data Quality

### `GET /v1/source-health`

Per-source request health, latency, failure rate, and rate-limit headroom.

Use this for operational dashboards and data-quality banners.

### `GET /v1/source-failure-audit`

M5 source failure-rate audit.

### `GET /v1/cost-watch`

Estimated source/API cost rollup.

### `GET /v1/regulatory-events`

Regulatory-news event filter.

### `GET /v1/security/rotation-audit`

Credential rotation audit summary.

## Drift, Backtests, And Monitoring

### `GET /v1/drift-monitor`

Latest model and feature drift snapshot.

Response:

```ts
type DriftSnapshot = {
  observed_at: string | null;
  model_metrics: DriftMetricRow[];
  feature_metrics: FeatureDriftMetricRow[];
};

type DriftMetricRow = {
  window_label: string;
  market_type: string;
  ttr_bucket: string;
  regime: string | null;
  sample_count: number;
  brier: number;
  ece: number;
  coverage: number | null;
  avg_predicted: number;
  avg_outcome: number;
  baseline_brier: number | null;
  brier_skill: number | null;
  observed_at: string;
};

type FeatureDriftMetricRow = {
  feature_name: string;
  reference_count: number;
  current_count: number;
  psi: number;
  kl_divergence: number;
  psi_threshold: number;
  is_alert: boolean;
  observed_at: string;
};
```

### `GET /v1/backtest/walk-forward`

Walk-forward backtest replay.

Query params:

- `lookback_days`
- `horizon_hours`
- `limit`

### `GET /v1/eol-monitor`

End-of-life convergence and last-hour false-positive checks.

### `GET /v1/decision-time-metrics`

Median and p90 time-to-decision monitor.

## Journal

### `GET /v1/journal/calls`

List user journal calls.

### `POST /v1/journal/calls`

Create a manual call.

Body:

```ts
type CreateJournalCall = {
  condition_id: string;
  outcome: "YES" | "NO";
  size_usdc: number;
};
```

### `GET /v1/journal/summary`

Journal aggregate metrics.

Response:

```ts
type JournalSummary = {
  total_calls: number;
  resolved_calls: number;
  unresolved_calls: number;
  avg_brier: number | null;
  total_pnl_usdc: number;
  resolution_sync: {
    proxy_wallet: string;
    verified_at: string | null;
    open_positions: number;
    redeemable_positions: number;
    total_position_value_usdc: number;
    total_earnings_usdc: number;
  } | null;
  confidence_buckets: Array<{
    label: string;
    bucket_mid: number;
    count: number;
    avg_confidence: number;
    hit_rate: number;
  }>;
  best_calls: Array<{
    id: string;
    condition_id: string;
    outcome: string;
    pnl_usdc: number | null;
    predicted_edge_bps: number;
  }>;
  worst_calls: Array<{
    id: string;
    condition_id: string;
    outcome: string;
    pnl_usdc: number | null;
    predicted_edge_bps: number;
  }>;
  edge_scatter: Array<{
    predicted_edge_bps: number;
    realized_edge_bps: number;
  }>;
  calibration_points: Array<{
    bucket_mid: number;
    avg_predicted: number;
    hit_rate: number;
    count: number;
  }>;
};
```

## Preferences And User Settings

### Push Preferences

- `GET /v1/push-preferences`
- `PUT /v1/push-preferences`

```ts
type PushPreferences = {
  email_enabled: boolean;
  email_to: string | null;
  webhook_enabled: boolean;
  webhook_url: string | null;
  min_severity: number;
  event_types: string[];
  condition_ids: string[];
  updated_at: string | null;
};
```

### Privacy Preferences

- `GET /v1/privacy-preferences`
- `PUT /v1/privacy-preferences`

```ts
type PrivacyPreferences = {
  cross_user_learning_opt_in: boolean;
  updated_at: string | null;
};
```

### Tuning Profile

- `GET /v1/tuning-profile`
- `PUT /v1/tuning-profile`

```ts
type TuningProfile = {
  name: string;
  preset: "conservative" | "balanced" | "aggressive" | "custom";
  log_odds_shifts: Record<string, number>;
  is_active: boolean;
  updated_at: string | null;
};
```

### Paper Trading

- `GET /v1/paper-trading`
- `PUT /v1/paper-trading`

```ts
type PaperTradingStatus = {
  enabled: boolean;
  updated_at: string | null;
};
```

## Polymarket Account And Credentials

### `GET /v1/polymarket-address`

Linked Polymarket proxy wallet plus public account summary.

```ts
type PolymarketAddressLink = {
  proxy_wallet: string | null;
  verified_at: string | null;
  summary: PolymarketAddressSummary | null;
};

type PolymarketAddressSummary = {
  proxy_wallet: string;
  verified_at: string | null;
  open_positions: number;
  redeemable_positions: number;
  total_position_value_usdc: number;
  total_earnings_usdc: number;
  recent_trades: PolymarketTrade[];
};

type PolymarketTrade = {
  trade_id: string | null;
  condition_id: string | null;
  outcome: string | null;
  side: string | null;
  price: number | null;
  size: number | null;
  timestamp: string | null;
};
```

### `PUT /v1/polymarket-address`

Body:

```json
{
  "proxy_wallet": "0x..."
}
```

Clear:

```json
{
  "proxy_wallet": null
}
```

### `GET /v1/polymarket-clob-credentials`

Returns status only. It never returns stored secrets.

```ts
type PolymarketClobCredentialStatus = {
  configured: boolean;
  proxy_wallet: string | null;
  created_at: string | null;
  rotated_at: string | null;
};
```

### `PUT /v1/polymarket-clob-credentials`

Body:

```json
{
  "api_key": "value",
  "api_secret": "value",
  "passphrase": "value",
  "proxy_wallet": "0x..."
}
```

Clear:

```json
{
  "api_key": null,
  "api_secret": null,
  "passphrase": null,
  "proxy_wallet": null
}
```

## Generic Provider Credentials

### `GET /v1/provider-credentials`

Returns supported providers, field metadata, and configured status. It never returns stored secrets.

```ts
type ProviderCredentialsResponse = {
  providers: ProviderCredentialStatus[];
};

type ProviderCredentialStatus = {
  provider: string;
  label: string;
  description: string;
  fields: ProviderCredentialField[];
  configured: boolean;
  configured_fields: string[];
  created_at: string | null;
  rotated_at: string | null;
};

type ProviderCredentialField = {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  placeholder: string;
};
```

Use `fields` to generate the frontend form dynamically.

Supported providers:

- `polymarket_clob`
- `cme_fedwatch`
- `x_api`
- `reddit`
- `glassnode`
- `dune`
- `stripe`

### `PUT /v1/provider-credentials/{provider}`

Body:

```json
{
  "values": {
    "api_id": "value",
    "api_secret": "value"
  }
}
```

Clear credentials by sending all fields as `null` or empty strings:

```json
{
  "values": {
    "api_id": null,
    "api_secret": null
  }
}
```

Frontend handling:

- Render password inputs for fields where `secret: true`.
- After successful save, clear input values.
- Refetch `GET /v1/provider-credentials`.
- Display only `configured`, `configured_fields`, and `rotated_at`.
- Never render stored secret values because the API will not return them.

## Billing And Beta

Billing:

- `POST /v1/billing/checkout`
- `GET /v1/billing/status?email={email}`
- `POST /v1/billing/webhook`

Beta:

- `GET /v1/beta/invites`
- `POST /v1/beta/invites`
- `POST /v1/beta/feedback`

Waitlist:

- `POST /v1/waitlist`
- `GET /v1/waitlist`

## Admin

Use admin endpoints only in internal tools:

- `GET /v1/admin/classification-review`
- `POST /v1/admin/classification-review/{review_id}`
- `POST /v1/admin/model/{market_type}/re-enable`
- `POST /v1/admin/model/{market_type}/disable`

## Frontend Implementation Rules

- Use `cache: "no-store"` for live dashboard reads.
- Use `/asof` endpoints for any historical timestamp.
- Treat `null` model probabilities, mids, bands, and edge values as valid loading or unavailable states.
- Do not assume every market has all signal fields.
- Refetch market list after tuning changes.
- Refetch credential status after credential saves.
- Do not display credential plaintext.
- For browser writes from `localhost:3000`, the API must allow CORS for that origin.
- Prefer the existing typed wrapper in `apps/web/src/lib/api.ts` as the integration contract.
