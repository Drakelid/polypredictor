"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  createBetaInvite,
  fetchBacktestSnapshot,
  fetchBetaInvites,
  fetchDriftSnapshot,
  fetchPrivacyPreferences,
  fetchPushPreferences,
  fetchJournalCalls,
  fetchJournalSummary,
  fetchMarkets,
  fetchPolymarketAddress,
  fetchPolymarketClobCredentials,
  fetchSignals,
  fetchTuningProfile,
  submitBetaFeedback,
  type BacktestCalibrationPoint,
  type BacktestReplayRow,
  type BetaInviteSummary,
  type DriftMetricRow,
  type FeatureDriftMetricRow,
  updatePushPreferences,
  updateTuningProfile,
  type JournalCall,
  type MarketRow,
  type PolymarketClobCredentialStatus,
  type PrivacyPreferences,
  type PushPreferences,
  type SignalEvent,
  type TuningProfile,
  updatePolymarketAddress,
  updatePolymarketClobCredentials,
  updatePrivacyPreferences,
} from "@/lib/api";

function fmtBps(bps: number | null): string {
  if (bps == null) return "-";
  const sign = bps >= 0 ? "+" : "";
  return `${sign}${bps.toFixed(0)} bps`;
}

function fmtPct(p: number | null): string {
  if (p == null) return "-";
  return `${(p * 100).toFixed(1)}%`;
}

function fmtMetric(value: number | null, digits = 3): string {
  if (value == null) return "-";
  return value.toFixed(digits);
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

const SIGNAL_TYPE_OPTIONS = [
  "whale_open",
  "whale_resize",
  "whale_close",
  "arb",
  "external_divergence",
  "large_print",
  "book_shock",
] as const;

const TUNING_SLIDERS = [
  {
    key: "smart_money",
    label: "Smart-money tilt",
    description: "Follow qualified-wallet consensus more or less aggressively.",
  },
  {
    key: "sibling_prior",
    label: "Sibling prior",
    description: "Lean more or less on ordered-threshold and sibling-market priors.",
  },
  {
    key: "holder_concentration",
    label: "Whale caution",
    description: "Shrink edges harder when holder concentration is elevated.",
  },
  {
    key: "resolution_risk",
    label: "Resolution-risk caution",
    description: "Pull probabilities back toward 50/50 on higher resolution-risk markets.",
  },
  {
    key: "adversarial_flow",
    label: "Adversarial-flow caution",
    description: "Pull probabilities back when flow looks manipulative or the book is thin.",
  },
] as const;

const TUNING_PRESETS: Array<{ preset: TuningProfile["preset"]; label: string }> = [
  { preset: "conservative", label: "Conservative" },
  { preset: "balanced", label: "Balanced" },
  { preset: "aggressive", label: "Aggressive" },
];

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const [cryptoOnly, setCryptoOnly] = useState(true);
  const [showTour, setShowTour] = useState(false);
  const [signalType, setSignalType] = useState("all");
  const [signalConditionId, setSignalConditionId] = useState("");
  const [signalMinSeverity, setSignalMinSeverity] = useState("0");
  const [backtestMarketType, setBacktestMarketType] = useState("all");
  const [backtestRegime, setBacktestRegime] = useState("all");
  const [prefsForm, setPrefsForm] = useState<PushPreferences | null>(null);
  const [privacyForm, setPrivacyForm] = useState<PrivacyPreferences | null>(null);
  const [tuningForm, setTuningForm] = useState<TuningProfile | null>(null);
  const [polymarketAddressInput, setPolymarketAddressInput] = useState("");
  const [clobForm, setClobForm] = useState<{
    api_key: string;
    api_secret: string;
    passphrase: string;
    proxy_wallet: string;
  }>({
    api_key: "",
    api_secret: "",
    passphrase: "",
    proxy_wallet: "",
  });
  const [inviteForm, setInviteForm] = useState({ email: "", display_name: "" });
  const [feedbackForm, setFeedbackForm] = useState<{
    kind: "bug" | "idea" | "model" | "data" | "other";
    message: string;
    contact_email: string;
  }>({ kind: "other", message: "", contact_email: "" });
  const { data, isLoading, error } = useQuery({
    queryKey: ["markets", cryptoOnly],
    queryFn: () => fetchMarkets({ cryptoOnly }),
    refetchInterval: 5_000,
  });
  const betaInvites = useQuery({
    queryKey: ["beta-invites"],
    queryFn: fetchBetaInvites,
  });
  const pushPrefs = useQuery({
    queryKey: ["push-preferences"],
    queryFn: fetchPushPreferences,
  });
  const privacyPrefs = useQuery({
    queryKey: ["privacy-preferences"],
    queryFn: fetchPrivacyPreferences,
  });
  const polymarketAddress = useQuery({
    queryKey: ["polymarket-address"],
    queryFn: fetchPolymarketAddress,
  });
  const polymarketClobCredentials = useQuery({
    queryKey: ["polymarket-clob-credentials"],
    queryFn: fetchPolymarketClobCredentials,
  });
  const tuningProfile = useQuery({
    queryKey: ["tuning-profile"],
    queryFn: fetchTuningProfile,
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
  const signals = useQuery({
    queryKey: ["signals", signalType, signalConditionId, signalMinSeverity],
    queryFn: () =>
      fetchSignals({
        lookbackHours: 48,
        limit: 25,
        eventTypes: signalType === "all" ? undefined : [signalType],
        conditionId: signalConditionId.trim() || undefined,
        minSeverity:
          signalMinSeverity === "0" ? undefined : Number.parseFloat(signalMinSeverity),
      }),
    refetchInterval: 30_000,
  });
  const driftSnapshot = useQuery({
    queryKey: ["drift-monitor"],
    queryFn: fetchDriftSnapshot,
    refetchInterval: 60_000,
  });
  const backtestSnapshot = useQuery({
    queryKey: ["backtest-walk-forward"],
    queryFn: () => fetchBacktestSnapshot(90, 24, 500),
    refetchInterval: 60_000,
  });
  const savePushPrefs = useMutation({
    mutationFn: (payload: Omit<PushPreferences, "updated_at">) =>
      updatePushPreferences(payload),
    onSuccess: async (next) => {
      setPrefsForm(next);
      await queryClient.invalidateQueries({ queryKey: ["push-preferences"] });
    },
  });
  const savePrivacyPrefs = useMutation({
    mutationFn: (payload: Omit<PrivacyPreferences, "updated_at">) =>
      updatePrivacyPreferences(payload),
    onSuccess: async (next) => {
      setPrivacyForm(next);
      await queryClient.invalidateQueries({ queryKey: ["privacy-preferences"] });
    },
  });
  const saveTuningProfile = useMutation({
    mutationFn: (payload: {
      preset: TuningProfile["preset"];
      log_odds_shifts: Record<string, number>;
    }) => updateTuningProfile(payload),
    onSuccess: async (next) => {
      setTuningForm(next);
      await queryClient.invalidateQueries({ queryKey: ["tuning-profile"] });
      await queryClient.invalidateQueries({ queryKey: ["markets"] });
      await queryClient.invalidateQueries({ queryKey: ["journal-summary"] });
      await queryClient.invalidateQueries({ queryKey: ["backtest-walk-forward"] });
    },
  });
  const savePolymarketAddress = useMutation({
    mutationFn: (proxyWallet: string | null) => updatePolymarketAddress(proxyWallet),
    onSuccess: async (next) => {
      setPolymarketAddressInput(next.proxy_wallet ?? "");
      await queryClient.invalidateQueries({ queryKey: ["polymarket-address"] });
    },
  });
  const savePolymarketClobCredentials = useMutation({
    mutationFn: (payload: {
      api_key: string | null;
      api_secret: string | null;
      passphrase: string | null;
      proxy_wallet: string | null;
    }) => updatePolymarketClobCredentials(payload),
    onSuccess: async (next) => {
      setClobForm({
        api_key: "",
        api_secret: "",
        passphrase: "",
        proxy_wallet: next.proxy_wallet ?? "",
      });
      await queryClient.invalidateQueries({ queryKey: ["polymarket-clob-credentials"] });
    },
  });
  const saveBetaInvite = useMutation({
    mutationFn: createBetaInvite,
    onSuccess: async () => {
      setInviteForm({ email: "", display_name: "" });
      await queryClient.invalidateQueries({ queryKey: ["beta-invites"] });
    },
  });
  const sendFeedback = useMutation({
    mutationFn: submitBetaFeedback,
    onSuccess: () => {
      setFeedbackForm((current) => ({ ...current, message: "" }));
    },
  });

  useEffect(() => {
    setShowTour(window.localStorage.getItem("polypredictor:onboarding-tour") !== "done");
  }, []);

  useEffect(() => {
    if (pushPrefs.data) {
      setPrefsForm(pushPrefs.data);
    }
  }, [pushPrefs.data]);

  useEffect(() => {
    if (privacyPrefs.data) {
      setPrivacyForm(privacyPrefs.data);
    }
  }, [privacyPrefs.data]);

  useEffect(() => {
    if (tuningProfile.data) {
      setTuningForm(tuningProfile.data);
    }
  }, [tuningProfile.data]);

  useEffect(() => {
    if (polymarketAddress.data) {
      setPolymarketAddressInput(polymarketAddress.data.proxy_wallet ?? "");
    }
  }, [polymarketAddress.data]);

  useEffect(() => {
    if (polymarketClobCredentials.data) {
      setClobForm((current) => ({
        api_key: "",
        api_secret: "",
        passphrase: "",
        proxy_wallet:
          current.proxy_wallet.trim() || polymarketClobCredentials.data.proxy_wallet || "",
      }));
    }
  }, [polymarketClobCredentials.data]);

  const filteredBacktestRows = (backtestSnapshot.data?.rows ?? []).filter(
    (row) =>
      (backtestMarketType === "all" || row.market_type === backtestMarketType) &&
      (backtestRegime === "all" || (row.regime ?? "none") === backtestRegime),
  );
  const backtestCalibrationPoints = calibrationPointsFromReplayRows(filteredBacktestRows);
  const backtestMarketTypes = Array.from(
    new Set((backtestSnapshot.data?.rows ?? []).map((row) => row.market_type)),
  ).sort();
  const backtestRegimes = Array.from(
    new Set((backtestSnapshot.data?.rows ?? []).map((row) => row.regime ?? "none")),
  ).sort();

  return (
    <main className="mx-auto max-w-6xl p-6">
      <header className="mb-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">PolyPredictor</h1>
            <p className="text-sm text-gray-400">
              Crypto &amp; finance Polymarket markets with model-vs-market edge.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-gray-200"
              onClick={() => setShowTour(true)}
            >
              Tour
            </button>
            <Link
              href="/status"
              className="rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-gray-200"
            >
              Status
            </Link>
          </div>
        </div>
      </header>

      {showTour && (
        <section className="mb-6 rounded border border-sky-800 bg-sky-950/20 p-4">
          <div className="mb-3 flex items-center justify-between gap-4">
            <div>
              <div className="text-sm font-semibold text-sky-100">Beta tour</div>
              <div className="text-xs text-sky-300">
                Market dashboard, detail pages, signal feed, and journal workflow.
              </div>
            </div>
            <button
              type="button"
              className="rounded border border-sky-700 px-3 py-1 text-xs text-sky-100"
              onClick={() => {
                window.localStorage.setItem("polypredictor:onboarding-tour", "done");
                setShowTour(false);
              }}
            >
              Done
            </button>
          </div>
          <div className="grid gap-3 text-sm text-sky-100 md:grid-cols-4">
            <TourStep title="Dashboard" body="Scan crypto markets by model edge and risk badge." />
            <TourStep title="Detail" body="Open a market for probability bands, drivers, and journal actions." />
            <TourStep title="Signals" body="Filter whale flow, arb, large prints, and book shocks by weight." />
            <TourStep title="Journal" body="Track calls, calibration, realized edge, and linked wallet sync." />
          </div>
        </section>
      )}

      <section className="mb-6 grid gap-4 md:grid-cols-[minmax(0,1fr)_360px]">
        <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
          <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
            <span>Market Scope</span>
            <span>{cryptoOnly ? "crypto default" : "all tracked markets"}</span>
          </div>
          <label className="flex items-start gap-3 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={cryptoOnly}
              onChange={(event) => setCryptoOnly(event.target.checked)}
            />
            <span>
              Crypto-only filter
              <span className="mt-1 block text-xs text-gray-500">
                Closed beta starts with crypto markets by default; turn this off to include the broader finance set.
              </span>
            </span>
          </label>
        </div>

        <BetaInviteCard
          summary={betaInvites.data}
          form={inviteForm}
          onFormChange={setInviteForm}
          onSubmit={() =>
            saveBetaInvite.mutate({
              email: inviteForm.email,
              display_name: inviteForm.display_name || null,
            })
          }
          isSaving={saveBetaInvite.isPending}
          error={saveBetaInvite.error as Error | null}
        />
      </section>

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

          {journalSummary.data.resolution_sync && (
            <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
              <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
                <span>Resolution Sync</span>
                <span>
                  {journalSummary.data.resolution_sync.verified_at
                    ? `linked ${new Date(journalSummary.data.resolution_sync.verified_at).toLocaleString()}`
                    : "linked"}
                </span>
              </div>
              <div className="mb-4 text-sm text-gray-400">
                Wallet {journalSummary.data.resolution_sync.proxy_wallet.slice(0, 8)}...
                {journalSummary.data.resolution_sync.proxy_wallet.slice(-6)} now feeds
                redeemable-position and earnings checks into the journal loop.
              </div>
              <div className="grid gap-4 md:grid-cols-4">
                <StatCard
                  label="Redeemable"
                  value={String(journalSummary.data.resolution_sync.redeemable_positions)}
                />
                <StatCard
                  label="Earnings"
                  value={fmtUsd(journalSummary.data.resolution_sync.total_earnings_usdc)}
                  tone={
                    journalSummary.data.resolution_sync.total_earnings_usdc >= 0
                      ? "bull"
                      : "bear"
                  }
                />
                <StatCard
                  label="Open positions"
                  value={String(journalSummary.data.resolution_sync.open_positions)}
                />
                <StatCard
                  label="Current value"
                  value={fmtUsd(journalSummary.data.resolution_sync.total_position_value_usdc)}
                />
              </div>
            </div>
          )}

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

      {prefsForm && privacyForm && (
        <section className="mb-6">
          {tuningForm && (
            <div className="mb-4 rounded border border-gray-800 bg-gray-900/40 p-4">
              <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
                <span>Model Tuning</span>
                <span>
                  {tuningForm.updated_at
                    ? `saved ${new Date(tuningForm.updated_at).toLocaleString()}`
                    : "using default balanced profile"}
                </span>
              </div>
              <div className="mb-4 flex flex-wrap gap-2">
                {TUNING_PRESETS.map((preset) => (
                  <button
                    key={preset.preset}
                    type="button"
                    className={`rounded border px-3 py-1.5 text-sm ${
                      tuningForm.preset === preset.preset
                        ? "border-sky-700/60 bg-sky-900/30 text-sky-200"
                        : "border-gray-700 bg-black/20 text-gray-300"
                    }`}
                    onClick={() =>
                      saveTuningProfile.mutate({
                        preset: preset.preset,
                        log_odds_shifts: {},
                      })
                    }
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
              <div className="space-y-4">
                {TUNING_SLIDERS.map((slider) => {
                  const value = tuningForm.log_odds_shifts[slider.key] ?? 0;
                  return (
                    <label key={slider.key} className="block">
                      <div className="mb-1 flex items-center justify-between gap-3 text-sm text-gray-300">
                        <span>{slider.label}</span>
                        <span className="tabular text-gray-500">{value.toFixed(2)}</span>
                      </div>
                      <input
                        type="range"
                        min={-1}
                        max={1}
                        step={0.05}
                        value={value}
                        onChange={(event) =>
                          setTuningForm({
                            ...tuningForm,
                            preset: "custom",
                            name: "Custom",
                            log_odds_shifts: {
                              ...tuningForm.log_odds_shifts,
                              [slider.key]: Number.parseFloat(event.target.value),
                            },
                          })
                        }
                        className="w-full accent-sky-400"
                      />
                      <div className="mt-1 text-xs text-gray-500">{slider.description}</div>
                    </label>
                  );
                })}
              </div>
              <div className="mt-4 flex items-center gap-2">
                <button
                  type="button"
                  className="rounded border border-sky-700/60 bg-sky-900/30 px-4 py-2 text-sm text-sky-200 disabled:opacity-50"
                  disabled={saveTuningProfile.isPending}
                  onClick={() =>
                    saveTuningProfile.mutate({
                      preset: tuningForm.preset,
                      log_odds_shifts: tuningForm.log_odds_shifts,
                    })
                  }
                >
                  {saveTuningProfile.isPending ? "Saving..." : "Save tuning"}
                </button>
                <div className="text-xs text-gray-500">
                  These controls add a post-model log-odds shift only. Learned ensemble weights do not change.
                </div>
              </div>
              {saveTuningProfile.error && (
                <div className="mt-2 text-sm text-edge-bearish">
                  {(saveTuningProfile.error as Error).message}
                </div>
              )}
            </div>
          )}

          <div className="mb-4 rounded border border-gray-800 bg-gray-900/40 p-4">
            <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
              <span>Polymarket Address</span>
              <span>
                {polymarketAddress.data?.verified_at
                  ? `verified ${new Date(polymarketAddress.data.verified_at).toLocaleString()}`
                  : "not linked"}
              </span>
            </div>
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]">
              <label className="block text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">Proxy Wallet</div>
                <input
                  value={polymarketAddressInput}
                  onChange={(event) => setPolymarketAddressInput(event.target.value)}
                  placeholder="0x..."
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                />
              </label>
              <div className="flex items-end gap-2">
                <button
                  type="button"
                  className="rounded border border-sky-700/60 bg-sky-900/30 px-4 py-2 text-sm text-sky-200 disabled:opacity-50"
                  disabled={savePolymarketAddress.isPending}
                  onClick={() =>
                    savePolymarketAddress.mutate(polymarketAddressInput.trim() || null)
                  }
                >
                  {savePolymarketAddress.isPending ? "Saving..." : "Save address"}
                </button>
                <button
                  type="button"
                  className="rounded border border-gray-700 bg-black/20 px-4 py-2 text-sm text-gray-300 disabled:opacity-50"
                  disabled={savePolymarketAddress.isPending}
                  onClick={() => savePolymarketAddress.mutate(null)}
                >
                  Clear
                </button>
              </div>
            </div>
            <div className="mt-4 text-xs text-gray-500">
              Read-only public account sync via positions, trades, and earnings. No trading keys are used in this flow.
            </div>
            {polymarketAddress.data?.summary && (
              <div className="mt-4 grid gap-4 md:grid-cols-4">
                <StatCard
                  label="Open positions"
                  value={String(polymarketAddress.data.summary.open_positions)}
                />
                <StatCard
                  label="Redeemable"
                  value={String(polymarketAddress.data.summary.redeemable_positions)}
                />
                <StatCard
                  label="Current value"
                  value={fmtUsd(polymarketAddress.data.summary.total_position_value_usdc)}
                />
                <StatCard
                  label="Total earnings"
                  value={fmtUsd(polymarketAddress.data.summary.total_earnings_usdc)}
                />
              </div>
            )}
            {polymarketAddress.data?.summary?.recent_trades?.length ? (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-gray-500">
                    <tr>
                      <th className="px-2 py-2 text-left">Market</th>
                      <th className="px-2 py-2 text-right">Outcome</th>
                      <th className="px-2 py-2 text-right">Side</th>
                      <th className="px-2 py-2 text-right">Price</th>
                      <th className="px-2 py-2 text-right">Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {polymarketAddress.data.summary.recent_trades.slice(0, 5).map((trade, idx) => (
                      <tr
                        key={`${trade.trade_id ?? "trade"}-${idx}`}
                        className="border-t border-gray-800"
                      >
                        <td className="px-2 py-2">
                          {trade.condition_id ? (
                            <Link
                              href={`/markets/${encodeURIComponent(trade.condition_id)}`}
                              className="hover:underline"
                            >
                              {trade.condition_id}
                            </Link>
                          ) : (
                            "-"
                          )}
                        </td>
                        <td className="px-2 py-2 text-right">{trade.outcome ?? "-"}</td>
                        <td className="px-2 py-2 text-right">{trade.side ?? "-"}</td>
                        <td className="px-2 py-2 text-right tabular">
                          {trade.price != null ? `${(trade.price * 100).toFixed(1)}c` : "-"}
                        </td>
                        <td className="px-2 py-2 text-right tabular">
                          {trade.size != null ? fmtUsd(trade.size) : "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {savePolymarketAddress.error && (
              <div className="mt-2 text-sm text-edge-bearish">
                {(savePolymarketAddress.error as Error).message}
              </div>
            )}
          </div>

          <div className="mb-4 rounded border border-gray-800 bg-gray-900/40 p-4">
            <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
              <span>Polymarket CLOB Credentials</span>
              <span>
                {polymarketClobCredentials.data?.configured
                  ? polymarketClobCredentials.data.rotated_at
                    ? `rotated ${new Date(polymarketClobCredentials.data.rotated_at).toLocaleString()}`
                    : "configured"
                  : "not configured"}
              </span>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">API Key</div>
                <input
                  value={clobForm.api_key}
                  onChange={(event) =>
                    setClobForm({ ...clobForm, api_key: event.target.value })
                  }
                  placeholder="pk_live_..."
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                />
              </label>
              <label className="block text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">API Secret</div>
                <input
                  type="password"
                  value={clobForm.api_secret}
                  onChange={(event) =>
                    setClobForm({ ...clobForm, api_secret: event.target.value })
                  }
                  placeholder="secret"
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                />
              </label>
              <label className="block text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">Passphrase</div>
                <input
                  type="password"
                  value={clobForm.passphrase}
                  onChange={(event) =>
                    setClobForm({ ...clobForm, passphrase: event.target.value })
                  }
                  placeholder="passphrase"
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                />
              </label>
              <label className="block text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">
                  Proxy Wallet (optional)
                </div>
                <input
                  value={clobForm.proxy_wallet}
                  onChange={(event) =>
                    setClobForm({ ...clobForm, proxy_wallet: event.target.value })
                  }
                  placeholder="0x..."
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                />
              </label>
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="rounded border border-sky-700/60 bg-sky-900/30 px-4 py-2 text-sm text-sky-200 disabled:opacity-50"
                disabled={savePolymarketClobCredentials.isPending}
                onClick={() =>
                  savePolymarketClobCredentials.mutate({
                    api_key: clobForm.api_key.trim() || null,
                    api_secret: clobForm.api_secret.trim() || null,
                    passphrase: clobForm.passphrase.trim() || null,
                    proxy_wallet: clobForm.proxy_wallet.trim() || null,
                  })
                }
              >
                {savePolymarketClobCredentials.isPending ? "Saving..." : "Save credentials"}
              </button>
              <button
                type="button"
                className="rounded border border-gray-700 bg-black/20 px-4 py-2 text-sm text-gray-300 disabled:opacity-50"
                disabled={savePolymarketClobCredentials.isPending}
                onClick={() =>
                  savePolymarketClobCredentials.mutate({
                    api_key: null,
                    api_secret: null,
                    passphrase: null,
                    proxy_wallet: null,
                  })
                }
              >
                Clear
              </button>
              {polymarketClobCredentials.data?.configured && (
                <Badge tone="accent">encrypted at rest</Badge>
              )}
            </div>
            <div className="mt-4 text-xs text-gray-500">
              Stored only for the optional user WebSocket path. Secrets are encrypted
              at rest, never returned by the API, and never used for trading endpoints
              in v1.
            </div>
            {polymarketClobCredentials.data?.configured && (
              <div className="mt-2 text-xs text-gray-500">
                {clobCredentialStatusText(polymarketClobCredentials.data)}
              </div>
            )}
            {savePolymarketClobCredentials.error && (
              <div className="mt-2 text-sm text-edge-bearish">
                {(savePolymarketClobCredentials.error as Error).message}
              </div>
            )}
          </div>

          <div className="mb-4 rounded border border-gray-800 bg-gray-900/40 p-4">
            <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
              <span>Privacy</span>
              <span>
                {privacyForm.updated_at
                  ? `saved ${new Date(privacyForm.updated_at).toLocaleString()}`
                  : "default off"}
              </span>
            </div>
            <label className="flex items-start gap-3 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={privacyForm.cross_user_learning_opt_in}
                onChange={(event) =>
                  setPrivacyForm({
                    ...privacyForm,
                    cross_user_learning_opt_in: event.target.checked,
                  })
                }
              />
              <span>
                Opt in to cross-user learning via anonymized differential-privacy aggregates.
                <span className="mt-1 block text-xs text-gray-500">
                  Default is off. When enabled, only noisy grouped summaries may leave your account; raw individual calls do not.
                </span>
              </span>
            </label>
            <div className="mt-4 flex items-center justify-between gap-3">
              <div className="text-xs text-gray-500">
                Aggregation is bucketed, k-anonymous, and differentially private before persistence or readout.
              </div>
              <button
                type="button"
                className="rounded border border-sky-700/60 bg-sky-900/30 px-4 py-2 text-sm text-sky-200 disabled:opacity-50"
                disabled={savePrivacyPrefs.isPending}
                onClick={() =>
                  savePrivacyPrefs.mutate({
                    cross_user_learning_opt_in: privacyForm.cross_user_learning_opt_in,
                  })
                }
              >
                {savePrivacyPrefs.isPending ? "Saving..." : "Save privacy"}
              </button>
            </div>
            {savePrivacyPrefs.error && (
              <div className="mt-2 text-sm text-edge-bearish">
                {(savePrivacyPrefs.error as Error).message}
              </div>
            )}
          </div>

          <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
            <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
              <span>Push Preferences</span>
              <span>
                {prefsForm.updated_at
                  ? `saved ${new Date(prefsForm.updated_at).toLocaleString()}`
                  : "not saved yet"}
              </span>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-3">
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input
                    type="checkbox"
                    checked={prefsForm.email_enabled}
                    onChange={(event) =>
                      setPrefsForm({ ...prefsForm, email_enabled: event.target.checked })
                    }
                  />
                  Email alerts
                </label>
                <label className="block text-xs text-gray-400">
                  <div className="mb-1 uppercase tracking-wide text-gray-500">Email To</div>
                  <input
                    value={prefsForm.email_to ?? ""}
                    onChange={(event) =>
                      setPrefsForm({ ...prefsForm, email_to: event.target.value })
                    }
                    placeholder="alerts@example.com"
                    className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                  />
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-300">
                  <input
                    type="checkbox"
                    checked={prefsForm.webhook_enabled}
                    onChange={(event) =>
                      setPrefsForm({ ...prefsForm, webhook_enabled: event.target.checked })
                    }
                  />
                  Webhook alerts
                </label>
                <label className="block text-xs text-gray-400">
                  <div className="mb-1 uppercase tracking-wide text-gray-500">Webhook URL</div>
                  <input
                    value={prefsForm.webhook_url ?? ""}
                    onChange={(event) =>
                      setPrefsForm({ ...prefsForm, webhook_url: event.target.value })
                    }
                    placeholder="https://example.com/hooks/polypredictor"
                    className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                  />
                </label>
              </div>
              <div className="space-y-3">
                <label className="block text-xs text-gray-400">
                  <div className="mb-1 uppercase tracking-wide text-gray-500">Min Weight</div>
                  <select
                    value={String(prefsForm.min_severity)}
                    onChange={(event) =>
                      setPrefsForm({
                        ...prefsForm,
                        min_severity: Number.parseFloat(event.target.value),
                      })
                    }
                    className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                  >
                    <option value="1">1.0x+</option>
                    <option value="2">2.0x+</option>
                    <option value="3">3.0x+</option>
                  </select>
                </label>
                <div>
                  <div className="mb-1 text-xs uppercase tracking-wide text-gray-500">
                    Signal Types
                  </div>
                  <div className="grid gap-2 md:grid-cols-2">
                    {SIGNAL_TYPE_OPTIONS.map((type) => {
                      const checked = prefsForm.event_types.includes(type);
                      return (
                        <label key={type} className="flex items-center gap-2 text-sm text-gray-300">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(event) =>
                              setPrefsForm({
                                ...prefsForm,
                                event_types: event.target.checked
                                  ? [...prefsForm.event_types, type]
                                  : prefsForm.event_types.filter((value) => value !== type),
                              })
                            }
                          />
                          {signalTypeLabel(type)}
                        </label>
                      );
                    })}
                  </div>
                </div>
                <label className="block text-xs text-gray-400">
                  <div className="mb-1 uppercase tracking-wide text-gray-500">
                    Market Scope
                  </div>
                  <input
                    value={prefsForm.condition_ids.join(", ")}
                    onChange={(event) =>
                      setPrefsForm({
                        ...prefsForm,
                        condition_ids: event.target.value
                          .split(",")
                          .map((value) => value.trim())
                          .filter(Boolean),
                      })
                    }
                    placeholder="leave blank for all markets"
                    className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                  />
                </label>
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between gap-3">
              <div className="text-xs text-gray-500">
                Preferences are stored per user for email and webhook delivery. Browser push stays deferred to M7.
              </div>
              <button
                type="button"
                className="rounded border border-sky-700/60 bg-sky-900/30 px-4 py-2 text-sm text-sky-200 disabled:opacity-50"
                disabled={savePushPrefs.isPending}
                onClick={() =>
                  savePushPrefs.mutate({
                    email_enabled: prefsForm.email_enabled,
                    email_to: prefsForm.email_to,
                    webhook_enabled: prefsForm.webhook_enabled,
                    webhook_url: prefsForm.webhook_url,
                    min_severity: prefsForm.min_severity,
                    event_types: prefsForm.event_types,
                    condition_ids: prefsForm.condition_ids,
                  })
                }
              >
                {savePushPrefs.isPending ? "Saving..." : "Save preferences"}
              </button>
            </div>
            {savePushPrefs.error && (
              <div className="mt-2 text-sm text-edge-bearish">
                {(savePushPrefs.error as Error).message}
              </div>
            )}
          </div>
        </section>
      )}

      {signals.data && (
        <section className="mb-6">
          <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
            <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
              <span>Signal Feed</span>
              <span>{signals.data.length} events · last 48h</span>
            </div>
            <div className="mb-4 grid gap-3 md:grid-cols-3">
              <label className="text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">Market</div>
                <input
                  value={signalConditionId}
                  onChange={(event) => setSignalConditionId(event.target.value)}
                  placeholder="condition id"
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
                />
              </label>
              <label className="text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">Signal Type</div>
                <select
                  value={signalType}
                  onChange={(event) => setSignalType(event.target.value)}
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                >
                  <option value="all">All types</option>
                  <option value="whale_open">Whale open</option>
                  <option value="whale_resize">Whale resize</option>
                  <option value="whale_close">Whale close</option>
                  <option value="arb">No-arb violation</option>
                  <option value="external_divergence">External divergence</option>
                  <option value="large_print">Large print</option>
                  <option value="book_shock">Book imbalance</option>
                </select>
              </label>
              <label className="text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">Min Weight</div>
                <select
                  value={signalMinSeverity}
                  onChange={(event) => setSignalMinSeverity(event.target.value)}
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                >
                  <option value="0">All severities</option>
                  <option value="1">1.0x+</option>
                  <option value="2">2.0x+</option>
                  <option value="3">3.0x+</option>
                </select>
              </label>
            </div>
            {signals.data.length === 0 ? (
              <div className="rounded border border-gray-800 bg-black/20 px-3 py-4 text-sm text-gray-500">
                No signals matched the current filters.
              </div>
            ) : (
              <ul className="space-y-2">
                {signals.data.slice(0, 8).map((event) => (
                  <SignalFeedRow key={event.event_id} event={event} />
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      <section className="mb-6 rounded border border-gray-800 bg-gray-900/40 p-4">
        <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
          <span>Beta Feedback</span>
          <span>{sendFeedback.isSuccess ? "received" : "in-app capture"}</span>
        </div>
        <div className="grid gap-4 md:grid-cols-[180px_minmax(0,1fr)_220px]">
          <label className="block text-xs text-gray-400">
            <div className="mb-1 uppercase tracking-wide text-gray-500">Type</div>
            <select
              value={feedbackForm.kind}
              onChange={(event) =>
                setFeedbackForm({
                  ...feedbackForm,
                  kind: event.target.value as typeof feedbackForm.kind,
                })
              }
              className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none"
            >
              <option value="bug">Bug</option>
              <option value="idea">Idea</option>
              <option value="model">Model</option>
              <option value="data">Data</option>
              <option value="other">Other</option>
            </select>
          </label>
          <label className="block text-xs text-gray-400">
            <div className="mb-1 uppercase tracking-wide text-gray-500">Message</div>
            <textarea
              value={feedbackForm.message}
              onChange={(event) =>
                setFeedbackForm({ ...feedbackForm, message: event.target.value })
              }
              rows={3}
              placeholder="What happened, what felt unclear, or what should change?"
              className="w-full resize-none rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
            />
          </label>
          <label className="block text-xs text-gray-400">
            <div className="mb-1 uppercase tracking-wide text-gray-500">Contact</div>
            <input
              value={feedbackForm.contact_email}
              onChange={(event) =>
                setFeedbackForm({ ...feedbackForm, contact_email: event.target.value })
              }
              placeholder="optional email"
              className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
            />
          </label>
        </div>
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="text-xs text-gray-500">
            Feedback is stored with the current page URL so beta issues can be traced quickly.
          </div>
          <button
            type="button"
            className="rounded border border-sky-700/60 bg-sky-900/30 px-4 py-2 text-sm text-sky-200 disabled:opacity-50"
            disabled={sendFeedback.isPending || !feedbackForm.message.trim()}
            onClick={() =>
              sendFeedback.mutate({
                kind: feedbackForm.kind,
                message: feedbackForm.message,
                page_url: window.location.href,
                contact_email: feedbackForm.contact_email || null,
              })
            }
          >
            {sendFeedback.isPending ? "Sending..." : "Send feedback"}
          </button>
        </div>
        {sendFeedback.error && (
          <div className="mt-2 text-sm text-edge-bearish">
            {(sendFeedback.error as Error).message}
          </div>
        )}
      </section>

      {driftSnapshot.data && driftSnapshot.data.model_metrics.length > 0 && (
        <section className="mb-6">
          <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
            <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
              <span>Drift Monitor</span>
              <span>
                {driftSnapshot.data.observed_at
                  ? `snapshot ${new Date(driftSnapshot.data.observed_at).toLocaleString()}`
                  : "no snapshots yet"}
              </span>
            </div>
            <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
              <div className="space-y-4">
                {["7d", "30d", "90d"].map((windowLabel) => {
                  const rows = driftSnapshot.data!.model_metrics.filter(
                    (row) =>
                      row.window_label === windowLabel &&
                      row.market_type !== "overall" &&
                      row.ttr_bucket !== "overall",
                  );
                  if (rows.length === 0) return null;
                  return (
                    <div key={windowLabel} className="rounded border border-gray-800 bg-black/20 p-3">
                      <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
                        <span>{windowLabel} cells</span>
                        <span>{rows.length} strata</span>
                      </div>
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead className="text-gray-500">
                            <tr>
                              <th className="px-2 py-2 text-left">Type</th>
                              <th className="px-2 py-2 text-left">Regime</th>
                              <th className="px-2 py-2 text-left">TTR</th>
                              <th className="px-2 py-2 text-right">Skill</th>
                              <th className="px-2 py-2 text-right">ECE</th>
                              <th className="px-2 py-2 text-right">Coverage</th>
                              <th className="px-2 py-2 text-right">N</th>
                            </tr>
                          </thead>
                          <tbody>
                            {rows.map((row) => (
                              <DriftRow key={`${row.window_label}-${row.market_type}-${row.regime ?? "none"}-${row.ttr_bucket}`} row={row} />
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="rounded border border-gray-800 bg-black/20 p-3">
                <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
                  <span>Feature Drift Alerts</span>
                  <span>
                    {
                      driftSnapshot.data.feature_metrics.filter((row) => row.is_alert).length
                    }{" "}
                    alerting
                  </span>
                </div>
                <div className="space-y-2">
                  {driftSnapshot.data.feature_metrics.slice(0, 8).map((row) => (
                    <FeatureDriftRow key={row.feature_name} row={row} />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>
      )}

      {backtestSnapshot.data && backtestSnapshot.data.rows.length > 0 && (
        <section className="mb-6">
          <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
            <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
              <span>Walk-Forward Backtest</span>
              <span>
                {backtestSnapshot.data.total_samples} samples · {backtestSnapshot.data.lookback_days}d · {backtestSnapshot.data.horizon_hours}h horizon
              </span>
            </div>
            {backtestSnapshot.data.tuning_comparison && (
              <div className="mb-4 rounded border border-gray-800 bg-black/20 p-4">
                <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
                  <span>Tuning Counterfactual</span>
                  <span>
                    {backtestSnapshot.data.tuning_comparison.profile_name} Â· {backtestSnapshot.data.tuning_comparison.total_samples} aligned samples
                  </span>
                </div>
                <div className="mb-4 grid gap-4 md:grid-cols-3">
                  <StatCard
                    label="Default Brier"
                    value={fmtMetric(backtestSnapshot.data.tuning_comparison.default_brier)}
                  />
                  <StatCard
                    label="Tuned Brier"
                    value={fmtMetric(backtestSnapshot.data.tuning_comparison.tuned_brier)}
                    tone={
                      backtestSnapshot.data.tuning_comparison.tuned_brier <=
                      backtestSnapshot.data.tuning_comparison.default_brier
                        ? "bull"
                        : "bear"
                    }
                  />
                  <StatCard
                    label="Delta"
                    value={fmtMetric(backtestSnapshot.data.tuning_comparison.brier_delta)}
                    tone={
                      backtestSnapshot.data.tuning_comparison.brier_delta <= 0
                        ? "bull"
                        : "bear"
                    }
                  />
                </div>
                <div className="grid gap-4 lg:grid-cols-2">
                  <div className="rounded border border-gray-800 bg-gray-900/30 p-4">
                    <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">
                      Default Calibration
                    </div>
                    <CalibrationChart
                      points={backtestSnapshot.data.tuning_comparison.default_calibration_points}
                    />
                  </div>
                  <div className="rounded border border-gray-800 bg-gray-900/30 p-4">
                    <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">
                      Tuned Calibration
                    </div>
                    <CalibrationChart
                      points={backtestSnapshot.data.tuning_comparison.tuned_calibration_points}
                    />
                  </div>
                </div>
              </div>
            )}
            <div className="mb-4 grid gap-3 md:grid-cols-2">
              <label className="text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">Market Type</div>
                <select
                  value={backtestMarketType}
                  onChange={(event) => setBacktestMarketType(event.target.value)}
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                >
                  <option value="all">All types</option>
                  {backtestMarketTypes.map((value) => (
                    <option key={value} value={value}>
                      {typeLabel(value)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-gray-400">
                <div className="mb-1 uppercase tracking-wide text-gray-500">Regime</div>
                <select
                  value={backtestRegime}
                  onChange={(event) => setBacktestRegime(event.target.value)}
                  className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                >
                  <option value="all">All regimes</option>
                  {backtestRegimes.map((value) => (
                    <option key={value} value={value}>
                      {value === "none" ? "Unknown" : value}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="grid gap-4 lg:grid-cols-[1.1fr_1.4fr]">
              <div className="rounded border border-gray-800 bg-black/20 p-4">
                <div className="mb-3 text-xs uppercase tracking-wide text-gray-500">
                  Historical Calibration
                </div>
                <CalibrationChart points={backtestCalibrationPoints} />
              </div>
              <div className="rounded border border-gray-800 bg-black/20 p-4">
                <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
                  <span>Resolved-Market Drilldown</span>
                  <span>{filteredBacktestRows.length} rows</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-gray-500">
                      <tr>
                        <th className="px-2 py-2 text-left">Market</th>
                        <th className="px-2 py-2 text-left">Type</th>
                        <th className="px-2 py-2 text-left">Regime</th>
                        <th className="px-2 py-2 text-right">Pred</th>
                        <th className="px-2 py-2 text-right">Outcome</th>
                        <th className="px-2 py-2 text-right">Brier</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredBacktestRows.slice(0, 12).map((row) => (
                        <tr key={`${row.condition_id}-${row.asked_at}`} className="border-t border-gray-800">
                          <td className="px-2 py-2">
                            <Link
                              href={`/markets/${encodeURIComponent(row.condition_id)}`}
                              className="hover:underline"
                            >
                              {row.condition_id}
                            </Link>
                            <div className="text-xs text-gray-500">
                              {new Date(row.resolved_at).toLocaleDateString()}
                            </div>
                          </td>
                          <td className="px-2 py-2">{typeLabel(row.market_type)}</td>
                          <td className="px-2 py-2">{row.regime ?? "unknown"}</td>
                          <td className="px-2 py-2 text-right tabular">{fmtPct(row.predicted_prob)}</td>
                          <td className="px-2 py-2 text-right tabular">{row.outcome ? "YES" : "NO"}</td>
                          <td className="px-2 py-2 text-right tabular">{fmtMetric(row.brier_contribution)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
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
                      {m.concentration_whale_flag && <Badge tone="warn">whale</Badge>}
                      {typeof m.concentration_score === "number" && (
                        <Badge
                          tone={m.concentration_score > 0.6 ? "warn" : "neutral"}
                        >
                          gini {(m.concentration_score ?? 0).toFixed(2)}
                        </Badge>
                      )}
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

function DriftRow({ row }: { row: DriftMetricRow }) {
  const skillTone =
    row.brier_skill == null ? "text-gray-400" : row.brier_skill >= 0 ? "text-edge-bullish" : "text-edge-bearish";
  return (
    <tr className="border-t border-gray-800">
      <td className="px-2 py-2">{typeLabel(row.market_type)}</td>
      <td className="px-2 py-2">{row.regime ?? "all"}</td>
      <td className="px-2 py-2">{row.ttr_bucket}</td>
      <td className={`px-2 py-2 text-right tabular ${skillTone}`}>{fmtMetric(row.brier_skill)}</td>
      <td className="px-2 py-2 text-right tabular">{fmtMetric(row.ece)}</td>
      <td className="px-2 py-2 text-right tabular">{fmtPct(row.coverage)}</td>
      <td className="px-2 py-2 text-right tabular">{row.sample_count}</td>
    </tr>
  );
}

function FeatureDriftRow({ row }: { row: FeatureDriftMetricRow }) {
  return (
    <div className="rounded border border-gray-800 bg-gray-900/30 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="truncate">{row.feature_name}</span>
        <Badge tone={row.is_alert ? "warn" : "neutral"}>
          PSI {row.psi.toFixed(3)}
        </Badge>
      </div>
      <div className="mt-1 text-xs text-gray-500">
        KL {row.kl_divergence.toFixed(3)} · ref {row.reference_count} · live {row.current_count}
      </div>
    </div>
  );
}

function TourStep({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded border border-sky-800/70 bg-black/20 p-3">
      <div className="font-medium">{title}</div>
      <div className="mt-1 text-xs leading-5 text-sky-200">{body}</div>
    </div>
  );
}

function BetaInviteCard({
  summary,
  form,
  onFormChange,
  onSubmit,
  isSaving,
  error,
}: {
  summary: BetaInviteSummary | undefined;
  form: { email: string; display_name: string };
  onFormChange: (next: { email: string; display_name: string }) => void;
  onSubmit: () => void;
  isSaving: boolean;
  error: Error | null;
}) {
  return (
    <div className="rounded border border-gray-800 bg-gray-900/40 p-4">
      <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-wide text-gray-500">
        <span>Closed Beta</span>
        <span>
          {summary
            ? `${summary.invited_count}/${summary.target_count} invited`
            : "loading"}
        </span>
      </div>
      <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <input
          value={form.email}
          onChange={(event) => onFormChange({ ...form, email: event.target.value })}
          placeholder="invite@example.com"
          className="rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
        />
        <input
          value={form.display_name}
          onChange={(event) =>
            onFormChange({ ...form, display_name: event.target.value })
          }
          placeholder="name optional"
          className="rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-600"
        />
      </div>
      <div className="mt-3 flex items-center justify-between gap-3">
        <div className="text-xs text-gray-500">
          {summary
            ? `${summary.accepted_count} accepted · ${summary.remaining_slots} slots left`
            : "Tracking the initial invited-user cohort."}
        </div>
        <button
          type="button"
          className="rounded border border-sky-700/60 bg-sky-900/30 px-3 py-2 text-sm text-sky-200 disabled:opacity-50"
          disabled={isSaving || !form.email.trim()}
          onClick={onSubmit}
        >
          {isSaving ? "Adding..." : "Add invite"}
        </button>
      </div>
      {summary?.invites.length ? (
        <div className="mt-3 max-h-28 space-y-1 overflow-auto text-xs text-gray-400">
          {summary.invites.slice(0, 5).map((invite) => (
            <div key={invite.id} className="flex items-center justify-between gap-3">
              <span className="truncate">{invite.email}</span>
              <span>{invite.status}</span>
            </div>
          ))}
        </div>
      ) : null}
      {error && <div className="mt-2 text-sm text-edge-bearish">{error.message}</div>}
    </div>
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

function calibrationPointsFromReplayRows(
  rows: BacktestReplayRow[],
): BacktestCalibrationPoint[] {
  const bins = 10;
  const bucketPredictions: number[][] = Array.from({ length: bins }, () => []);
  const bucketOutcomes: number[][] = Array.from({ length: bins }, () => []);
  for (const row of rows) {
    const clipped = Math.min(Math.max(row.predicted_prob, 0), 1 - 1e-9);
    const idx = Math.min(Math.floor(clipped * bins), bins - 1);
    bucketPredictions[idx].push(clipped);
    bucketOutcomes[idx].push(row.outcome);
  }
  const points: BacktestCalibrationPoint[] = [];
  for (let idx = 0; idx < bins; idx += 1) {
    if (bucketPredictions[idx].length === 0) continue;
    const avgPredicted =
      bucketPredictions[idx].reduce((sum, value) => sum + value, 0) /
      bucketPredictions[idx].length;
    const hitRate =
      bucketOutcomes[idx].reduce((sum, value) => sum + value, 0) /
      bucketOutcomes[idx].length;
    points.push({
      bucket_mid: (idx + 0.5) / bins,
      avg_predicted: avgPredicted,
      hit_rate: hitRate,
      count: bucketPredictions[idx].length,
    });
  }
  return points;
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

function signalTypeLabel(type: string): string {
  switch (type) {
    case "whale_open":
      return "Whale open";
    case "whale_resize":
      return "Whale resize";
    case "whale_close":
      return "Whale close";
    case "arb":
      return "No-arb violation";
    case "external_divergence":
      return "External divergence";
    case "large_print":
      return "Large print";
    case "book_shock":
      return "Book imbalance";
    default:
      return type;
  }
}

function fmtDeltaUsdc(value: number | null): string {
  if (value == null) return "-";
  const sign = value >= 0 ? "+" : "";
  if (Math.abs(value) >= 1_000_000) return `${sign}$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${sign}$${(value / 1_000).toFixed(1)}k`;
  return `${sign}$${value.toFixed(0)}`;
}

function SignalFeedRow({ event }: { event: SignalEvent }) {
  const tone: "bull" | "bear" | "warn" | "neutral" =
    event.event_type === "whale_close"
      ? "warn"
      : event.direction === "yes"
        ? "bull"
        : event.direction === "no"
          ? "bear"
          : "neutral";
  const deltaClass =
    (event.size_delta_usdc ?? 0) >= 0 ? "text-edge-bullish" : "text-edge-bearish";
  return (
    <li className="flex items-center justify-between gap-3 rounded border border-gray-800 bg-black/20 px-3 py-2 text-sm">
      <div className="flex flex-1 items-center gap-3">
        <Badge tone={tone}>{signalTypeLabel(event.event_type)}</Badge>
        <Link
          href={`/markets/${encodeURIComponent(event.condition_id)}`}
          className="truncate text-gray-200 hover:underline"
          title={event.condition_id}
        >
          {event.condition_id}
        </Link>
        {event.direction !== "neutral" && (
          <span className="text-xs uppercase tracking-wide text-gray-500">
            {event.direction}
          </span>
        )}
        {event.actor && (
          <span className="truncate text-xs text-gray-500" title={event.actor}>
            {event.actor.slice(0, 10)}…
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        <span className={`tabular ${deltaClass}`}>
          {event.size_delta_usdc != null
            ? fmtDeltaUsdc(event.size_delta_usdc)
            : `${event.severity.toFixed(1)}x`}
        </span>
        <span className="text-xs text-gray-500" title={event.observed_at}>
          {new Date(event.observed_at).toLocaleTimeString(undefined, {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      </div>
    </li>
  );
}

function clobCredentialStatusText(status: PolymarketClobCredentialStatus): string {
  if (!status.configured) {
    return "No stored CLOB credentials yet.";
  }
  const created = status.created_at
    ? `created ${new Date(status.created_at).toLocaleString()}`
    : "created recently";
  const wallet = status.proxy_wallet ? ` for ${status.proxy_wallet}` : "";
  return `${created}${wallet}.`;
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
  return <span className={`rounded border px-2 py-0.5 text-xs ${toneClasses}`}>{children}</span>;
}
