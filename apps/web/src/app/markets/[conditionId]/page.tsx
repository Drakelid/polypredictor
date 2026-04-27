"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  createJournalCall,
  fetchExternalEvents,
  fetchConcentration,
  fetchMarketAsOf,
  fetchMarketHistory,
  fetchMarketModel,
  fetchSmartMoney,
  type ExternalEvent,
  type FeatureAttribution,
  type MarketHistoryPoint,
} from "@/lib/api";

function fmtPct(p: number | null | undefined): string {
  if (p == null) return "-%";
  return `${(p * 100).toFixed(1)}%`;
}

function fmtBps(bps: number | null | undefined): string {
  if (bps == null) return "-";
  const sign = bps >= 0 ? "+" : "";
  return `${sign}${bps.toFixed(0)} bps`;
}

function fmtSigned(value: number | null | undefined, digits = 3): string {
  if (value == null) return "-";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}`;
}

function fmtFeatureValue(attribution: FeatureAttribution): string {
  if (attribution.feature_value == null) return "n/a";
  if (
    attribution.feature_name === "p_base_logit" ||
    attribution.feature_name === "market_mid_logit" ||
    attribution.feature_name === "realized_vol_24h"
  ) {
    return `${(attribution.feature_value * 100).toFixed(1)}%`;
  }
  return fmtSigned(attribution.feature_value);
}

function fmtPctSigned(p: number | null | undefined): string {
  if (p == null) return "-";
  const sign = p >= 0 ? "+" : "";
  return `${sign}${(p * 100).toFixed(1)}%`;
}

function fmtDateTime(ts: string): string {
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtAgeSeconds(ageSeconds: number): string {
  if (ageSeconds < 3600) return `${Math.max(1, Math.round(ageSeconds / 60))}m ago`;
  if (ageSeconds < 24 * 3600) return `${Math.round(ageSeconds / 3600)}h ago`;
  return `${Math.round(ageSeconds / (24 * 3600))}d ago`;
}

function evidenceSourceLabel(source: string): string {
  if (source.startsWith("rss:")) return source.slice(4);
  if (source.startsWith("reddit:")) return source.slice(7);
  if (source.startsWith("macro:")) return source.slice(6);
  return source;
}

function evidenceSummary(event: ExternalEvent): string {
  const body = event.body.trim();
  if (body) {
    return body.length > 180 ? `${body.slice(0, 177)}...` : body;
  }
  const title = event.title.trim();
  return title.length > 180 ? `${title.slice(0, 177)}...` : title;
}

function baselineSourceLabel(src: string | undefined): string {
  switch (src) {
    case "bs_one_touch":
      return "Black-Scholes barrier";
    case "bs_terminal":
      return "Black-Scholes terminal";
    case "range_conjunction":
      return "Range conjunction";
    case "fedwatch":
      return "FedWatch / OIS";
    case "consensus":
      return "Consensus survey";
    case "softmax":
      return "Softmax normalization";
    case "embedding_match":
      return "Nearest-resolved prior";
    case "base_rate":
      return "Historical base rate";
    case "market_mid":
      return "Market mid (fallback)";
    case "uniform":
      return "Uninformative";
    default:
      return src ?? "-";
  }
}

function modelSourceLabel(
  source: string | undefined,
  refinementSource: string | null | undefined,
): string {
  if (source === "ensemble") {
    return refinementSource === "per_type_ensemble_v1"
      ? "Per-type ensemble"
      : "Ensemble refinement";
  }
  if (source === "baseline") return "Baseline only";
  return source ?? "-";
}

function typeLabel(t: string | undefined): string {
  switch (t) {
    case "threshold":
      return "Threshold";
    case "range":
      return "Range";
    case "discrete_event":
      return "Discrete event";
    case "multi_outcome":
      return "Multi-outcome";
    case "long_tail_binary":
      return "Long-tail binary";
    case "misc":
      return "Misc";
    default:
      return t ?? "-";
  }
}

export default function MarketDetailPage() {
  const params = useParams<{ conditionId: string }>();
  const conditionId = decodeURIComponent(params.conditionId);
  const queryClient = useQueryClient();
  const [sizeUsdc, setSizeUsdc] = useState("100");

  const snapshot = useQuery({
    queryKey: ["market-asof", conditionId],
    queryFn: () => fetchMarketAsOf(conditionId, new Date()),
    refetchInterval: 5_000,
  });
  const model = useQuery({
    queryKey: ["market-model", conditionId],
    queryFn: () => fetchMarketModel(conditionId),
    refetchInterval: 5_000,
  });
  const history = useQuery({
    queryKey: ["market-history", conditionId],
    queryFn: () => fetchMarketHistory(conditionId),
    refetchInterval: 60_000,
  });
  const smartMoney = useQuery({
    queryKey: ["market-smart-money", conditionId],
    queryFn: () => fetchSmartMoney(conditionId),
    refetchInterval: 60_000,
  });
  const concentration = useQuery({
    queryKey: ["market-concentration", conditionId],
    queryFn: () => fetchConcentration(conditionId),
    refetchInterval: 60_000,
  });
  const evidence = useQuery({
    queryKey: ["market-external-events", conditionId],
    queryFn: () =>
      fetchExternalEvents({
        conditionId,
        lookbackHours: 24 * 7,
        limit: 8,
      }),
    refetchInterval: 60_000,
  });
  const journalCall = useMutation({
    mutationFn: (outcome: "YES" | "NO") =>
      createJournalCall({
        condition_id: conditionId,
        outcome,
        size_usdc: Number(sizeUsdc),
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["journal-calls"] }),
        queryClient.invalidateQueries({ queryKey: ["journal-summary"] }),
      ]);
    },
  });

  const data = snapshot.data;
  const m = model.data;
  const edgeClass = (m?.edge_bps ?? 0) >= 0 ? "text-edge-bullish" : "text-edge-bearish";
  const kellyClass =
    (m?.kelly_side ?? "YES") === "YES" ? "text-edge-bullish" : "text-edge-bearish";

  return (
    <main className="mx-auto max-w-6xl p-6">
      <Link href="/" className="text-sm text-gray-400 hover:underline">
        {"<-"} Back to dashboard
      </Link>

      {snapshot.isLoading && <p className="mt-6 text-gray-400">Loading...</p>}
      {snapshot.error && (
        <p className="mt-6 text-edge-bearish">
          Failed: {(snapshot.error as Error).message}
        </p>
      )}

      {data && (
        <>
          <header className="mb-6 mt-4">
            <h1 className="text-xl font-semibold text-white">{data.question}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
              <span className="tabular">{data.condition_id}</span>
              {m && (
                <>
                  <Badge tone={m.model_source === "ensemble" ? "accent" : "neutral"}>
                    {modelSourceLabel(m.model_source, m.refinement_source)}
                  </Badge>
                  <Badge tone="neutral">{typeLabel(m.market_type)}</Badge>
                  <Badge tone={m.needs_review ? "warn" : "neutral"}>
                    conf {Math.round(m.confidence * 100)}%
                  </Badge>
                  {m.needs_review && <Badge tone="warn">needs review</Badge>}
                  {m.resolution_risk_flagged && (
                    <Badge tone="warn">
                      resolution risk {m.resolution_risk_level ?? ""}
                    </Badge>
                  )}
                  {m.adversarial_flow_flagged && (
                    <Badge tone="warn">adversarial flow</Badge>
                  )}
                  {m.thin_book && <Badge tone="warn">thin book</Badge>}
                  {typeof m.smart_money_consensus === "number" && (
                    <Badge
                      tone={
                        m.smart_money_dominant === "YES"
                          ? "bull"
                          : m.smart_money_dominant === "NO"
                            ? "bear"
                            : "neutral"
                      }
                    >
                      smart $ {m.smart_money_dominant ?? "-"}{" "}
                      {Math.round((m.smart_money_consensus ?? 0) * 100)}%
                    </Badge>
                  )}
                  {m.concentration_whale_flag && <Badge tone="warn">whale &gt;40%</Badge>}
                  {typeof m.concentration_score === "number" && (
                    <Badge
                      tone={
                        m.concentration_score > 0.6 ? "warn" : "neutral"
                      }
                    >
                      gini {(m.concentration_score ?? 0).toFixed(2)}
                    </Badge>
                  )}
                </>
              )}
            </div>
          </header>

          <section className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <Card title="Model probability">
              <div className="text-3xl font-semibold text-white">{fmtPct(m?.model_prob)}</div>
              <div className="text-xs text-gray-500">
                {modelSourceLabel(m?.model_source, m?.refinement_source)}
              </div>
              {m?.band_lo != null && m?.band_hi != null && m?.band_coverage != null && (
                <div className="text-xs text-gray-500">
                  {Math.round(m.band_coverage * 100)}% band: {fmtPct(m.band_lo)} to {fmtPct(m.band_hi)}
                </div>
              )}
              {m?.resolution_risk_flagged && (
                <div className="text-xs text-amber-400">
                  Resolution risk {m.resolution_risk_level ?? "flagged"}; edge suppressed and uncertainty widened.
                </div>
              )}
              {m?.adversarial_flow_flagged && (
                <div className="text-xs text-amber-400">
                  Adversarial-flow risk elevated; ensemble refinement is being down-weighted.
                </div>
              )}
              {m?.thin_book && (
                <div className="text-xs text-amber-400">
                  Thin book: top-of-book depth is below the configured threshold.
                </div>
              )}
              <div className="text-xs text-gray-500">
                Underlying baseline: {baselineSourceLabel(m?.baseline_source)}
              </div>
            </Card>
            <Card title="Market mid">
              <div className="text-3xl font-semibold tabular text-white">{fmtPct(m?.mid)}</div>
              <div className="text-xs text-gray-500">Live quote (YES leg)</div>
            </Card>
            <Card title="Edge">
              <div className={`text-3xl font-semibold tabular ${edgeClass}`}>
                {fmtBps(m?.edge_bps)}
              </div>
              <div className="text-xs text-gray-500">Model - mid</div>
            </Card>
            <Card title="Kelly size">
              {m?.kelly_fraction != null && m.kelly_side ? (
                <>
                  <div className={`text-3xl font-semibold tabular ${kellyClass}`}>
                    {fmtPct(m.kelly_fraction)}
                  </div>
                  <div className="text-xs text-gray-500">{m.kelly_side} side, capped fractional Kelly</div>
                  <div className="text-xs text-gray-500">
                    full {fmtPct(m.kelly_uncapped_fraction)} x {fmtPct(m.kelly_fractional_multiplier)} multiplier,{" "}
                    {fmtPct(m.kelly_cap)} cap
                  </div>
                </>
              ) : (
                <>
                  <div className="text-3xl font-semibold text-white">-</div>
                  <div className="text-xs text-gray-500">No positive Kelly edge at current price</div>
                </>
              )}
            </Card>
          </section>

          {m &&
            (m.model_reasons.length > 0 ||
              m.reasons.length > 0 ||
              m.classifier_reasons.length > 0 ||
              m.driver_summaries.length > 0) && (
            <section className="mt-4">
              <Card title="Why this number?">
                {m.model_reasons.length > 0 && (
                  <div className="text-sm text-gray-300">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Model
                    </div>
                    <ul className="ml-4 list-disc text-gray-400">
                      {m.model_reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {m.classifier_reasons.length > 0 && (
                  <div className="mt-2 text-sm text-gray-300">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Classifier
                    </div>
                    <ul className="ml-4 list-disc text-gray-400">
                      {m.classifier_reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {m.resolution_risk_reasons && m.resolution_risk_reasons.length > 0 && (
                  <div className="mt-2 text-sm text-gray-300">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Resolution risk
                    </div>
                    <ul className="ml-4 list-disc text-gray-400">
                      {m.resolution_risk_reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {m.adversarial_flow_reasons && m.adversarial_flow_reasons.length > 0 && (
                  <div className="mt-2 text-sm text-gray-300">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Adversarial flow
                    </div>
                    <ul className="ml-4 list-disc text-gray-400">
                      {m.adversarial_flow_reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {m.reasons.length > 0 && (
                  <div className="mt-2 text-sm text-gray-300">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Underlying baseline
                    </div>
                    <ul className="ml-4 list-disc text-gray-400">
                      {m.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {m.driver_summaries.length > 0 && (
                  <div className="mt-2 text-sm text-gray-300">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Top drivers
                    </div>
                    <ul className="ml-4 list-disc text-gray-400">
                      {m.driver_summaries.map((summary, i) => (
                        <li key={i}>{summary}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </Card>
            </section>
          )}

          {m?.distribution_samples != null && m.distribution_samples.length > 0 && (
            <section className="mt-4">
              <Card title="Distribution Samples">
                 <DistributionChart samples={m.distribution_samples} />
                 <div className="mt-3 text-xs text-gray-500">
                   Percentile grid projection based on current market type and ensemble bounds.
                 </div>
              </Card>
            </section>
          )}

          <section className="mt-6">
            <Card title="Price & model history">
              {history.data && history.data.length > 1 ? (
                <>
                  <HistoryChart points={history.data} />
                  <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-gray-500">
                    <span className="inline-flex items-center gap-2">
                      <span className="h-2 w-2 rounded-full bg-gray-300" />
                      Market mid
                    </span>
                    <span className="inline-flex items-center gap-2">
                      <span className="h-2 w-2 rounded-full bg-sky-400" />
                      Model probability
                    </span>
                    <span>{history.data.length} PIT samples over the last 7d</span>
                  </div>
                </>
              ) : (
                <p className="text-sm text-gray-400">
                  Historical overlay appears once quote history is available for this market.
                </p>
              )}
            </Card>
          </section>

          <section className="mt-6">
            <Card title="Raw evidence">
              {evidence.data && evidence.data.length > 0 ? (
                <div className="space-y-3">
                  {evidence.data.map((event) => (
                    <a
                      key={`${event.source}:${event.source_id}`}
                      href={event.url || event.source_uri}
                      target="_blank"
                      rel="noreferrer"
                      className="block rounded border border-gray-800 bg-black/20 px-3 py-3 transition hover:border-gray-700"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                        <span className="uppercase tracking-wide">
                          {event.event_kind.replaceAll("_", " ")}
                        </span>
                        <span>{evidenceSourceLabel(event.source)}</span>
                        <span>{fmtAgeSeconds(event.age_seconds)}</span>
                        <span>
                          freshness {Math.round(event.freshness_weight * 100)}%
                        </span>
                      </div>
                      <div className="mt-1 text-sm font-medium text-white">
                        {event.title || "Untitled evidence"}
                      </div>
                      <div className="mt-1 text-sm text-gray-400">
                        {evidenceSummary(event)}
                      </div>
                      <div className="mt-2 text-xs text-gray-500">
                        {fmtDateTime(event.event_time)}
                      </div>
                    </a>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-400">
                  No linked headlines or social evidence have been captured for this market yet.
                </p>
              )}
            </Card>
          </section>

          <section className="mt-6">
            <Card title="Mark my call">
              <div className="grid gap-3 md:grid-cols-[140px_1fr] md:items-end">
                <label className="text-sm text-gray-300">
                  <div className="mb-1 text-xs uppercase tracking-wide text-gray-500">Size</div>
                  <input
                    className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-white outline-none"
                    inputMode="decimal"
                    value={sizeUsdc}
                    onChange={(event) => setSizeUsdc(event.target.value)}
                  />
                </label>
                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                    className="rounded border border-emerald-700/60 bg-emerald-900/30 px-4 py-2 text-sm text-emerald-200 disabled:opacity-50"
                    disabled={!m?.mid || journalCall.isPending}
                    onClick={() => journalCall.mutate("YES")}
                  >
                    Mark YES @ {m?.mid != null ? `${(m.mid * 100).toFixed(1)}c` : "--"}
                  </button>
                  <button
                    type="button"
                    className="rounded border border-rose-700/60 bg-rose-900/30 px-4 py-2 text-sm text-rose-200 disabled:opacity-50"
                    disabled={!m?.mid || journalCall.isPending}
                    onClick={() => journalCall.mutate("NO")}
                  >
                    Mark NO @ {m?.mid != null ? `${((1 - m.mid) * 100).toFixed(1)}c` : "--"}
                  </button>
                </div>
              </div>
              <div className="mt-2 text-xs text-gray-500">
                Manual journal entries capture model probability, band, and market price at click time.
              </div>
              {journalCall.error && (
                <div className="mt-2 text-sm text-edge-bearish">
                  {(journalCall.error as Error).message}
                </div>
              )}
              {journalCall.data && (
                <div className="mt-2 text-sm text-edge-bullish">
                  Logged {journalCall.data.outcome} {journalCall.data.side.toLowerCase()} at{" "}
                  {(journalCall.data.entry_price * 100).toFixed(1)}c.
                </div>
              )}
            </Card>
          </section>

          <section className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card title="Signal decomposition">
              {m?.feature_attributions.length ? (
                <details className="group">
                  <summary className="cursor-pointer text-sm text-gray-300 marker:text-gray-500">
                    Expand feature contributions
                  </summary>
                  <div className="mt-3 space-y-2">
                    {m.feature_attributions.map((attribution) => (
                      <div
                        key={attribution.feature_name}
                        className="rounded border border-gray-800 bg-black/20 px-3 py-2 text-sm"
                      >
                        <div className="flex items-center justify-between gap-4">
                          <span className="text-gray-200">{attribution.label}</span>
                          <span
                            className={
                              attribution.score_contribution >= 0
                                ? "tabular text-edge-bullish"
                                : "tabular text-edge-bearish"
                            }
                          >
                            {fmtSigned(attribution.score_contribution)}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center justify-between gap-4 text-xs text-gray-500">
                          <span>Observed value {fmtFeatureValue(attribution)}</span>
                          <span>scaled {fmtSigned(attribution.transformed_value)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              ) : (
                <p className="text-sm text-gray-400">
                  Feature decomposition appears when ensemble refinement is active.
                </p>
              )}
            </Card>
            <Card title="Smart money & holder concentration">
              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-500">
                    Smart-money consensus
                  </div>
                  {smartMoney.data ? (
                    <>
                      <div className="mt-1 text-2xl font-semibold tabular text-white">
                        {(smartMoney.data.latest.consensus_score * 100).toFixed(0)}%{" "}
                        <span className="text-sm font-normal text-gray-400">
                          {smartMoney.data.latest.dominant_outcome}
                        </span>
                      </div>
                      <div className="text-xs text-gray-500">
                        {smartMoney.data.latest.sample_wallets} qualified wallets ·
                        {" "}
                        YES {smartMoney.data.latest.yes_wallets} / NO{" "}
                        {smartMoney.data.latest.no_wallets}
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        Net USDC{" "}
                        <span
                          className={
                            smartMoney.data.latest.net_size_usdc >= 0
                              ? "text-edge-bullish"
                              : "text-edge-bearish"
                          }
                        >
                          {smartMoney.data.latest.net_size_usdc >= 0 ? "+" : ""}
                          {smartMoney.data.latest.net_size_usdc.toFixed(0)}
                        </span>
                        {smartMoney.data.directional_delta_usdc != null && (
                          <>
                            {" · 24h Δ "}
                            <span
                              className={
                                smartMoney.data.directional_delta_usdc >= 0
                                  ? "text-edge-bullish"
                                  : "text-edge-bearish"
                              }
                            >
                              {smartMoney.data.directional_delta_usdc >= 0 ? "+" : ""}
                              {smartMoney.data.directional_delta_usdc.toFixed(0)}
                            </span>
                          </>
                        )}
                      </div>
                    </>
                  ) : (
                    <p className="mt-1 text-xs text-gray-500">
                      No qualified smart-money positions yet for this market.
                    </p>
                  )}
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-500">
                    Holder concentration
                  </div>
                  {concentration.data ? (
                    <>
                      <div className="mt-1 text-2xl font-semibold tabular text-white">
                        {concentration.data.max_gini != null
                          ? concentration.data.max_gini.toFixed(2)
                          : "-"}
                        <span className="ml-2 text-sm font-normal text-gray-400">
                          Gini
                        </span>
                      </div>
                      <div className="text-xs text-gray-500">
                        YES top-1{" "}
                        {concentration.data.yes_top1_pct != null
                          ? `${(concentration.data.yes_top1_pct * 100).toFixed(0)}%`
                          : "-"}
                        {" · NO top-1 "}
                        {concentration.data.no_top1_pct != null
                          ? `${(concentration.data.no_top1_pct * 100).toFixed(0)}%`
                          : "-"}
                      </div>
                      {concentration.data.any_whale_flag && (
                        <div className="mt-1 text-xs text-yellow-300">
                          Whale alert: single wallet {">"} 40% of an outcome
                        </div>
                      )}
                      {concentration.data.max_gini != null &&
                        concentration.data.max_gini > 0.6 && (
                          <div className="mt-1 text-xs text-gray-500">
                            Smart-money consensus is down-weighted in the ensemble
                            when concentration {">"} 0.6.
                          </div>
                        )}
                    </>
                  ) : (
                    <p className="mt-1 text-xs text-gray-500">
                      No holder snapshot yet for this market.
                    </p>
                  )}
                </div>
              </div>
            </Card>
            <Card title="Point-in-time snapshot">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                <dt className="text-gray-400">active</dt>
                <dd className="tabular">{String(data.active)}</dd>
                <dt className="text-gray-400">closed</dt>
                <dd className="tabular">{String(data.closed)}</dd>
                <dt className="text-gray-400">volume</dt>
                <dd className="tabular">${data.volume_usdc.toLocaleString()}</dd>
                <dt className="text-gray-400">liquidity</dt>
                <dd className="tabular">${data.liquidity_usdc.toLocaleString()}</dd>
                <dt className="text-gray-400">open interest</dt>
                <dd className="tabular">${data.open_interest_usdc.toLocaleString()}</dd>
                <dt className="text-gray-400">end_date</dt>
                <dd className="tabular">{data.end_date ?? "-"}</dd>
                <dt className="text-gray-400">observed_at</dt>
                <dd className="tabular">{data.observed_at}</dd>
              </dl>
            </Card>
          </section>
        </>
      )}
    </main>
  );
}

function HistoryChart({ points }: { points: MarketHistoryPoint[] }) {
  const width = 960;
  const height = 280;
  const padX = 18;
  const padY = 18;
  const innerWidth = width - padX * 2;
  const innerHeight = height - padY * 2;
  const minTs = new Date(points[0].event_time).getTime();
  const maxTs = new Date(points[points.length - 1].event_time).getTime();
  const spanTs = Math.max(maxTs - minTs, 1);
  const x = (ts: string) =>
    padX + ((new Date(ts).getTime() - minTs) / spanTs) * innerWidth;
  const y = (value: number | null) =>
    padY + (1 - Math.min(Math.max(value ?? 0, 0), 1)) * innerHeight;
  const buildPath = (selector: (point: MarketHistoryPoint) => number | null) => {
    let started = false;
    return points
      .flatMap((point) => {
        const value = selector(point);
        if (value == null) {
          started = false;
          return [];
        }
        const cmd = started ? "L" : "M";
        started = true;
        return `${cmd} ${x(point.event_time)} ${y(value)}`;
      })
      .join(" ");
  };
  const latest = points[points.length - 1];

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full overflow-visible">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line
              x1={padX}
              x2={width - padX}
              y1={y(tick)}
              y2={y(tick)}
              className="stroke-gray-800"
              strokeWidth="1"
            />
            <text x={0} y={y(tick) + 4} className="fill-gray-500 text-[11px]">
              {Math.round(tick * 100)}%
            </text>
          </g>
        ))}
        <path d={buildPath((point) => point.market_mid)} fill="none" stroke="#d1d5db" strokeWidth="2" />
        <path d={buildPath((point) => point.model_prob)} fill="none" stroke="#38bdf8" strokeWidth="2.5" />
        <circle cx={x(latest.event_time)} cy={y(latest.market_mid)} r="3" fill="#d1d5db" />
        {latest.model_prob != null && (
          <circle cx={x(latest.event_time)} cy={y(latest.model_prob)} r="3.5" fill="#38bdf8" />
        )}
      </svg>
      <div className="mt-2 flex items-center justify-between gap-4 text-xs text-gray-500">
        <span>{fmtDateTime(points[0].event_time)}</span>
        <span>{fmtDateTime(points[points.length - 1].event_time)}</span>
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
      <div className="mb-2 text-xs uppercase tracking-wide text-gray-500">{title}</div>
      {children}
    </div>
  );
}

function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "accent" | "neutral" | "warn" | "bull" | "bear";
}) {
  const toneClasses =
    tone === "warn"
      ? "border-yellow-700/60 bg-yellow-900/30 text-yellow-200"
      : tone === "accent"
        ? "border-sky-700/60 bg-sky-900/30 text-sky-200"
        : tone === "bull"
          ? "border-emerald-700/60 bg-emerald-900/30 text-emerald-200"
          : tone === "bear"
            ? "border-rose-700/60 bg-rose-900/30 text-rose-200"
            : "border-gray-700 bg-gray-800/60 text-gray-300";
  return (
    <span className={`rounded border px-2 py-0.5 text-xs ${toneClasses}`}>
      {children}
    </span>
  );
}

function DistributionChart({ samples }: { samples: Array<[number, number]> }) {
  const width = 960;
  const height = 180;
  const padX = 18;
  const padY = 18;
  const innerWidth = width - padX * 2;
  const innerHeight = height - padY * 2;

  // X axis is percentile (0 to 1)
  // Y axis is value (often 0 to 1 or prices)
  const minV = Math.min(...samples.map(s => s[1]), 0);
  const maxV = Math.max(...samples.map(s => s[1]), 1);
  const spanV = Math.max(maxV - minV, 1e-9);

  const x = (pct: number) => padX + pct * innerWidth;
  const y = (val: number) => padY + (1 - (val - minV) / spanV) * innerHeight;

  let started = false;
  const pathD = samples.map(([pct, val]) => {
     const cmd = started ? "L" : "M";
     started = true;
     return `${cmd} ${x(pct)} ${y(val)}`;
  }).join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full overflow-visible mt-2">
      <path d={pathD} fill="none" stroke="#818cf8" strokeWidth="2" />
      {samples.map(([pct, val], i) => (
        <circle key={i} cx={x(pct)} cy={y(val)} r="2" fill="#818cf8" />
      ))}
      <line x1={padX} y1={height - padY} x2={width - padX} y2={height - padY} stroke="#374151" />
      <line x1={padX} y1={padY} x2={padX} y2={height - padY} stroke="#374151" />
      <text x={padX} y={height - 2} className="fill-gray-500 text-[10px]">0th pct</text>
      <text x={width - padX - 30} y={height - 2} className="fill-gray-500 text-[10px]">100th pct</text>
    </svg>
  );
}

