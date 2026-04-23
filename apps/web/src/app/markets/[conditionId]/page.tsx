"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchMarketAsOf, fetchMarketModel } from "@/lib/api";

// Market detail page. As of M1 the Model Probability card is live — the
// pipeline runs classify → baseline → display, and the badge shows which
// baseline produced the number (PRD §6.1 / tasks.md §1.3). SHAP / orderbook
// panels land in M2.

function fmtPct(p: number | null | undefined): string {
  if (p == null) return "—%";
  return `${(p * 100).toFixed(1)}%`;
}

function fmtBps(bps: number | null | undefined): string {
  if (bps == null) return "—";
  const sign = bps >= 0 ? "+" : "";
  return `${sign}${bps.toFixed(0)} bps`;
}

function sourceLabel(src: string | undefined): string {
  switch (src) {
    case "bs_one_touch":
      return "Black–Scholes barrier";
    case "bs_terminal":
      return "Black–Scholes terminal";
    case "range_conjunction":
      return "Range (lognormal)";
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
      return src ?? "—";
  }
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
      return t ?? "—";
  }
}

export default function MarketDetailPage() {
  const params = useParams<{ conditionId: string }>();
  const conditionId = decodeURIComponent(params.conditionId);

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

  const data = snapshot.data;
  const m = model.data;
  const edgeClass = (m?.edge_bps ?? 0) >= 0 ? "text-edge-bullish" : "text-edge-bearish";

  return (
    <main className="mx-auto max-w-6xl p-6">
      <Link href="/" className="text-sm text-gray-400 hover:underline">
        ← Back to dashboard
      </Link>

      {snapshot.isLoading && <p className="mt-6 text-gray-400">Loading…</p>}
      {snapshot.error && (
        <p className="mt-6 text-edge-bearish">
          Failed: {(snapshot.error as Error).message}
        </p>
      )}

      {data && (
        <>
          <header className="mt-4 mb-6">
            <h1 className="text-xl font-semibold text-white">{data.question}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
              <span className="tabular">{data.condition_id}</span>
              {m && (
                <>
                  <Badge tone="neutral">{typeLabel(m.market_type)}</Badge>
                  <Badge tone={m.needs_review ? "warn" : "neutral"}>
                    conf {Math.round(m.confidence * 100)}%
                  </Badge>
                  {m.needs_review && <Badge tone="warn">needs review</Badge>}
                </>
              )}
            </div>
          </header>

          <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Card title="Model probability">
              <div className="text-3xl font-semibold text-white">{fmtPct(m?.model_prob)}</div>
              <div className="text-xs text-gray-500">{sourceLabel(m?.baseline_source)}</div>
            </Card>
            <Card title="Market mid">
              <div className="text-3xl font-semibold tabular text-white">{fmtPct(m?.mid)}</div>
              <div className="text-xs text-gray-500">Live quote (YES leg)</div>
            </Card>
            <Card title="Edge">
              <div className={`text-3xl font-semibold tabular ${edgeClass}`}>
                {fmtBps(m?.edge_bps)}
              </div>
              <div className="text-xs text-gray-500">Model − mid</div>
            </Card>
          </section>

          {m && (m.reasons.length > 0 || m.classifier_reasons.length > 0) && (
            <section className="mt-4">
              <Card title="Why this number?">
                {m.classifier_reasons.length > 0 && (
                  <div className="text-sm text-gray-300">
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
                {m.reasons.length > 0 && (
                  <div className="mt-2 text-sm text-gray-300">
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      Baseline
                    </div>
                    <ul className="ml-4 list-disc text-gray-400">
                      {m.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="mt-3 text-xs text-gray-500">
                  SHAP-based top-3 drivers arrive in M2 once the ensemble is live.
                </div>
              </Card>
            </section>
          )}

          <section className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card title="Orderbook snapshot">
              <p className="text-sm text-gray-400">
                L2 book, recent trades, informed-vs-passive flow breakdown (M2).
              </p>
            </Card>
            <Card title="Signal decomposition">
              <p className="text-sm text-gray-400">
                SHAP-based top-3 drivers render here in M2.
              </p>
            </Card>
          </section>

          <section className="mt-6">
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
                <dd className="tabular">{data.end_date ?? "—"}</dd>
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
  tone?: "neutral" | "warn";
}) {
  const toneClasses =
    tone === "warn"
      ? "border-yellow-700/60 bg-yellow-900/30 text-yellow-200"
      : "border-gray-700 bg-gray-800/60 text-gray-300";
  return (
    <span className={`rounded border px-2 py-0.5 text-xs ${toneClasses}`}>
      {children}
    </span>
  );
}
