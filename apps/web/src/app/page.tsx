"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { fetchMarkets, type MarketRow } from "@/lib/api";

function fmtBps(bps: number | null): string {
  if (bps == null) return "—";
  const sign = bps >= 0 ? "+" : "";
  return `${sign}${bps.toFixed(0)} bps`;
}

function fmtPct(p: number | null): string {
  if (p == null) return "—";
  return `${(p * 100).toFixed(1)}%`;
}

function fmtUsd(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
}

function fmtTTR(secs: number | null): string {
  if (secs == null) return "—";
  const d = Math.floor(secs / 86_400);
  if (d > 1) return `${d}d`;
  const h = Math.floor(secs / 3_600);
  if (h > 1) return `${h}h`;
  return `${Math.max(0, Math.floor(secs / 60))}m`;
}

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["markets"],
    queryFn: fetchMarkets,
    refetchInterval: 5_000,
  });

  return (
    <main className="mx-auto max-w-6xl p-6">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">PolyPredictor</h1>
        <p className="text-sm text-gray-400">
          Crypto &amp; finance Polymarket markets with model-vs-market edge.
        </p>
      </header>

      {isLoading && <p className="text-gray-400">Loading markets…</p>}
      {error && <p className="text-edge-bearish">Failed to load: {(error as Error).message}</p>}

      {data && data.length === 0 && (
        <div className="rounded border border-gray-800 bg-gray-900/40 p-4 text-sm text-gray-400">
          No markets yet. The ingestion pipeline needs a first Gamma discovery pass — run{" "}
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
                <tr key={m.condition_id} className="border-t border-gray-800 hover:bg-gray-900/40">
                  <td className="px-3 py-2">
                    <Link
                      href={`/markets/${encodeURIComponent(m.condition_id)}`}
                      className="text-white hover:underline"
                    >
                      {m.question}
                    </Link>
                    <div className="text-xs text-gray-500">{m.category ?? "—"}</div>
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
