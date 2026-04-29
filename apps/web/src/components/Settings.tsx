"use client";

// Settings: every operator-console panel reskinned in the dashboard's design system.
// Tab nav across the top; each panel is a focused form/diagnostic view.

import Link from "next/link";
import type { Route } from "next";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type BacktestCalibrationPoint,
  type BacktestReplayRow,
  type DriftMetricRow,
  type FeatureDriftMetricRow,
  type JournalCall,
  type PaperTradingStatus,
  type PolymarketClobCredentialStatus,
  type PrivacyPreferences,
  type ProviderCredentialStatus,
  type PushPreferences,
  type SignalEvent,
  type TuningProfile,
  createBetaInvite,
  fetchBacktestSnapshot,
  fetchBetaInvites,
  fetchDriftSnapshot,
  fetchJournalCalls,
  fetchJournalSummary,
  fetchPaperTradingStatus,
  fetchPolymarketAddress,
  fetchPolymarketClobCredentials,
  fetchPrivacyPreferences,
  fetchProviderCredentials,
  fetchPushPreferences,
  fetchSignals,
  fetchTuningProfile,
  submitBetaFeedback,
  updatePaperTrading,
  updatePolymarketAddress,
  updatePolymarketClobCredentials,
  updatePrivacyPreferences,
  updateProviderCredentials,
  updatePushPreferences,
  updateTuningProfile,
} from "@/lib/api";

import {
  Badge,
  Card,
  Checkbox,
  ErrorText,
  FormRow,
  HintText,
  PrimaryButton,
  SecondaryButton,
  StatTile,
  Toggle,
  inputStyle,
  selectStyle,
  textareaStyle,
} from "./settings-ui";

// ---------------------------------------------------------------------------
// Constants & formatters (ported from console)
// ---------------------------------------------------------------------------

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

function fmtUsd(n: number): string {
  if (n <= -1_000_000) return `-$${(Math.abs(n) / 1_000_000).toFixed(1)}M`;
  if (n <= -1_000) return `-$${(Math.abs(n) / 1_000).toFixed(1)}k`;
  if (n < 0) return `-$${Math.abs(n).toFixed(0)}`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
}

function fmtBps(bps: number | null | undefined): string {
  if (bps == null) return "-";
  const sign = bps >= 0 ? "+" : "";
  return `${sign}${bps.toFixed(0)} bps`;
}

function fmtPct(p: number | null | undefined): string {
  if (p == null) return "-";
  return `${(p * 100).toFixed(1)}%`;
}

function fmtMetric(value: number | null | undefined, digits = 3): string {
  if (value == null) return "-";
  return value.toFixed(digits);
}

function fmtDeltaUsdc(value: number | null): string {
  if (value == null) return "-";
  const sign = value >= 0 ? "+" : "";
  if (Math.abs(value) >= 1_000_000) return `${sign}$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${sign}$${(value / 1_000).toFixed(1)}k`;
  return `${sign}$${value.toFixed(0)}`;
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

function clobCredentialStatusText(status: PolymarketClobCredentialStatus): string {
  if (!status.configured) return "No stored CLOB credentials yet.";
  const created = status.created_at
    ? `created ${new Date(status.created_at).toLocaleString()}`
    : "created recently";
  const wallet = status.proxy_wallet ? ` for ${status.proxy_wallet}` : "";
  return `${created}${wallet}.`;
}

function emptyCredentialValues(provider: ProviderCredentialStatus): Record<string, string> {
  return Object.fromEntries(provider.fields.map((field) => [field.name, ""]));
}

function calibrationPointsFromReplayRows(rows: BacktestReplayRow[]): BacktestCalibrationPoint[] {
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
      bucketPredictions[idx].reduce((s, v) => s + v, 0) / bucketPredictions[idx].length;
    const hitRate =
      bucketOutcomes[idx].reduce((s, v) => s + v, 0) / bucketOutcomes[idx].length;
    points.push({
      bucket_mid: (idx + 0.5) / bins,
      avg_predicted: avgPredicted,
      hit_rate: hitRate,
      count: bucketPredictions[idx].length,
    });
  }
  return points;
}

// ---------------------------------------------------------------------------
// Charts (theme-matched)
// ---------------------------------------------------------------------------

function CalibrationChart({ points }: { points: BacktestCalibrationPoint[] }) {
  const W = 320;
  const H = 220;
  const pad = 24;
  const inner = W - pad * 2;
  const x = (v: number) => pad + v * inner;
  const y = (v: number) => H - pad - v * inner;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", overflow: "visible" }}>
      {ticks.map((t) => (
        <g key={`x-${t}`}>
          <line x1={x(t)} y1={pad} x2={x(t)} y2={H - pad} stroke="var(--border)" strokeWidth="1" />
          <text x={x(t)} y={H - pad + 12} textAnchor="middle" fontSize="9" fill="var(--muted)">
            {(t * 100).toFixed(0)}%
          </text>
        </g>
      ))}
      {ticks.map((t) => (
        <g key={`y-${t}`}>
          <line x1={pad} y1={y(t)} x2={W - pad} y2={y(t)} stroke="var(--border)" strokeWidth="1" />
          <text x={pad - 6} y={y(t) + 4} textAnchor="end" fontSize="9" fill="var(--muted)">
            {(t * 100).toFixed(0)}%
          </text>
        </g>
      ))}
      <line
        x1={x(0)}
        y1={y(0)}
        x2={x(1)}
        y2={y(1)}
        stroke="var(--border2)"
        strokeWidth="1.5"
        strokeDasharray="5,4"
      />
      {points.map((p, idx) => (
        <circle
          key={idx}
          cx={x(p.avg_predicted)}
          cy={y(p.hit_rate)}
          r={Math.max(3, Math.min(9, Math.log2(p.count + 1) + 2))}
          fill="var(--violet)"
          opacity="0.85"
          stroke="var(--bg)"
          strokeWidth="1"
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
  const W = 560;
  const H = 220;
  const pad = 24;
  const all = points.flatMap((p) => [p.predicted_edge_bps, p.realized_edge_bps]);
  const bound = Math.max(100, ...all.map((v) => Math.abs(v)));
  const sx = (v: number) => pad + ((v + bound) / (bound * 2)) * (W - pad * 2);
  const sy = (v: number) => H - (pad + ((v + bound) / (bound * 2)) * (H - pad * 2));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", overflow: "visible" }}>
      <line x1={pad} y1={H / 2} x2={W - pad} y2={H / 2} stroke="var(--border)" />
      <line x1={W / 2} y1={pad} x2={W / 2} y2={H - pad} stroke="var(--border)" />
      <line
        x1={sx(-bound)}
        y1={sy(-bound)}
        x2={sx(bound)}
        y2={sy(bound)}
        stroke="var(--border2)"
        strokeDasharray="4 4"
        strokeWidth="1"
      />
      {points.map((p, idx) => (
        <circle
          key={idx}
          cx={sx(p.predicted_edge_bps)}
          cy={sy(p.realized_edge_bps)}
          r="3.5"
          fill={p.realized_edge_bps >= 0 ? "var(--green)" : "var(--red)"}
          opacity="0.8"
        />
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Tab nav
// ---------------------------------------------------------------------------

type Tab = "tuning" | "notifications" | "account" | "providers" | "journal" | "diagnostics" | "feedback";

const TABS: Array<{ id: Tab; label: string; sub?: string }> = [
  { id: "tuning", label: "Tuning" },
  { id: "notifications", label: "Notifications" },
  { id: "account", label: "Account" },
  { id: "providers", label: "Data providers" },
  { id: "journal", label: "Journal" },
  { id: "diagnostics", label: "Diagnostics" },
  { id: "feedback", label: "Beta & feedback" },
];

// ---------------------------------------------------------------------------
// Settings root
// ---------------------------------------------------------------------------

export default function Settings() {
  const [tab, setTab] = useState<Tab>("tuning");
  return (
    <div style={{ flex: 1, overflowY: "auto" }}>
      <div
        style={{
          padding: "10px 24px 0",
          position: "sticky",
          top: 0,
          background: "var(--bg)",
          zIndex: 5,
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", paddingBottom: 12 }}>
          {TABS.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                style={{
                  padding: "6px 14px",
                  borderRadius: 7,
                  border: "1px solid",
                  borderColor: active ? "var(--green)" : "var(--border)",
                  background: active ? "var(--green-dim)" : "var(--surface)",
                  color: active ? "var(--green)" : "var(--muted)",
                  fontSize: 12,
                  fontWeight: 500,
                  cursor: "pointer",
                  transition: "all .15s",
                }}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      </div>
      <div style={{ padding: "20px 24px 40px", display: "flex", flexDirection: "column", gap: 16 }}>
        {tab === "tuning" && <TuningPanel />}
        {tab === "notifications" && <NotificationsPanel />}
        {tab === "account" && <AccountPanel />}
        {tab === "providers" && <ProvidersPanel />}
        {tab === "journal" && <JournalPanel />}
        {tab === "diagnostics" && <DiagnosticsPanel />}
        {tab === "feedback" && <FeedbackPanel />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tuning + Privacy + Paper trading
// ---------------------------------------------------------------------------

function TuningPanel() {
  const queryClient = useQueryClient();
  const tuningProfileQ = useQuery({
    queryKey: ["tuning-profile"],
    queryFn: fetchTuningProfile,
  });
  const privacyPrefsQ = useQuery({
    queryKey: ["privacy-preferences"],
    queryFn: fetchPrivacyPreferences,
  });
  const paperTradingQ = useQuery({
    queryKey: ["paper-trading"],
    queryFn: fetchPaperTradingStatus,
  });

  const [tuningForm, setTuningForm] = useState<TuningProfile | null>(null);
  const [privacyForm, setPrivacyForm] = useState<PrivacyPreferences | null>(null);
  const [paperForm, setPaperForm] = useState<PaperTradingStatus | null>(null);

  useEffect(() => {
    if (tuningProfileQ.data) setTuningForm(tuningProfileQ.data);
  }, [tuningProfileQ.data]);
  useEffect(() => {
    if (privacyPrefsQ.data) setPrivacyForm(privacyPrefsQ.data);
  }, [privacyPrefsQ.data]);
  useEffect(() => {
    if (paperTradingQ.data) setPaperForm(paperTradingQ.data);
  }, [paperTradingQ.data]);

  const saveTuning = useMutation({
    mutationFn: (payload: { preset: TuningProfile["preset"]; log_odds_shifts: Record<string, number> }) =>
      updateTuningProfile(payload),
    onSuccess: async (next) => {
      setTuningForm(next);
      await queryClient.invalidateQueries({ queryKey: ["tuning-profile"] });
      await queryClient.invalidateQueries({ queryKey: ["markets-dashboard"] });
      await queryClient.invalidateQueries({ queryKey: ["journal-summary"] });
      await queryClient.invalidateQueries({ queryKey: ["backtest-walk-forward"] });
    },
  });

  const savePrivacy = useMutation({
    mutationFn: (payload: Omit<PrivacyPreferences, "updated_at">) => updatePrivacyPreferences(payload),
    onSuccess: async (next) => {
      setPrivacyForm(next);
      await queryClient.invalidateQueries({ queryKey: ["privacy-preferences"] });
    },
  });

  const savePaper = useMutation({
    mutationFn: (enabled: boolean) => updatePaperTrading(enabled),
    onSuccess: async (next) => {
      setPaperForm(next);
      await queryClient.invalidateQueries({ queryKey: ["paper-trading"] });
    },
  });

  return (
    <>
      <Card
        title="Model tuning"
        meta={
          tuningForm?.updated_at
            ? `saved ${new Date(tuningForm.updated_at).toLocaleString()}`
            : "default balanced"
        }
      >
        {tuningForm ? (
          <>
            <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
              {TUNING_PRESETS.map((p) => {
                const active = tuningForm.preset === p.preset;
                return (
                  <button
                    key={p.preset}
                    onClick={() => saveTuning.mutate({ preset: p.preset, log_odds_shifts: {} })}
                    style={{
                      padding: "6px 14px",
                      borderRadius: 7,
                      border: "1px solid",
                      borderColor: active ? "var(--green)" : "var(--border)",
                      background: active ? "var(--green-dim)" : "var(--surface2)",
                      color: active ? "var(--green)" : "var(--muted)",
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    {p.label}
                  </button>
                );
              })}
              {tuningForm.preset === "custom" && <Badge tone="warn">Custom</Badge>}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {TUNING_SLIDERS.map((slider) => {
                const value = tuningForm.log_odds_shifts[slider.key] ?? 0;
                return (
                  <div key={slider.key}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        marginBottom: 4,
                      }}
                    >
                      <span style={{ fontSize: 12, color: "var(--text)", fontWeight: 500 }}>
                        {slider.label}
                      </span>
                      <span
                        style={{
                          fontSize: 11,
                          color: value === 0 ? "var(--muted)" : value > 0 ? "var(--green)" : "var(--red)",
                          fontFamily:
                            "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace",
                          fontWeight: 600,
                        }}
                      >
                        {value > 0 ? "+" : ""}
                        {value.toFixed(2)}
                      </span>
                    </div>
                    <input
                      type="range"
                      min={-1}
                      max={1}
                      step={0.05}
                      value={value}
                      onChange={(e) =>
                        setTuningForm({
                          ...tuningForm,
                          preset: "custom",
                          name: "Custom",
                          log_odds_shifts: {
                            ...tuningForm.log_odds_shifts,
                            [slider.key]: parseFloat(e.target.value),
                          },
                        })
                      }
                      style={{ width: "100%", accentColor: "var(--green)", cursor: "pointer" }}
                    />
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {slider.description}
                    </div>
                  </div>
                );
              })}
            </div>
            <div style={{ marginTop: 14, display: "flex", gap: 8, alignItems: "center" }}>
              <PrimaryButton
                disabled={saveTuning.isPending}
                onClick={() =>
                  saveTuning.mutate({
                    preset: tuningForm.preset,
                    log_odds_shifts: tuningForm.log_odds_shifts,
                  })
                }
              >
                {saveTuning.isPending ? "Saving…" : "Save tuning"}
              </PrimaryButton>
              <HintText>Post-model log-odds shift only. Learned ensemble weights do not change.</HintText>
            </div>
            {saveTuning.error && <ErrorText>{(saveTuning.error as Error).message}</ErrorText>}
          </>
        ) : (
          <div style={{ color: "var(--muted)", fontSize: 12 }}>Loading tuning profile…</div>
        )}
      </Card>

      <Card
        title="Paper trading"
        meta={paperForm?.updated_at ? `saved ${new Date(paperForm.updated_at).toLocaleString()}` : "default off"}
      >
        {paperForm ? (
          <>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
              <Toggle
                checked={paperForm.enabled}
                onChange={(v) => setPaperForm({ ...paperForm, enabled: v })}
              />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>Enable paper trading mode</div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.5 }}>
                  New journal calls are tagged as paper trades — no real wallet actions, excluded from live PnL.
                  They still track model edge exactly like live trades using simulated entries.
                </div>
              </div>
            </div>
            <div style={{ marginTop: 12, display: "flex", justifyContent: "flex-end" }}>
              <PrimaryButton
                disabled={savePaper.isPending}
                onClick={() => savePaper.mutate(paperForm.enabled)}
              >
                {savePaper.isPending ? "Saving…" : "Save paper trading"}
              </PrimaryButton>
            </div>
            {savePaper.error && <ErrorText>{(savePaper.error as Error).message}</ErrorText>}
          </>
        ) : (
          <div style={{ color: "var(--muted)", fontSize: 12 }}>Loading…</div>
        )}
      </Card>

      <Card
        title="Privacy"
        meta={privacyForm?.updated_at ? `saved ${new Date(privacyForm.updated_at).toLocaleString()}` : "default off"}
      >
        {privacyForm ? (
          <>
            <Checkbox
              checked={privacyForm.cross_user_learning_opt_in}
              onChange={(v) =>
                setPrivacyForm({ ...privacyForm, cross_user_learning_opt_in: v })
              }
              label="Opt in to cross-user learning via anonymized differential-privacy aggregates."
              hint="Default off. When enabled, only noisy grouped summaries leave your account; raw individual calls do not. Aggregation is bucketed, k-anonymous, and differentially private before persistence or readout."
            />
            <div style={{ marginTop: 14, display: "flex", justifyContent: "flex-end" }}>
              <PrimaryButton
                disabled={savePrivacy.isPending}
                onClick={() =>
                  savePrivacy.mutate({
                    cross_user_learning_opt_in: privacyForm.cross_user_learning_opt_in,
                  })
                }
              >
                {savePrivacy.isPending ? "Saving…" : "Save privacy"}
              </PrimaryButton>
            </div>
            {savePrivacy.error && <ErrorText>{(savePrivacy.error as Error).message}</ErrorText>}
          </>
        ) : (
          <div style={{ color: "var(--muted)", fontSize: 12 }}>Loading…</div>
        )}
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------
// Notifications: push prefs + signal feed preview
// ---------------------------------------------------------------------------

function NotificationsPanel() {
  const queryClient = useQueryClient();
  const pushPrefsQ = useQuery({
    queryKey: ["push-preferences"],
    queryFn: fetchPushPreferences,
  });
  const [prefsForm, setPrefsForm] = useState<PushPreferences | null>(null);
  useEffect(() => {
    if (pushPrefsQ.data) setPrefsForm(pushPrefsQ.data);
  }, [pushPrefsQ.data]);

  const savePushPrefs = useMutation({
    mutationFn: (payload: Omit<PushPreferences, "updated_at">) => updatePushPreferences(payload),
    onSuccess: async (next) => {
      setPrefsForm(next);
      await queryClient.invalidateQueries({ queryKey: ["push-preferences"] });
    },
  });

  const [signalConditionId, setSignalConditionId] = useState("");
  const [signalType, setSignalType] = useState("all");
  const [signalMinSeverity, setSignalMinSeverity] = useState("0");
  const signalsQ = useQuery({
    queryKey: ["signals-settings", signalType, signalConditionId, signalMinSeverity],
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

  return (
    <>
      <Card
        title="Push preferences"
        meta={prefsForm?.updated_at ? `saved ${new Date(prefsForm.updated_at).toLocaleString()}` : "not saved yet"}
      >
        {prefsForm ? (
          <div style={{ display: "grid", gap: 18, gridTemplateColumns: "1fr 1fr" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Checkbox
                checked={prefsForm.email_enabled}
                onChange={(v) => setPrefsForm({ ...prefsForm, email_enabled: v })}
                label="Email alerts"
              />
              <FormRow label="Email recipient">
                <input
                  value={prefsForm.email_to ?? ""}
                  onChange={(e) => setPrefsForm({ ...prefsForm, email_to: e.target.value })}
                  placeholder="alerts@example.com"
                  style={inputStyle}
                />
              </FormRow>
              <Checkbox
                checked={prefsForm.webhook_enabled}
                onChange={(v) => setPrefsForm({ ...prefsForm, webhook_enabled: v })}
                label="Webhook alerts"
              />
              <FormRow label="Webhook URL">
                <input
                  value={prefsForm.webhook_url ?? ""}
                  onChange={(e) => setPrefsForm({ ...prefsForm, webhook_url: e.target.value })}
                  placeholder="https://example.com/hooks/polypredictor"
                  style={inputStyle}
                />
              </FormRow>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <FormRow label="Min weight">
                <select
                  value={String(prefsForm.min_severity)}
                  onChange={(e) =>
                    setPrefsForm({ ...prefsForm, min_severity: parseFloat(e.target.value) })
                  }
                  style={selectStyle}
                >
                  <option value="1">1.0×+</option>
                  <option value="2">2.0×+</option>
                  <option value="3">3.0×+</option>
                </select>
              </FormRow>
              <div>
                <div
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    color: "var(--muted)",
                    letterSpacing: ".08em",
                    marginBottom: 7,
                  }}
                >
                  Signal types
                </div>
                <div style={{ display: "grid", gap: 8, gridTemplateColumns: "1fr 1fr" }}>
                  {SIGNAL_TYPE_OPTIONS.map((type) => {
                    const checked = prefsForm.event_types.includes(type);
                    return (
                      <Checkbox
                        key={type}
                        checked={checked}
                        onChange={(v) =>
                          setPrefsForm({
                            ...prefsForm,
                            event_types: v
                              ? [...prefsForm.event_types, type]
                              : prefsForm.event_types.filter((t) => t !== type),
                          })
                        }
                        label={signalTypeLabel(type)}
                      />
                    );
                  })}
                </div>
              </div>
              <FormRow
                label="Market scope"
                hint="Comma-separated condition IDs. Leave blank for all markets."
              >
                <input
                  value={prefsForm.condition_ids.join(", ")}
                  onChange={(e) =>
                    setPrefsForm({
                      ...prefsForm,
                      condition_ids: e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="0x... , 0x..."
                  style={inputStyle}
                />
              </FormRow>
            </div>
            <div
              style={{
                gridColumn: "1 / -1",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
                marginTop: 4,
              }}
            >
              <HintText>Stored per user for email and webhook delivery. Browser push is deferred.</HintText>
              <PrimaryButton
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
                {savePushPrefs.isPending ? "Saving…" : "Save preferences"}
              </PrimaryButton>
            </div>
            {savePushPrefs.error && (
              <div style={{ gridColumn: "1 / -1" }}>
                <ErrorText>{(savePushPrefs.error as Error).message}</ErrorText>
              </div>
            )}
          </div>
        ) : (
          <div style={{ color: "var(--muted)", fontSize: 12 }}>Loading…</div>
        )}
      </Card>

      <Card
        title="Signal feed"
        meta={`${signalsQ.data?.length ?? 0} events · last 48h`}
      >
        <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(3, 1fr)", marginBottom: 12 }}>
          <FormRow label="Market">
            <input
              value={signalConditionId}
              onChange={(e) => setSignalConditionId(e.target.value)}
              placeholder="condition id"
              style={inputStyle}
            />
          </FormRow>
          <FormRow label="Signal type">
            <select value={signalType} onChange={(e) => setSignalType(e.target.value)} style={selectStyle}>
              <option value="all">All types</option>
              {SIGNAL_TYPE_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {signalTypeLabel(t)}
                </option>
              ))}
            </select>
          </FormRow>
          <FormRow label="Min weight">
            <select
              value={signalMinSeverity}
              onChange={(e) => setSignalMinSeverity(e.target.value)}
              style={selectStyle}
            >
              <option value="0">All severities</option>
              <option value="1">1.0×+</option>
              <option value="2">2.0×+</option>
              <option value="3">3.0×+</option>
            </select>
          </FormRow>
        </div>
        {(signalsQ.data ?? []).length === 0 ? (
          <div
            style={{
              padding: "20px 12px",
              fontSize: 12,
              color: "var(--muted)",
              textAlign: "center",
              border: "1px dashed var(--border)",
              borderRadius: 8,
            }}
          >
            No signals matched the current filters.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {(signalsQ.data ?? []).slice(0, 10).map((event) => (
              <SignalFeedRow key={event.event_id} event={event} />
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

function SignalFeedRow({ event }: { event: SignalEvent }) {
  const tone =
    event.event_type === "whale_close"
      ? "warn"
      : event.direction === "yes"
      ? "bull"
      : event.direction === "no"
      ? "bear"
      : "neutral";
  const deltaColor = (event.size_delta_usdc ?? 0) >= 0 ? "var(--green)" : "var(--red)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 12px",
        borderRadius: 7,
        background: "var(--surface2)",
        border: "1px solid var(--border)",
        fontSize: 12,
      }}
    >
      <Badge tone={tone}>{signalTypeLabel(event.event_type)}</Badge>
      <Link
        href={`/markets/${encodeURIComponent(event.condition_id)}` as Route}
        style={{
          flex: 1,
          color: "var(--text)",
          textDecoration: "none",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={event.condition_id}
      >
        {event.condition_id.slice(0, 12)}…{event.condition_id.slice(-6)}
      </Link>
      {event.direction !== "neutral" && (
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: ".08em",
            color: "var(--muted)",
          }}
        >
          {event.direction}
        </span>
      )}
      {event.actor && (
        <span
          style={{ fontSize: 10, color: "var(--muted2)", maxWidth: 110 }}
          title={event.actor}
        >
          {event.actor.slice(0, 10)}…
        </span>
      )}
      <span
        style={{
          fontFamily: "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace",
          color: deltaColor,
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        {event.size_delta_usdc != null
          ? fmtDeltaUsdc(event.size_delta_usdc)
          : `${event.severity.toFixed(1)}×`}
      </span>
      <span
        style={{ fontSize: 10, color: "var(--muted2)" }}
        title={event.observed_at}
      >
        {new Date(event.observed_at).toLocaleTimeString(undefined, {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Account: Polymarket address + CLOB credentials
// ---------------------------------------------------------------------------

function AccountPanel() {
  const queryClient = useQueryClient();
  const polymarketAddrQ = useQuery({
    queryKey: ["polymarket-address"],
    queryFn: fetchPolymarketAddress,
  });
  const clobQ = useQuery({
    queryKey: ["polymarket-clob-credentials"],
    queryFn: fetchPolymarketClobCredentials,
  });

  const [addressInput, setAddressInput] = useState("");
  const [clobForm, setClobForm] = useState({
    api_key: "",
    api_secret: "",
    passphrase: "",
    proxy_wallet: "",
  });

  useEffect(() => {
    if (polymarketAddrQ.data) setAddressInput(polymarketAddrQ.data.proxy_wallet ?? "");
  }, [polymarketAddrQ.data]);

  useEffect(() => {
    if (clobQ.data) {
      setClobForm((current) => ({
        api_key: "",
        api_secret: "",
        passphrase: "",
        proxy_wallet: current.proxy_wallet.trim() || clobQ.data!.proxy_wallet || "",
      }));
    }
  }, [clobQ.data]);

  const saveAddress = useMutation({
    mutationFn: (proxyWallet: string | null) => updatePolymarketAddress(proxyWallet),
    onSuccess: async (next) => {
      setAddressInput(next.proxy_wallet ?? "");
      await queryClient.invalidateQueries({ queryKey: ["polymarket-address"] });
    },
  });

  const saveClob = useMutation({
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
      await queryClient.invalidateQueries({ queryKey: ["provider-credentials"] });
    },
  });

  const summary = polymarketAddrQ.data?.summary;

  return (
    <>
      <Card
        title="Polymarket address"
        meta={
          polymarketAddrQ.data?.verified_at
            ? `verified ${new Date(polymarketAddrQ.data.verified_at).toLocaleString()}`
            : "not linked"
        }
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 12 }}>
          <FormRow label="Proxy wallet">
            <input
              value={addressInput}
              onChange={(e) => setAddressInput(e.target.value)}
              placeholder="0x..."
              style={inputStyle}
            />
          </FormRow>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
            <PrimaryButton
              disabled={saveAddress.isPending}
              onClick={() => saveAddress.mutate(addressInput.trim() || null)}
            >
              {saveAddress.isPending ? "Saving…" : "Save address"}
            </PrimaryButton>
            <SecondaryButton
              onClick={() => saveAddress.mutate(null)}
              disabled={saveAddress.isPending}
              tone="danger"
            >
              Clear
            </SecondaryButton>
          </div>
        </div>
        <HintText>
          Read-only public account sync via positions, trades, and earnings. No trading keys are used in this flow.
        </HintText>
        {summary && (
          <div
            style={{
              display: "grid",
              gap: 10,
              gridTemplateColumns: "repeat(4, 1fr)",
              marginTop: 14,
            }}
          >
            <StatTile label="Open positions" value={String(summary.open_positions)} />
            <StatTile label="Redeemable" value={String(summary.redeemable_positions)} />
            <StatTile label="Current value" value={fmtUsd(summary.total_position_value_usdc)} />
            <StatTile
              label="Total earnings"
              value={fmtUsd(summary.total_earnings_usdc)}
              tone={summary.total_earnings_usdc >= 0 ? "bull" : "bear"}
            />
          </div>
        )}
        {summary?.recent_trades?.length ? (
          <div style={{ marginTop: 14 }}>
            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                textTransform: "uppercase",
                color: "var(--muted)",
                letterSpacing: ".08em",
                marginBottom: 6,
              }}
            >
              Recent trades
            </div>
            <SimpleTable
              head={["Market", "Outcome", "Side", "Price", "Size"]}
              rows={summary.recent_trades.slice(0, 5).map((t, i) => [
                t.condition_id ? (
                  <Link
                    href={`/markets/${encodeURIComponent(t.condition_id)}` as Route}
                    style={{ color: "var(--text)", textDecoration: "none" }}
                    key={`m-${i}`}
                  >
                    {t.condition_id.slice(0, 10)}…{t.condition_id.slice(-4)}
                  </Link>
                ) : (
                  "-"
                ),
                t.outcome ?? "-",
                t.side ?? "-",
                t.price != null ? `${(t.price * 100).toFixed(1)}c` : "-",
                t.size != null ? fmtUsd(t.size) : "-",
              ])}
              align={["left", "right", "right", "right", "right"]}
            />
          </div>
        ) : null}
        {saveAddress.error && <ErrorText>{(saveAddress.error as Error).message}</ErrorText>}
      </Card>

      <Card
        title="Polymarket CLOB credentials"
        meta={
          clobQ.data?.configured
            ? clobQ.data.rotated_at
              ? `rotated ${new Date(clobQ.data.rotated_at).toLocaleString()}`
              : "configured"
            : "not configured"
        }
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <FormRow label="API key">
            <input
              value={clobForm.api_key}
              onChange={(e) => setClobForm({ ...clobForm, api_key: e.target.value })}
              placeholder="pk_live_..."
              style={inputStyle}
            />
          </FormRow>
          <FormRow label="API secret">
            <input
              type="password"
              value={clobForm.api_secret}
              onChange={(e) => setClobForm({ ...clobForm, api_secret: e.target.value })}
              placeholder="secret"
              style={inputStyle}
            />
          </FormRow>
          <FormRow label="Passphrase">
            <input
              type="password"
              value={clobForm.passphrase}
              onChange={(e) => setClobForm({ ...clobForm, passphrase: e.target.value })}
              placeholder="passphrase"
              style={inputStyle}
            />
          </FormRow>
          <FormRow label="Proxy wallet (optional)">
            <input
              value={clobForm.proxy_wallet}
              onChange={(e) => setClobForm({ ...clobForm, proxy_wallet: e.target.value })}
              placeholder="0x..."
              style={inputStyle}
            />
          </FormRow>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          <PrimaryButton
            disabled={saveClob.isPending}
            onClick={() =>
              saveClob.mutate({
                api_key: clobForm.api_key.trim() || null,
                api_secret: clobForm.api_secret.trim() || null,
                passphrase: clobForm.passphrase.trim() || null,
                proxy_wallet: clobForm.proxy_wallet.trim() || null,
              })
            }
          >
            {saveClob.isPending ? "Saving…" : "Save credentials"}
          </PrimaryButton>
          <SecondaryButton
            disabled={saveClob.isPending}
            onClick={() =>
              saveClob.mutate({
                api_key: null,
                api_secret: null,
                passphrase: null,
                proxy_wallet: null,
              })
            }
            tone="danger"
          >
            Clear
          </SecondaryButton>
          {clobQ.data?.configured && <Badge tone="accent">encrypted at rest</Badge>}
        </div>
        <HintText>
          Stored only for the optional user WebSocket path. Secrets are encrypted at rest, never returned by the API,
          and never used for trading endpoints in v1.
        </HintText>
        {clobQ.data?.configured && <HintText>{clobCredentialStatusText(clobQ.data)}</HintText>}
        {saveClob.error && <ErrorText>{(saveClob.error as Error).message}</ErrorText>}
      </Card>
    </>
  );
}

function SimpleTable({
  head,
  rows,
  align = [],
}: {
  head: string[];
  rows: Array<Array<React.ReactNode>>;
  align?: Array<"left" | "right">;
}) {
  return (
    <div
      style={{
        overflowX: "auto",
        border: "1px solid var(--border)",
        borderRadius: 8,
      }}
    >
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {head.map((h, i) => (
              <th
                key={h}
                style={{
                  padding: "8px 12px",
                  textAlign: align[i] ?? "left",
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: ".07em",
                  textTransform: "uppercase",
                  color: "var(--muted)",
                  whiteSpace: "nowrap",
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} style={{ borderTop: "1px solid var(--border)" }}>
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  style={{
                    padding: "8px 12px",
                    textAlign: align[ci] ?? "left",
                    fontFamily:
                      align[ci] === "right"
                        ? "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace"
                        : "inherit",
                    color: "var(--text)",
                  }}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

function ProvidersPanel() {
  const queryClient = useQueryClient();
  const providersQ = useQuery({
    queryKey: ["provider-credentials"],
    queryFn: fetchProviderCredentials,
  });
  const [forms, setForms] = useState<Record<string, Record<string, string>>>({});

  useEffect(() => {
    if (providersQ.data) {
      setForms((current) => {
        const next = { ...current };
        for (const provider of providersQ.data.providers) {
          next[provider.provider] = next[provider.provider] ?? emptyCredentialValues(provider);
        }
        return next;
      });
    }
  }, [providersQ.data]);

  const saveProvider = useMutation({
    mutationFn: updateProviderCredentials,
    onSuccess: async (next) => {
      setForms((current) => ({
        ...current,
        [next.provider]: emptyCredentialValues(next),
      }));
      if (next.provider === "polymarket_clob") {
        await queryClient.invalidateQueries({ queryKey: ["polymarket-clob-credentials"] });
      }
      await queryClient.invalidateQueries({ queryKey: ["provider-credentials"] });
    },
  });

  const providers = (providersQ.data?.providers ?? []).filter((p) => p.provider !== "polymarket_clob");
  const configuredCount = providers.filter((p) => p.configured).length;

  return (
    <Card title="External API credentials" meta={`${configuredCount} configured`}>
      <HintText>
        Secrets are encrypted at rest and returned only as status flags. Runtime workers may need a restart before
        newly saved credentials are picked up.
      </HintText>
      <div
        style={{
          marginTop: 14,
          display: "grid",
          gap: 12,
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
        }}
      >
        {providers.map((provider) => {
          const formValues = forms[provider.provider] ?? emptyCredentialValues(provider);
          return (
            <div
              key={provider.provider}
              style={{
                background: "var(--surface2)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: 14,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{provider.label}</div>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.4 }}>
                    {provider.description}
                  </div>
                </div>
                <Badge tone={provider.configured ? "accent" : "neutral"}>
                  {provider.configured ? "configured" : "not set"}
                </Badge>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
                {provider.fields.map((field) => (
                  <FormRow key={field.name} label={field.label} required={field.required}>
                    <input
                      type={field.secret ? "password" : "text"}
                      value={formValues[field.name] ?? ""}
                      onChange={(e) =>
                        setForms((current) => ({
                          ...current,
                          [provider.provider]: {
                            ...(current[provider.provider] ?? emptyCredentialValues(provider)),
                            [field.name]: e.target.value,
                          },
                        }))
                      }
                      placeholder={
                        provider.configured_fields.includes(field.name)
                          ? "Stored — enter a new value to rotate"
                          : field.placeholder
                      }
                      style={inputStyle}
                    />
                  </FormRow>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <PrimaryButton
                  disabled={saveProvider.isPending}
                  onClick={() =>
                    saveProvider.mutate({
                      provider: provider.provider,
                      values: Object.fromEntries(
                        provider.fields.map((field) => [
                          field.name,
                          (formValues[field.name] ?? "").trim() || null,
                        ]),
                      ),
                    })
                  }
                >
                  {saveProvider.isPending ? "Saving…" : "Save"}
                </PrimaryButton>
                <SecondaryButton
                  disabled={saveProvider.isPending}
                  onClick={() =>
                    saveProvider.mutate({
                      provider: provider.provider,
                      values: Object.fromEntries(provider.fields.map((field) => [field.name, null])),
                    })
                  }
                  tone="danger"
                >
                  Clear
                </SecondaryButton>
                {provider.rotated_at && (
                  <span style={{ fontSize: 10, color: "var(--muted2)" }}>
                    rotated {new Date(provider.rotated_at).toLocaleString()}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {saveProvider.error && <ErrorText>{(saveProvider.error as Error).message}</ErrorText>}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Journal
// ---------------------------------------------------------------------------

function JournalPanel() {
  const journalSummaryQ = useQuery({
    queryKey: ["journal-summary"],
    queryFn: fetchJournalSummary,
    refetchInterval: 30_000,
  });
  const journalCallsQ = useQuery({
    queryKey: ["journal-calls"],
    queryFn: fetchJournalCalls,
    refetchInterval: 30_000,
  });

  const summary = journalSummaryQ.data;
  if (!summary) {
    return (
      <Card title="Journal">
        <div style={{ color: "var(--muted)", fontSize: 12 }}>Loading journal…</div>
      </Card>
    );
  }

  return (
    <>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(5, 1fr)",
          gap: 12,
        }}
      >
        <StatTile label="Calls" value={String(summary.total_calls)} />
        <StatTile label="Resolved" value={String(summary.resolved_calls)} />
        <StatTile
          label="Total PnL"
          value={fmtUsd(summary.total_pnl_usdc)}
          tone={summary.total_pnl_usdc >= 0 ? "bull" : "bear"}
        />
        <StatTile
          label="Avg Brier"
          value={summary.avg_brier != null ? summary.avg_brier.toFixed(3) : "-"}
        />
        <StatTile label="Unresolved" value={String(summary.unresolved_calls)} />
      </div>

      {summary.resolution_sync && (
        <Card
          title="Resolution sync"
          meta={
            summary.resolution_sync.verified_at
              ? `linked ${new Date(summary.resolution_sync.verified_at).toLocaleString()}`
              : "linked"
          }
        >
          <HintText>
            Wallet {summary.resolution_sync.proxy_wallet.slice(0, 8)}…
            {summary.resolution_sync.proxy_wallet.slice(-6)} feeds redeemable-position and earnings checks into the
            journal loop.
          </HintText>
          <div
            style={{
              marginTop: 10,
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: 12,
            }}
          >
            <StatTile label="Redeemable" value={String(summary.resolution_sync.redeemable_positions)} />
            <StatTile
              label="Earnings"
              value={fmtUsd(summary.resolution_sync.total_earnings_usdc)}
              tone={summary.resolution_sync.total_earnings_usdc >= 0 ? "bull" : "bear"}
            />
            <StatTile label="Open positions" value={String(summary.resolution_sync.open_positions)} />
            <StatTile label="Current value" value={fmtUsd(summary.resolution_sync.total_position_value_usdc)} />
          </div>
        </Card>
      )}

      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
        <Card title="Hit rate by confidence bucket">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {summary.confidence_buckets.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--muted)" }}>No buckets yet.</div>
            ) : (
              summary.confidence_buckets.map((bucket) => (
                <div key={bucket.label}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: 11,
                      color: "var(--muted)",
                      marginBottom: 4,
                    }}
                  >
                    <span>{bucket.label}</span>
                    <span
                      style={{
                        fontFamily:
                          "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace",
                      }}
                    >
                      {bucket.count} calls · {(bucket.hit_rate * 100).toFixed(0)}% hit
                    </span>
                  </div>
                  <div style={{ height: 5, background: "var(--border)", borderRadius: 3 }}>
                    <div
                      style={{
                        height: "100%",
                        width: `${bucket.hit_rate * 100}%`,
                        background: "var(--green)",
                        borderRadius: 3,
                      }}
                    />
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>
        <Card title="Calibration plot">
          {summary.calibration_points.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>No data yet.</div>
          ) : (
            <CalibrationChart points={summary.calibration_points} />
          )}
        </Card>
      </div>

      <Card title="Edge realized vs predicted">
        {summary.edge_scatter.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--muted)" }}>No resolved calls yet.</div>
        ) : (
          <EdgeScatter points={summary.edge_scatter} />
        )}
      </Card>

      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
        <CallList title="Best calls" calls={summary.best_calls} />
        <CallList title="Worst calls" calls={summary.worst_calls} />
      </div>

      {(journalCallsQ.data ?? []).length > 0 && (
        <Card title="Recent calls">
          <SimpleTable
            head={["Market", "Call", "Entry", "Model", "Pred edge", "PnL"]}
            rows={(journalCallsQ.data ?? []).slice(0, 8).map((call: JournalCall) => [
              <Link
                href={`/markets/${encodeURIComponent(call.condition_id)}` as Route}
                style={{ color: "var(--text)", textDecoration: "none" }}
                key={call.id}
              >
                {call.condition_id.slice(0, 10)}…{call.condition_id.slice(-4)}
              </Link>,
              call.outcome,
              `${(call.entry_price * 100).toFixed(1)}c`,
              `${(call.model_prob_at_call * 100).toFixed(1)}%`,
              <span
                key="pe"
                style={{ color: call.predicted_edge_bps >= 0 ? "var(--green)" : "var(--red)" }}
              >
                {fmtBps(call.predicted_edge_bps)}
              </span>,
              <span
                key="pnl"
                style={{
                  color: (call.pnl_usdc ?? 0) >= 0 ? "var(--green)" : "var(--red)",
                }}
              >
                {call.pnl_usdc != null ? fmtUsd(call.pnl_usdc) : "-"}
              </span>,
            ])}
            align={["left", "right", "right", "right", "right", "right"]}
          />
        </Card>
      )}
    </>
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
    <Card title={title}>
      {calls.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>No resolved calls yet.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {calls.map((call) => (
            <div
              key={call.id}
              style={{
                background: "var(--surface2)",
                border: "1px solid var(--border)",
                borderRadius: 7,
                padding: "8px 12px",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 10,
                  fontSize: 12,
                }}
              >
                <Link
                  href={`/markets/${encodeURIComponent(call.condition_id)}` as Route}
                  style={{ color: "var(--text)", textDecoration: "none", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                  {call.condition_id.slice(0, 12)}…{call.condition_id.slice(-6)}
                </Link>
                <span
                  style={{
                    color: (call.pnl_usdc ?? 0) >= 0 ? "var(--green)" : "var(--red)",
                    fontFamily:
                      "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace",
                    fontWeight: 600,
                  }}
                >
                  {call.pnl_usdc != null ? fmtUsd(call.pnl_usdc) : "-"}
                </span>
              </div>
              <div style={{ fontSize: 10, color: "var(--muted)" }}>
                {call.outcome} · predicted{" "}
                <span
                  style={{
                    fontFamily:
                      "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace",
                  }}
                >
                  {fmtBps(call.predicted_edge_bps)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Diagnostics: drift + backtest
// ---------------------------------------------------------------------------

function DiagnosticsPanel() {
  const driftQ = useQuery({
    queryKey: ["drift-monitor"],
    queryFn: fetchDriftSnapshot,
    refetchInterval: 60_000,
  });
  const backtestQ = useQuery({
    queryKey: ["backtest-walk-forward"],
    queryFn: () => fetchBacktestSnapshot(90, 24, 500),
    refetchInterval: 60_000,
  });

  const [marketType, setMarketType] = useState("all");
  const [regime, setRegime] = useState("all");

  const filtered = (backtestQ.data?.rows ?? []).filter(
    (row) =>
      (marketType === "all" || row.market_type === marketType) &&
      (regime === "all" || (row.regime ?? "none") === regime),
  );
  const calibPoints = calibrationPointsFromReplayRows(filtered);
  const types = Array.from(new Set((backtestQ.data?.rows ?? []).map((r) => r.market_type))).sort();
  const regimes = Array.from(new Set((backtestQ.data?.rows ?? []).map((r) => r.regime ?? "none"))).sort();

  return (
    <>
      <Card
        title="Drift monitor"
        meta={
          driftQ.data?.observed_at
            ? `snapshot ${new Date(driftQ.data.observed_at).toLocaleString()}`
            : "no snapshots yet"
        }
      >
        {driftQ.data && driftQ.data.model_metrics.length > 0 ? (
          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 14 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {["7d", "30d", "90d"].map((windowLabel) => {
                const rows = driftQ.data!.model_metrics.filter(
                  (row) =>
                    row.window_label === windowLabel &&
                    row.market_type !== "overall" &&
                    row.ttr_bucket !== "overall",
                );
                if (rows.length === 0) return null;
                return (
                  <div
                    key={windowLabel}
                    style={{
                      background: "var(--surface2)",
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      padding: 12,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: "uppercase",
                        color: "var(--muted)",
                        letterSpacing: ".09em",
                        marginBottom: 8,
                      }}
                    >
                      <span>{windowLabel} cells</span>
                      <span>{rows.length} strata</span>
                    </div>
                    <SimpleTable
                      head={["Type", "Regime", "TTR", "Skill", "ECE", "Coverage", "N"]}
                      rows={rows.map((row: DriftMetricRow) => [
                        typeLabel(row.market_type),
                        row.regime ?? "all",
                        row.ttr_bucket,
                        <span
                          key={`s-${row.window_label}-${row.market_type}-${row.ttr_bucket}`}
                          style={{
                            color:
                              row.brier_skill == null
                                ? "var(--muted)"
                                : row.brier_skill >= 0
                                ? "var(--green)"
                                : "var(--red)",
                          }}
                        >
                          {fmtMetric(row.brier_skill)}
                        </span>,
                        fmtMetric(row.ece),
                        fmtPct(row.coverage),
                        String(row.sample_count),
                      ])}
                      align={["left", "left", "left", "right", "right", "right", "right"]}
                    />
                  </div>
                );
              })}
            </div>
            <div
              style={{
                background: "var(--surface2)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: 12,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  fontSize: 10,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  color: "var(--muted)",
                  letterSpacing: ".09em",
                  marginBottom: 8,
                }}
              >
                <span>Feature drift alerts</span>
                <span>
                  {driftQ.data.feature_metrics.filter((row) => row.is_alert).length} alerting
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {driftQ.data.feature_metrics.slice(0, 8).map((row: FeatureDriftMetricRow) => (
                  <div
                    key={row.feature_name}
                    style={{
                      background: "var(--surface)",
                      border: "1px solid var(--border)",
                      borderRadius: 6,
                      padding: "7px 10px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <span
                        style={{
                          fontSize: 11,
                          color: "var(--text)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {row.feature_name}
                      </span>
                      <Badge tone={row.is_alert ? "warn" : "neutral"}>PSI {row.psi.toFixed(3)}</Badge>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      KL {row.kl_divergence.toFixed(3)} · ref {row.reference_count} · live{" "}
                      {row.current_count}
                    </div>
                  </div>
                ))}
                {driftQ.data.feature_metrics.length === 0 && (
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>
                    No feature drift snapshots yet.
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            Drift snapshots will appear here once the monitor has run.
          </div>
        )}
      </Card>

      <Card
        title="Walk-forward backtest"
        meta={
          backtestQ.data
            ? `${backtestQ.data.total_samples} samples · ${backtestQ.data.lookback_days}d · ${backtestQ.data.horizon_hours}h horizon`
            : "loading"
        }
      >
        {backtestQ.data && backtestQ.data.rows.length > 0 ? (
          <>
            {backtestQ.data.tuning_comparison && (
              <div
                style={{
                  background: "var(--surface2)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  padding: 14,
                  marginBottom: 14,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    color: "var(--muted)",
                    letterSpacing: ".09em",
                    marginBottom: 12,
                  }}
                >
                  <span>Tuning counterfactual</span>
                  <span>
                    {backtestQ.data.tuning_comparison.profile_name} ·{" "}
                    {backtestQ.data.tuning_comparison.total_samples} aligned
                  </span>
                </div>
                <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(3,1fr)" }}>
                  <StatTile
                    label="Default Brier"
                    value={fmtMetric(backtestQ.data.tuning_comparison.default_brier)}
                  />
                  <StatTile
                    label="Tuned Brier"
                    value={fmtMetric(backtestQ.data.tuning_comparison.tuned_brier)}
                    tone={
                      backtestQ.data.tuning_comparison.tuned_brier <=
                      backtestQ.data.tuning_comparison.default_brier
                        ? "bull"
                        : "bear"
                    }
                  />
                  <StatTile
                    label="Δ"
                    value={fmtMetric(backtestQ.data.tuning_comparison.brier_delta)}
                    tone={backtestQ.data.tuning_comparison.brier_delta <= 0 ? "bull" : "bear"}
                  />
                </div>
                <div
                  style={{
                    display: "grid",
                    gap: 12,
                    gridTemplateColumns: "1fr 1fr",
                    marginTop: 14,
                  }}
                >
                  <Card title="Default calibration" style={{ background: "var(--surface)" }}>
                    <CalibrationChart
                      points={backtestQ.data.tuning_comparison.default_calibration_points}
                    />
                  </Card>
                  <Card title="Tuned calibration" style={{ background: "var(--surface)" }}>
                    <CalibrationChart
                      points={backtestQ.data.tuning_comparison.tuned_calibration_points}
                    />
                  </Card>
                </div>
              </div>
            )}
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr", marginBottom: 14 }}>
              <FormRow label="Market type">
                <select value={marketType} onChange={(e) => setMarketType(e.target.value)} style={selectStyle}>
                  <option value="all">All types</option>
                  {types.map((t) => (
                    <option key={t} value={t}>
                      {typeLabel(t)}
                    </option>
                  ))}
                </select>
              </FormRow>
              <FormRow label="Regime">
                <select value={regime} onChange={(e) => setRegime(e.target.value)} style={selectStyle}>
                  <option value="all">All regimes</option>
                  {regimes.map((r) => (
                    <option key={r} value={r}>
                      {r === "none" ? "Unknown" : r}
                    </option>
                  ))}
                </select>
              </FormRow>
            </div>
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1.1fr 1.4fr" }}>
              <Card title="Historical calibration" style={{ background: "var(--surface2)" }}>
                <CalibrationChart points={calibPoints} />
              </Card>
              <Card
                title="Resolved-market drilldown"
                meta={`${filtered.length} rows`}
                style={{ background: "var(--surface2)" }}
              >
                <SimpleTable
                  head={["Market", "Type", "Regime", "Pred", "Outcome", "Brier"]}
                  rows={filtered.slice(0, 12).map((row) => [
                    <div key={row.condition_id}>
                      <Link
                        href={`/markets/${encodeURIComponent(row.condition_id)}` as Route}
                        style={{ color: "var(--text)", textDecoration: "none" }}
                      >
                        {row.condition_id.slice(0, 10)}…{row.condition_id.slice(-4)}
                      </Link>
                      <div style={{ fontSize: 10, color: "var(--muted)" }}>
                        {new Date(row.resolved_at).toLocaleDateString()}
                      </div>
                    </div>,
                    typeLabel(row.market_type),
                    row.regime ?? "unknown",
                    fmtPct(row.predicted_prob),
                    row.outcome ? "YES" : "NO",
                    fmtMetric(row.brier_contribution),
                  ])}
                  align={["left", "left", "left", "right", "right", "right"]}
                />
              </Card>
            </div>
          </>
        ) : (
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            Backtest data not available yet.
          </div>
        )}
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------
// Beta invites + feedback
// ---------------------------------------------------------------------------

function FeedbackPanel() {
  const queryClient = useQueryClient();
  const betaInvitesQ = useQuery({
    queryKey: ["beta-invites"],
    queryFn: fetchBetaInvites,
  });

  const [inviteForm, setInviteForm] = useState({ email: "", display_name: "" });
  const [feedbackForm, setFeedbackForm] = useState<{
    kind: "bug" | "idea" | "model" | "data" | "other";
    message: string;
    contact_email: string;
  }>({ kind: "other", message: "", contact_email: "" });

  const saveInvite = useMutation({
    mutationFn: createBetaInvite,
    onSuccess: async () => {
      setInviteForm({ email: "", display_name: "" });
      await queryClient.invalidateQueries({ queryKey: ["beta-invites"] });
    },
  });

  const sendFeedback = useMutation({
    mutationFn: submitBetaFeedback,
    onSuccess: () => setFeedbackForm((c) => ({ ...c, message: "" })),
  });

  const summary = betaInvitesQ.data;

  return (
    <>
      <Card
        title="Closed beta invites"
        meta={summary ? `${summary.invited_count}/${summary.target_count} invited` : "loading"}
      >
        <div style={{ display: "grid", gap: 10, gridTemplateColumns: "1fr 1fr" }}>
          <FormRow label="Email">
            <input
              value={inviteForm.email}
              onChange={(e) => setInviteForm({ ...inviteForm, email: e.target.value })}
              placeholder="invite@example.com"
              style={inputStyle}
            />
          </FormRow>
          <FormRow label="Display name">
            <input
              value={inviteForm.display_name}
              onChange={(e) => setInviteForm({ ...inviteForm, display_name: e.target.value })}
              placeholder="optional"
              style={inputStyle}
            />
          </FormRow>
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginTop: 4,
            gap: 12,
          }}
        >
          <HintText>
            {summary
              ? `${summary.accepted_count} accepted · ${summary.remaining_slots} slots left`
              : "Tracking the initial invited-user cohort."}
          </HintText>
          <PrimaryButton
            disabled={saveInvite.isPending || !inviteForm.email.trim()}
            onClick={() =>
              saveInvite.mutate({
                email: inviteForm.email,
                display_name: inviteForm.display_name || null,
              })
            }
          >
            {saveInvite.isPending ? "Adding…" : "Add invite"}
          </PrimaryButton>
        </div>
        {summary?.invites.length ? (
          <div
            style={{
              marginTop: 14,
              display: "flex",
              flexDirection: "column",
              gap: 4,
              maxHeight: 160,
              overflowY: "auto",
            }}
          >
            {summary.invites.slice(0, 8).map((invite) => (
              <div
                key={invite.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: 11,
                  color: "var(--muted)",
                  padding: "6px 10px",
                  background: "var(--surface2)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {invite.email}
                </span>
                <Badge tone={invite.status === "accepted" ? "bull" : "neutral"}>{invite.status}</Badge>
              </div>
            ))}
          </div>
        ) : null}
        {saveInvite.error && <ErrorText>{(saveInvite.error as Error).message}</ErrorText>}
      </Card>

      <Card
        title="Beta feedback"
        meta={sendFeedback.isSuccess ? "received — thanks!" : "in-app capture"}
      >
        <div style={{ display: "grid", gap: 10, gridTemplateColumns: "180px 1fr 220px" }}>
          <FormRow label="Type">
            <select
              value={feedbackForm.kind}
              onChange={(e) =>
                setFeedbackForm({
                  ...feedbackForm,
                  kind: e.target.value as typeof feedbackForm.kind,
                })
              }
              style={selectStyle}
            >
              <option value="bug">Bug</option>
              <option value="idea">Idea</option>
              <option value="model">Model</option>
              <option value="data">Data</option>
              <option value="other">Other</option>
            </select>
          </FormRow>
          <FormRow label="Message">
            <textarea
              value={feedbackForm.message}
              onChange={(e) => setFeedbackForm({ ...feedbackForm, message: e.target.value })}
              rows={3}
              placeholder="What happened, what felt unclear, or what should change?"
              style={textareaStyle}
            />
          </FormRow>
          <FormRow label="Contact (optional)">
            <input
              value={feedbackForm.contact_email}
              onChange={(e) => setFeedbackForm({ ...feedbackForm, contact_email: e.target.value })}
              placeholder="optional email"
              style={inputStyle}
            />
          </FormRow>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginTop: 4 }}>
          <HintText>Stored with the current page URL so beta issues can be traced quickly.</HintText>
          <PrimaryButton
            disabled={sendFeedback.isPending || !feedbackForm.message.trim()}
            onClick={() =>
              sendFeedback.mutate({
                kind: feedbackForm.kind,
                message: feedbackForm.message,
                page_url: typeof window === "undefined" ? null : window.location.href,
                contact_email: feedbackForm.contact_email || null,
              })
            }
          >
            {sendFeedback.isPending ? "Sending…" : "Send feedback"}
          </PrimaryButton>
        </div>
        {sendFeedback.error && <ErrorText>{(sendFeedback.error as Error).message}</ErrorText>}
      </Card>
    </>
  );
}

