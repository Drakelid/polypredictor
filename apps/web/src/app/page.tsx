"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { fetchJournalCalls, fetchJournalSummary, fetchMarkets, type JournalCall, type MarketRow } from "@/lib/api";

function fmtBps(bps: number | null): string {
  if (bps == null) return "-";
  const sign = bps >= 0 ? "+" : "";
  return `${sign}${bps.toFixed(0)} bps`;
}

function fmtPct(p: number | null): string {
  if (p == null) return "-";
  return `${(p * 100).toFixed(1)}%`;
}

function fmtUsd(n: number): string {
  if (n <= -1_000_000) return `-$${(Math.abs(n) / 1_000_000).toFixed(1)}M`;
  if (n <= -1_000) return `-$${(Math.abs(n) / 1_000).toFixed(1)}k`;
  if (n < 0) return `-$${Math.abs(n).toFixed(0)}`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
}

function fmtTTR(secs: number | null): string {
  if (secs == null) return "-";
  const d = Math.floor(secs / 86_400);
  if (d > 1) return `${d}d`;
  const h = Math.floor(secs / 3_600);
  if (h > 1) return `${h}h`;
  return `${Math.max(0, Math.floor(secs / 60))}m`;
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

function modelSourceLabel(source: string | undefined): string {
  switch (source) {
    case "ensemble":
      return "Ensemble";
    case "baseline":
      return "Baseline";
    default:
      return source ?? "-";
  }
}

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["markets"],
    queryFn: fetchMarkets,
    refetchInterval: 5_000,
  });
  const journalSummary = useQuery({
    queryKey: ["journal-summary"],
    queryFn: fetchJournalSummary,
    refetchInterval: 30_000,
  });
  const journalCalls = useQuery({
    queryKey: ["journal-calls"],
    queryFn: fetchJournalCalls,
    refetchInterval: 30_000,
  });

  return (
    <main className="mx-auto max-w-6xl p-6">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">PolyPredictor</h1>
        <p className="text-sm text-gray-400">
          Crypto &amp; finance Polymarket markets with model-vs-market edge.
        </p>
      </header>

      {journalSummary.data && (
        <section className="mb-6 space-y-4">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <StatCard label="Journal calls" value={String(journalSummary.data.total_calls)} />
            <StatCard label="Resolved" value={String(journalSummary.data.resolved_calls)} />
            <StatCard
              label="Total PnL"
              value={fmtUsd(journalSummary.data.total_pnl_usdc)}
              tone={journalSummary.data.total_pnl_usdc >= 0 ? "bull" : "bear"}
            />
            <StatCard
              label="Avg Brier"
              value={
                journalSummary.data.avg_brier != null
                  ? journalSummary.data.avg_brier.toFixed(3)
                  : "-"
              }
            />
            <StatCard label="Unresolved" value={String(journalSummary.data.unresolved_calls)} />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
              <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">
                Hit Rate By Confidence Bucket
              </div>
              <div className="space-y-2">
                {journalSummary.data.confidence_buckets.map((bucket) => (
                  <div key={bucket.label}>
                    <div className="mb-1 flex items-center justify-between text-xs text-gray-400">
                      <span>{bucket.label}</span>
                      <span>
                        {bucket.count} calls · {(bucket.hit_rate * 100).toFixed(0)}% hit
                      </span>
                    </div>
                    <div className="h-2 rounded bg-gray-800">
                      <div
                        className="h-2 rounded bg-sky-400"
                        style={{ width: `${bucket.hit_rate * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
              <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">
                Calibration Plot
              </div>
              <CalibrationChart points={journalSummary.data.calibration_points} />
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded border border-gray-800 bg-gray-900/40 p-4 md:col-span-2">
              <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">
                Edge Realized vs Edge Predicted
              </div>
              <EdgeScatter points={journalSummary.data.edge_scatter} />
            </div>
            <div className="space-y-4">
              <CallList title="Best Calls" calls={journalSummary.data.best_calls} />
              <CallList title="Worst Calls" calls={journalSummary.data.worst_calls} />
            </div>
          </div>

          {journalCalls.data && journalCalls.data.length > 0 && (
            <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
              <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">
                Recent Calls
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-gray-500">
                    <tr>
                      <th className="px-2 py-2 text-left">Market</th>
                      <th className="px-2 py-2 text-right">Call</th>
                      <th className="px-2 py-2 text-right">Entry</th>
                      <th className="px-2 py-2 text-right">Model</th>
                      <th className="px-2 py-2 text-right">Pred Edge</th>
                      <th className="px-2 py-2 text-right">PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {journalCalls.data.slice(0, 8).map((call: JournalCall) => (
                      <tr key={call.id} className="border-t border-gray-800">
                        <td className="px-2 py-2">
                          <Link
                            href={`/markets/${encodeURIComponent(call.condition_id)}`}
                            className="hover:underline"
                          >
                            {call.condition_id}
                          </Link>
                        </td>
                        <td className="px-2 py-2 text-right">{call.outcome}</td>
                        <td className="px-2 py-2 text-right tabular">
                          {(call.entry_price * 100).toFixed(1)}c
                        </td>
                        <td className="px-2 py-2 text-right tabular">
                          {(call.model_prob_at_call * 100).toFixed(1)}%
                        </td>
                        <td
                          className={`px-2 py-2 text-right tabular ${
                            call.predicted_edge_bps >= 0 ? "text-edge-bullish" : "text-edge-bearish"
                          }`}
                        >
                          {fmtBps(call.predicted_edge_bps)}
                        </td>
                        <td
                          className={`px-2 py-2 text-right tabular ${
                            (call.pnl_usdc ?? 0) >= 0 ? "text-edge-bullish" : "text-edge-bearish"
                          }`}
                        >
                          {call.pnl_usdc != null ? fmtUsd(call.pnl_usdc) : "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      )}

      {isLoading && <p className="text-gray-400">Loading markets...</p>}
      {error && <p className="text-edge-bearish">Failed to load: {(error as Error).message}</p>}

      {data && data.length === 0 && (
        <div className="rounded border border-gray-800 bg-gray-900/40 p-4 text-sm text-gray-400">
          No markets yet. The ingestion pipeline needs a first Gamma discovery pass - run{" "}
          <code className="rounded bg-gray-800 px-1 py-0.5 text-xs">make ingest-discover</code>{" "}
          and refresh.
        </div>
      )}

      {data && data.length > 0 && (
        <div className="overflow-x-auto rounded border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-400">
              <tr>
                <th className="px-3 py-2 text-left">Market</th>
                <th className="px-3 py-2 text-right">Mid</th>
                <th className="px-3 py-2 text-right">Model</th>
                <th className="px-3 py-2 text-right">Edge</th>
                <th className="px-3 py-2 text-right">Liquidity</th>
                <th className="px-3 py-2 text-right">Volume</th>
                <th className="px-3 py-2 text-right">TTR</th>
              </tr>
            </thead>
            <tbody>
              {data.map((m: MarketRow) => (
                <tr
                  key={m.condition_id}
                  className="border-t border-gray-800 hover:bg-gray-900/40"
                >
                  <td className="px-3 py-2">
                    <Link
                      href={`/markets/${encodeURIComponent(m.condition_id)}`}
                      className="text-white hover:underline"
                    >
                      {m.question}
                    </Link>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                      <span>{m.category ?? "-"}</span>
                      <Badge tone={m.model_source === "ensemble" ? "accent" : "neutral"}>
                        {modelSourceLabel(m.model_source)}
                      </Badge>
                      <Badge tone="neutral">{typeLabel(m.market_type)}</Badge>
                      {typeof m.confidence === "number" && (
                        <Badge tone={m.needs_review ? "warn" : "neutral"}>
                          conf {Math.round(m.confidence * 100)}%
                        </Badge>
                      )}
                      {m.needs_review && <Badge tone="warn">needs review</Badge>}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right tabular">{fmtPct(m.mid)}</td>
                  <td className="px-3 py-2 text-right tabular">{fmtPct(m.model_prob)}</td>
                  <td
                    className={`px-3 py-2 text-right tabular ${
                      (m.edge_bps ?? 0) >= 0 ? "text-edge-bullish" : "text-edge-bearish"
                    }`}
                  >
                    {fmtBps(m.edge_bps)}
                  </td>
                  <td className="px-3 py-2 text-right tabular">{fmtUsd(m.liquidity_usdc)}</td>
                  <td className="px-3 py-2 text-right tabular">{fmtUsd(m.volume_usdc)}</td>
                  <td className="px-3 py-2 text-right tabular">{fmtTTR(m.time_to_resolution_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function StatCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "bull" | "bear";
}) {
  const toneClass =
    tone === "bull" ? "text-edge-bullish" : tone === "bear" ? "text-edge-bearish" : "text-white";
  return (
    <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}

function CalibrationChart({
  points,
}: {
  points: Array<{ bucket_mid: number; avg_predicted: number; hit_rate: number; count: number }>;
}) {
  const width = 320;
  const height = 220;
  const pad = 20;
  const inner = width - pad * 2;
  const x = (value: number) => pad + value * inner;
  const y = (value: number) => height - pad - value * inner;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full">
      <line x1={pad} y1={height - pad} x2={width - pad} y2={pad} stroke="#374151" strokeDasharray="4 4" />
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="#1f2937" />
      <line x1={pad} y1={height - pad} x2={pad} y2={pad} stroke="#1f2937" />
      {points.map((point, idx) => (
        <circle
          key={idx}
          cx={x(point.avg_predicted)}
          cy={y(point.hit_rate)}
          r={Math.max(3, Math.min(8, point.count + 2))}
          fill="#38bdf8"
        />
      ))}
    </svg>
  );
}

function EdgeScatter({
  points,
}: {
  points: Array<{ predicted_edge_bps: number; realized_edge_bps: number }>;
}) {
  const width = 560;
  const height = 220;
  const pad = 20;
  const all = points.flatMap((point) => [point.predicted_edge_bps, point.realized_edge_bps]);
  const bound = Math.max(100, ...all.map((value) => Math.abs(value)), 100);
  const scale = (value: number, size: number) => pad + ((value + bound) / (bound * 2)) * (size - pad * 2);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full">
      <line x1={pad} y1={height / 2} x2={width - pad} y2={height / 2} stroke="#1f2937" />
      <line x1={width / 2} y1={pad} x2={width / 2} y2={height - pad} stroke="#1f2937" />
      {points.map((point, idx) => (
        <circle
          key={idx}
          cx={scale(point.predicted_edge_bps, width)}
          cy={height - scale(point.realized_edge_bps, height)}
          r="4"
          fill="#94a3b8"
        />
      ))}
    </svg>
  );
}

function CallList({
  title,
  calls,
}: {
  title: string;
  calls: Array<{ id: string; condition_id: string; outcome: string; pnl_usdc: number | null; predicted_edge_bps: number }>;
}) {
  return (
    <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
      <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">{title}</div>
      <div className="space-y-2 text-sm">
        {calls.length === 0 && <div className="text-gray-500">No resolved calls yet.</div>}
        {calls.map((call) => (
          <div key={call.id} className="rounded border border-gray-800 bg-black/20 px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <Link href={`/markets/${encodeURIComponent(call.condition_id)}`} className="hover:underline">
                {call.condition_id}
              </Link>
              <span className={(call.pnl_usdc ?? 0) >= 0 ? "text-edge-bullish" : "text-edge-bearish"}>
                {call.pnl_usdc != null ? fmtUsd(call.pnl_usdc) : "-"}
              </span>
            </div>
            <div className="mt-1 text-xs text-gray-500">
              {call.outcome} · predicted {fmtBps(call.predicted_edge_bps)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "accent" | "neutral" | "warn";
}) {
  const toneClasses =
    tone === "warn"
      ? "border-yellow-700/60 bg-yellow-900/30 text-yellow-200"
      : tone === "accent"
        ? "border-sky-700/60 bg-sky-900/30 text-sky-200"
        : "border-gray-700 bg-gray-800/60 text-gray-300";
  return <span className={`rounded border px-2 py-0.5 text-xs ${toneClasses}`}>{children}</span>;
}
