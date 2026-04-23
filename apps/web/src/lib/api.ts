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
};

export async function fetchMarkets(): Promise<MarketRow[]> {
  try {
    return await getJson<MarketRow[]>("/v1/markets");
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
  refinement_source: string | null;
  baseline_source: string;
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
};

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
      confidence_buckets: [],
      best_calls: [],
      worst_calls: [],
      edge_scatter: [],
      calibration_points: [],
    };
  }
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
