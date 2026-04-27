// Thin typed wrapper around the FastAPI backend.
//
// All historical reads MUST use the /asof endpoints (PRD §6.6 PIT correctness).
// Live reads are the default for dashboard views.

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? process.env.API_BASE ?? "http://localhost:8000";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} → ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `API ${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PUT",
    cache: "no-store",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `API ${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type MarketRow = {
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
  // M3 Polymarket-native signals (nullable when data hasn't landed yet).
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

export async function fetchMarkets(options: { cryptoOnly?: boolean } = {}): Promise<MarketRow[]> {
  const qs = new URLSearchParams({
    crypto_only: String(options.cryptoOnly ?? true),
  });
  try {
    return await getJson<MarketRow[]>(`/v1/markets?${qs}`);
  } catch {
    return [];
  }
}

export type MarketModel = {
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

export type SmartMoneyMarket = {
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

export async function fetchSmartMoney(
  conditionId: string,
): Promise<SmartMoneyMarket | null> {
  try {
    return await getJson<SmartMoneyMarket>(
      `/v1/markets/${encodeURIComponent(conditionId)}/smart-money`,
    );
  } catch {
    return null;
  }
}

export type ConcentrationSummary = {
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

export async function fetchConcentration(
  conditionId: string,
): Promise<ConcentrationSummary | null> {
  try {
    return await getJson<ConcentrationSummary>(
      `/v1/markets/${encodeURIComponent(conditionId)}/concentration`,
    );
  } catch {
    return null;
  }
}

export type SignalEvent = {
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

export type ExternalEvent = {
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

export type PushPreferences = {
  email_enabled: boolean;
  email_to: string | null;
  webhook_enabled: boolean;
  webhook_url: string | null;
  min_severity: number;
  event_types: string[];
  condition_ids: string[];
  updated_at: string | null;
};

export type PrivacyPreferences = {
  cross_user_learning_opt_in: boolean;
  updated_at: string | null;
};

export type TuningProfile = {
  name: string;
  preset: "conservative" | "balanced" | "aggressive" | "custom";
  log_odds_shifts: Record<string, number>;
  is_active: boolean;
  updated_at: string | null;
};

export type PolymarketTrade = {
  trade_id: string | null;
  condition_id: string | null;
  outcome: string | null;
  side: string | null;
  price: number | null;
  size: number | null;
  timestamp: string | null;
};

export type PolymarketAddressSummary = {
  proxy_wallet: string;
  verified_at: string | null;
  open_positions: number;
  redeemable_positions: number;
  total_position_value_usdc: number;
  total_earnings_usdc: number;
  recent_trades: PolymarketTrade[];
};

export type PolymarketAddressLink = {
  proxy_wallet: string | null;
  verified_at: string | null;
  summary: PolymarketAddressSummary | null;
};

export type PolymarketClobCredentialStatus = {
  configured: boolean;
  proxy_wallet: string | null;
  created_at: string | null;
  rotated_at: string | null;
};

export type SignalFeedOptions = {
  lookbackHours?: number;
  limit?: number;
  eventTypes?: string[];
  conditionId?: string;
  minSeverity?: number;
};

export type ExternalEventOptions = {
  lookbackHours?: number;
  limit?: number;
  eventKinds?: string[];
  sources?: string[];
  conditionId?: string;
};

export async function fetchSignals(
  options: SignalFeedOptions = {},
): Promise<SignalEvent[]> {
  const qs = new URLSearchParams();
  if (options.lookbackHours != null) qs.set("lookback_hours", String(options.lookbackHours));
  if (options.limit != null) qs.set("limit", String(options.limit));
  if (options.conditionId) qs.set("condition_id", options.conditionId);
  if (options.minSeverity != null) qs.set("min_severity", String(options.minSeverity));
  for (const type of options.eventTypes ?? []) qs.append("event_type", type);
  const qsStr = qs.toString();
  try {
    return await getJson<SignalEvent[]>(`/v1/signals${qsStr ? `?${qsStr}` : ""}`);
  } catch {
    return [];
  }
}

export async function fetchExternalEvents(
  options: ExternalEventOptions = {},
): Promise<ExternalEvent[]> {
  const qs = new URLSearchParams();
  if (options.lookbackHours != null) qs.set("lookback_hours", String(options.lookbackHours));
  if (options.limit != null) qs.set("limit", String(options.limit));
  if (options.conditionId) qs.set("condition_id", options.conditionId);
  for (const kind of options.eventKinds ?? []) qs.append("event_kind", kind);
  for (const source of options.sources ?? []) qs.append("source", source);
  const qsStr = qs.toString();
  try {
    return await getJson<ExternalEvent[]>(`/v1/external-events${qsStr ? `?${qsStr}` : ""}`);
  } catch {
    return [];
  }
}

export async function fetchPushPreferences(): Promise<PushPreferences> {
  return getJson<PushPreferences>("/v1/push-preferences");
}

export async function fetchPrivacyPreferences(): Promise<PrivacyPreferences> {
  return getJson<PrivacyPreferences>("/v1/privacy-preferences");
}

export async function updatePushPreferences(
  payload: Omit<PushPreferences, "updated_at">,
): Promise<PushPreferences> {
  return putJson<PushPreferences>("/v1/push-preferences", payload);
}

export async function updatePrivacyPreferences(
  payload: Omit<PrivacyPreferences, "updated_at">,
): Promise<PrivacyPreferences> {
  return putJson<PrivacyPreferences>("/v1/privacy-preferences", payload);
}

export async function fetchTuningProfile(): Promise<TuningProfile> {
  return getJson<TuningProfile>("/v1/tuning-profile");
}

export async function updateTuningProfile(payload: {
  preset: TuningProfile["preset"];
  log_odds_shifts: Record<string, number>;
}): Promise<TuningProfile> {
  return putJson<TuningProfile>("/v1/tuning-profile", payload);
}

export async function fetchPolymarketAddress(): Promise<PolymarketAddressLink> {
  return getJson<PolymarketAddressLink>("/v1/polymarket-address");
}

export async function fetchPolymarketClobCredentials(): Promise<PolymarketClobCredentialStatus> {
  return getJson<PolymarketClobCredentialStatus>("/v1/polymarket-clob-credentials");
}

export async function updatePolymarketAddress(
  proxyWallet: string | null,
): Promise<PolymarketAddressLink> {
  return putJson<PolymarketAddressLink>("/v1/polymarket-address", {
    proxy_wallet: proxyWallet,
  });
}

export async function updatePolymarketClobCredentials(payload: {
  api_key: string | null;
  api_secret: string | null;
  passphrase: string | null;
  proxy_wallet: string | null;
}): Promise<PolymarketClobCredentialStatus> {
  return putJson<PolymarketClobCredentialStatus>("/v1/polymarket-clob-credentials", payload);
}

export type FeatureAttribution = {
  feature_name: string;
  label: string;
  feature_value: number | null;
  score_contribution: number;
  transformed_value: number;
};

export async function fetchMarketModel(conditionId: string): Promise<MarketModel> {
  return getJson<MarketModel>(`/v1/markets/${encodeURIComponent(conditionId)}/model`);
}

export type MarketHistoryPoint = {
  event_time: string;
  market_mid: number;
  model_prob: number | null;
};

export async function fetchMarketHistory(
  conditionId: string,
  windowHours = 24 * 7,
  maxPoints = 96,
): Promise<MarketHistoryPoint[]> {
  const qs = new URLSearchParams({
    window_hours: String(windowHours),
    max_points: String(maxPoints),
  });
  return getJson<MarketHistoryPoint[]>(
    `/v1/markets/${encodeURIComponent(conditionId)}/history?${qs}`,
  );
}

export type AsOfMarket = {
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

export async function fetchMarketAsOf(conditionId: string, at: Date): Promise<AsOfMarket> {
  const qs = new URLSearchParams({ at: at.toISOString() });
  return getJson<AsOfMarket>(`/v1/markets/${encodeURIComponent(conditionId)}/asof?${qs}`);
}

export type JournalCall = {
  id: string;
  condition_id: string;
  outcome: string;
  side: string;
  size_usdc: number;
  entry_price: number;
  model_prob_at_call: number;
  model_band_lo_at_call: number;
  model_band_hi_at_call: number;
  market_mid_at_call: number;
  created_at: string;
  resolved_outcome: string | null;
  resolved_at: string | null;
  pnl_usdc: number | null;
  brier_contribution: number | null;
  predicted_edge_bps: number;
  realized_edge_bps: number | null;
  call_confidence: number;
};

export type JournalSummary = {
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

export async function fetchJournalCalls(): Promise<JournalCall[]> {
  try {
    return await getJson<JournalCall[]>("/v1/journal/calls");
  } catch {
    return [];
  }
}

export async function fetchJournalSummary(): Promise<JournalSummary> {
  try {
    return await getJson<JournalSummary>("/v1/journal/summary");
  } catch {
    return {
      total_calls: 0,
      resolved_calls: 0,
      unresolved_calls: 0,
      avg_brier: null,
      total_pnl_usdc: 0,
      resolution_sync: null,
      confidence_buckets: [],
      best_calls: [],
      worst_calls: [],
      edge_scatter: [],
      calibration_points: [],
    };
  }
}

export type DriftMetricRow = {
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

export type FeatureDriftMetricRow = {
  feature_name: string;
  reference_count: number;
  current_count: number;
  psi: number;
  kl_divergence: number;
  psi_threshold: number;
  is_alert: boolean;
  observed_at: string;
};

export type DriftSnapshot = {
  observed_at: string | null;
  model_metrics: DriftMetricRow[];
  feature_metrics: FeatureDriftMetricRow[];
};

export async function fetchDriftSnapshot(): Promise<DriftSnapshot> {
  try {
    return await getJson<DriftSnapshot>("/v1/drift-monitor");
  } catch {
    return {
      observed_at: null,
      model_metrics: [],
      feature_metrics: [],
    };
  }
}

export type BacktestCalibrationPoint = {
  bucket_mid: number;
  avg_predicted: number;
  hit_rate: number;
  count: number;
};

export type BacktestReplayRow = {
  condition_id: string;
  market_type: string;
  regime: string | null;
  asked_at: string;
  resolved_at: string;
  predicted_prob: number;
  outcome: number;
  brier_contribution: number;
  time_to_resolution_s: number;
  band_lo: number | null;
  band_hi: number | null;
  band_hit: boolean | null;
};

export type BacktestSnapshot = {
  lookback_days: number;
  horizon_hours: number;
  total_samples: number;
  calibration_points: BacktestCalibrationPoint[];
  rows: BacktestReplayRow[];
  corpus: {
    total_resolved_markets: number;
    binary_markets: number;
    invalid_markets: number;
    disputed_markets: number;
    low_volume_markets: number;
    missing_snapshot_markets: number;
  };
  tuning_comparison: {
    profile_name: string;
    profile_preset: string;
    total_samples: number;
    default_brier: number;
    tuned_brier: number;
    brier_delta: number;
    default_calibration_points: BacktestCalibrationPoint[];
    tuned_calibration_points: BacktestCalibrationPoint[];
  } | null;
};

export async function fetchBacktestSnapshot(
  lookbackDays = 90,
  horizonHours = 24,
  limit = 500,
): Promise<BacktestSnapshot> {
  const qs = new URLSearchParams({
    lookback_days: String(lookbackDays),
    horizon_hours: String(horizonHours),
    limit: String(limit),
  });
  try {
    return await getJson<BacktestSnapshot>(`/v1/backtest/walk-forward?${qs}`);
  } catch {
    return {
      lookback_days: lookbackDays,
      horizon_hours: horizonHours,
      total_samples: 0,
      calibration_points: [],
      rows: [],
      corpus: {
        total_resolved_markets: 0,
        binary_markets: 0,
        invalid_markets: 0,
        disputed_markets: 0,
        low_volume_markets: 0,
        missing_snapshot_markets: 0,
      },
      tuning_comparison: null,
    };
  }
}

export type SystemStatus = {
  state: "operational" | "degraded" | "down" | "unknown";
  checked_at: string;
  components: Array<{
    name: string;
    state: "operational" | "degraded" | "down" | "unknown";
    detail: string;
    last_observed_at: string | null;
  }>;
};

export async function fetchSystemStatus(): Promise<SystemStatus> {
  return getJson<SystemStatus>("/v1/status");
}

export async function reportClientError(payload: {
  message: string;
  stack?: string | null;
  url?: string | null;
  user_agent?: string | null;
  context?: Record<string, unknown>;
}): Promise<void> {
  try {
    await postJson("/v1/error-reports", {
      source: "web",
      severity: "error",
      ...payload,
    });
  } catch {
    // Error reporting must never create a second user-visible failure.
  }
}

export type BetaInvite = {
  id: string;
  email: string;
  display_name: string | null;
  status: string;
  invited_at: string;
  accepted_at: string | null;
};

export type BetaInviteSummary = {
  target_count: number;
  invited_count: number;
  accepted_count: number;
  remaining_slots: number;
  invites: BetaInvite[];
};

export async function fetchBetaInvites(): Promise<BetaInviteSummary> {
  return getJson<BetaInviteSummary>("/v1/beta/invites");
}

export async function createBetaInvite(payload: {
  email: string;
  display_name?: string | null;
}): Promise<BetaInvite> {
  return postJson<BetaInvite>("/v1/beta/invites", payload);
}

export async function submitBetaFeedback(payload: {
  kind: "bug" | "idea" | "model" | "data" | "other";
  message: string;
  page_url?: string | null;
  condition_id?: string | null;
  contact_email?: string | null;
}): Promise<{ id: string; kind: string; submitted_at: string }> {
  return postJson("/v1/beta/feedback", payload);
}

export async function createJournalCall(payload: {
  condition_id: string;
  outcome: "YES" | "NO";
  size_usdc: number;
}): Promise<{
  id: string;
  condition_id: string;
  outcome: string;
  side: string;
  size_usdc: number;
  entry_price: number;
  model_prob_at_call: number;
  market_mid_at_call: number;
  created_at: string;
}> {
  return postJson("/v1/journal/calls", payload);
}

// ---------------------------------------------------------------------------
// Paper trading toggle
// ---------------------------------------------------------------------------

export type PaperTradingStatus = {
  enabled: boolean;
  updated_at: string | null;
};

export async function fetchPaperTradingStatus(): Promise<PaperTradingStatus> {
  try {
    return await getJson<PaperTradingStatus>("/v1/paper-trading");
  } catch {
    return { enabled: false, updated_at: null };
  }
}

export async function updatePaperTrading(
  enabled: boolean,
): Promise<PaperTradingStatus> {
  return putJson<PaperTradingStatus>("/v1/paper-trading", { enabled });
}

// ---------------------------------------------------------------------------
// Waitlist
// ---------------------------------------------------------------------------

export type WaitlistSignupResult = {
  id: string;
  email: string;
  name: string | null;
  created_at: string;
};

export async function submitWaitlistSignup(payload: {
  email: string;
  name?: string | null;
  use_case?: string | null;
  source?: string;
}): Promise<WaitlistSignupResult> {
  return postJson<WaitlistSignupResult>("/v1/waitlist", payload);
}

// ---------------------------------------------------------------------------
// Billing
// ---------------------------------------------------------------------------

export type CheckoutSession = {
  session_id: string;
  url: string;
  is_mock: boolean;
};

export type BillingSubscriptionStatus = {
  email: string;
  status: string;
  plan: string | null;
};

export async function createCheckoutSession(payload: {
  email: string;
  success_url?: string;
  cancel_url?: string;
}): Promise<CheckoutSession> {
  return postJson<CheckoutSession>("/v1/billing/checkout", payload);
}

export async function fetchBillingStatus(
  email: string,
): Promise<BillingSubscriptionStatus> {
  return getJson<BillingSubscriptionStatus>(
    `/v1/billing/status?email=${encodeURIComponent(email)}`,
  );
}

// ---------------------------------------------------------------------------
// EOL monitor (M8.3)
// ---------------------------------------------------------------------------

export type EolMonitorReport = {
  as_of: string;
  lookback_days: number;
  ramp_up: {
    markets_checked: number;
    markets_with_high_certainty: number;
    markets_with_low_certainty: number;
    high_certainty_rate: number;
    sample_market_ids: string[];
  };
  phantom_edge: {
    alerts_in_final_hour: number;
    alerts_suppressed: number;
    alerts_not_suppressed: number;
    suppression_rate: number;
    sample_unsuppressed_ids: string[];
  };
  last_hour_fp: {
    alerts_classified: number;
    true_positives: number;
    false_positives: number;
    skipped_unresolved: number;
    fp_rate: number | null;
    meets_target: boolean;
  };
};

export async function fetchEolMonitor(
  lookbackDays = 30,
): Promise<EolMonitorReport | null> {
  try {
    return await getJson<EolMonitorReport>(
      `/v1/eol-monitor?lookback_days=${lookbackDays}`,
    );
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Decision-time metrics (M8.5)
// ---------------------------------------------------------------------------

export type DecisionTimeReport = {
  as_of: string;
  window_hours: number;
  window_label: string;
  sample_count: number;
  median_minutes: number | null;
  p90_minutes: number | null;
  meets_target: boolean;
  target_minutes: number;
};

export async function fetchDecisionTimeMetrics(
  windowHours = 48,
): Promise<DecisionTimeReport | null> {
  try {
    return await getJson<DecisionTimeReport>(
      `/v1/decision-time-metrics?window_hours=${windowHours}`,
    );
  } catch {
    return null;
  }
}

