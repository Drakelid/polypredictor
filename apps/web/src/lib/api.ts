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
  baseline_source: string;
  edge_bps: number | null;
  uncertainty_multiplier: number;
  reasons: string[];
  classifier_reasons: string[];
};

export async function fetchMarketModel(conditionId: string): Promise<MarketModel> {
  return getJson<MarketModel>(`/v1/markets/${encodeURIComponent(conditionId)}/model`);
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
