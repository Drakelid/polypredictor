"use client";

import Link from "next/link";
import type { Route } from "next";
import { useQuery } from "@tanstack/react-query";
import {
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  fetchMarketHistory,
  fetchMarketModel,
  fetchMarkets,
  fetchSignals,
  fetchSystemStatus,
  type FeatureAttribution,
  type MarketHistoryPoint,
  type MarketModel,
  type MarketRow,
  type SignalEvent,
  type SystemStatus,
} from "@/lib/api";
import Settings from "@/components/Settings";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type CategoryKey = "BTC" | "ETH" | "SOL" | "MACRO" | "COIN" | "MKT" | "OTHER";

const CATEGORY_TABS: Array<{ id: "All" | CategoryKey; label: string }> = [
  { id: "All", label: "All" },
  { id: "BTC", label: "BTC" },
  { id: "ETH", label: "ETH" },
  { id: "SOL", label: "SOL" },
  { id: "MACRO", label: "Macro" },
  { id: "COIN", label: "Equities" },
];

function classifyMarket(m: MarketRow): CategoryKey {
  const haystack = `${m.category ?? ""} ${m.question} ${(m.tags ?? []).join(" ")}`.toLowerCase();
  if (/\bbtc|bitcoin\b/.test(haystack)) return "BTC";
  if (/\beth|ether/.test(haystack)) return "ETH";
  if (/\bsol(ana)?\b/.test(haystack)) return "SOL";
  if (/fed|fomc|cpi|pce|rate|macro|gdp|inflation|jobs/.test(haystack)) return "MACRO";
  if (/coin(base)?|stock|equit|nasdaq|s&p/.test(haystack)) return "COIN";
  if (/market\s*cap|mcap|total\s*cap/.test(haystack)) return "MKT";
  return "OTHER";
}

function categoryColor(cat: CategoryKey): string {
  switch (cat) {
    case "BTC":
      return "var(--amber)";
    case "ETH":
      return "var(--violet)";
    case "SOL":
      return "oklch(0.72 0.20 210)";
    case "MACRO":
      return "oklch(0.68 0.16 228)";
    case "COIN":
      return "oklch(0.72 0.18 320)";
    case "MKT":
      return "var(--green)";
    default:
      return "var(--muted)";
  }
}

function categoryShort(cat: CategoryKey): string {
  return cat === "OTHER" ? "MKT" : cat;
}

type EdgeTier = "strong" | "moderate" | "slight" | "none";

function edgeTier(pp: number): EdgeTier {
  const a = Math.abs(pp);
  if (a >= 8) return "strong";
  if (a >= 5) return "moderate";
  if (a >= 2) return "slight";
  return "none";
}

function edgeColor(pp: number): string {
  const tier = edgeTier(pp);
  if (tier === "none") return "var(--muted)";
  if (tier === "slight") return "var(--amber)";
  return pp > 0 ? "var(--green)" : "var(--red)";
}

function edgeLabel(pp: number): string {
  const tier = edgeTier(pp);
  if (tier === "none") return "No edge";
  const dir = pp > 0 ? "YES" : "NO";
  if (tier === "slight") return `Slight ${dir}`;
  if (tier === "moderate") return `${dir} edge`;
  return `Strong ${dir} edge`;
}

function ppFromBps(bps: number | null | undefined): number | null {
  if (bps == null) return null;
  return Math.round((bps / 100) * 10) / 10;
}

function pctOf(p: number | null | undefined): number | null {
  if (p == null) return null;
  return Math.round(p * 1000) / 10;
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function ttrToDays(secs: number | null | undefined): number | null {
  if (secs == null || secs < 0) return null;
  return Math.max(0, Math.round(secs / 86_400));
}

function fmtDaysAgo(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function fmtLiveAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  return `${Math.floor(s / 60)}m ago`;
}

// ---------------------------------------------------------------------------
// Iconography (matches design)
// ---------------------------------------------------------------------------

const Logo = () => (
  <svg width="26" height="26" viewBox="0 0 28 28" fill="none">
    <polygon points="14,2 26,9 26,19 14,26 2,19 2,9" stroke="var(--green)" strokeWidth="1.5" fill="none" />
    <polygon
      points="14,7 21,11 21,17 14,21 7,17 7,11"
      fill="oklch(0.75 0.20 155 / 0.15)"
      stroke="var(--green)"
      strokeWidth="1"
    />
    <circle cx="14" cy="14" r="2.5" fill="var(--green)" />
    <line x1="14" y1="14" x2="20" y2="9" stroke="var(--green)" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const navIconStyle = {
  width: 15,
  height: 15,
} as const;

const IconMarkets = () => (
  <svg {...navIconStyle} viewBox="0 0 15 15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <rect x="1" y="1" width="5" height="5" rx="1" />
    <rect x="9" y="1" width="5" height="5" rx="1" />
    <rect x="1" y="9" width="5" height="5" rx="1" />
    <rect x="9" y="9" width="5" height="5" rx="1" />
  </svg>
);

const IconWatch = () => (
  <svg
    {...navIconStyle}
    viewBox="0 0 15 15"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <polygon points="7.5,1.5 9.5,6 14,6.5 10.5,9.8 11.5,14 7.5,11.8 3.5,14 4.5,9.8 1,6.5 5.5,6" />
  </svg>
);

const IconCorr = () => (
  <svg {...navIconStyle} viewBox="0 0 15 15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <circle cx="3.5" cy="7.5" r="2" />
    <circle cx="11.5" cy="7.5" r="2" />
    <circle cx="7.5" cy="2.5" r="2" />
    <circle cx="7.5" cy="12.5" r="2" />
    <line x1="5.5" y1="7.5" x2="9.5" y2="7.5" />
    <line x1="7.5" y1="4.5" x2="7.5" y2="10.5" />
  </svg>
);

const IconBacktest = () => (
  <svg {...navIconStyle} viewBox="0 0 15 15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <polyline points="1,11 4,7 7,9 10,4 14,6" />
    <line x1="1" y1="13.5" x2="14" y2="13.5" />
  </svg>
);

const IconAlerts = () => (
  <svg {...navIconStyle} viewBox="0 0 15 15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <path d="M7.5 1.5 C5 1.5 3 3.5 3 6.5 L3 10 L1.5 11.5 L13.5 11.5 L12 10 L12 6.5 C12 3.5 10 1.5 7.5 1.5Z" />
    <line x1="6" y1="11.5" x2="6" y2="13" />
    <line x1="9" y1="11.5" x2="9" y2="13" />
  </svg>
);

const IconSettings = () => (
  <svg {...navIconStyle} viewBox="0 0 15 15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <circle cx="7.5" cy="7.5" r="2" />
    <path d="M7.5 1v1.5M7.5 12.5V14M1 7.5h1.5M12.5 7.5H14M2.8 2.8l1.1 1.1M11.1 11.1l1.1 1.1M2.8 12.2l1.1-1.1M11.1 3.9l1.1-1.1" />
  </svg>
);

// ---------------------------------------------------------------------------
// Sidebar / bottom nav
// ---------------------------------------------------------------------------

type ScreenId = "markets" | "watchlist" | "correlation" | "backtest" | "alerts" | "settings";

type NavItemDef = { id: ScreenId; icon: ReactNode; label: string; badge?: number };

function Sidebar({
  screen,
  onChange,
  unread,
}: {
  screen: ScreenId;
  onChange: (id: ScreenId) => void;
  unread: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const top: NavItemDef[] = [
    { id: "markets", icon: <IconMarkets />, label: "Markets" },
    { id: "watchlist", icon: <IconWatch />, label: "Watchlist" },
    { id: "correlation", icon: <IconCorr />, label: "Signals" },
    { id: "backtest", icon: <IconBacktest />, label: "Backtest" },
  ];
  const bottom: NavItemDef[] = [
    { id: "alerts", icon: <IconAlerts />, label: "Alerts", badge: unread },
    { id: "settings", icon: <IconSettings />, label: "Settings" },
  ];

  const renderItem = (item: NavItemDef) => {
    const active = screen === item.id;
    return (
      <div
        key={item.id}
        onClick={() => onChange(item.id)}
        title={!expanded ? item.label : undefined}
        style={{
          display: "flex",
          alignItems: "center",
          gap: expanded ? 10 : 0,
          padding: 9,
          borderRadius: 8,
          background: active ? "var(--surface3)" : "transparent",
          color: active ? "var(--green)" : "var(--muted)",
          cursor: "pointer",
          transition: "color .15s, background .15s",
          position: "relative",
          width: "100%",
          flexDirection: expanded ? "row" : "column",
          justifyContent: expanded ? "flex-start" : "center",
          overflow: "hidden",
        }}
      >
        <span style={{ flexShrink: 0, display: "flex", alignItems: "center" }}>{item.icon}</span>
        {!expanded && active && (
          <span
            style={{
              width: 14,
              height: 3,
              borderRadius: 2,
              background: "var(--green)",
              marginTop: 3,
              boxShadow: "0 0 6px var(--green)",
            }}
          />
        )}
        {expanded && (
          <span style={{ fontSize: 12, fontWeight: active ? 600 : 400, whiteSpace: "nowrap" }}>{item.label}</span>
        )}
        {expanded && (item.badge ?? 0) > 0 && (
          <span
            style={{
              marginLeft: "auto",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--red)",
              border: "1.5px solid var(--bg)",
              animation: "pp-pulse 2s infinite",
              flexShrink: 0,
            }}
          />
        )}
        {!expanded && (item.badge ?? 0) > 0 && (
          <span
            style={{
              position: "absolute",
              top: 6,
              right: 6,
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--red)",
              border: "1.5px solid var(--bg)",
            }}
          />
        )}
      </div>
    );
  };

  return (
    <div
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
      style={{
        width: expanded ? 160 : 52,
        background: "var(--surface)",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        padding: "12px 6px",
        gap: 2,
        flexShrink: 0,
        transition: "width .2s ease",
        overflow: "hidden",
        zIndex: 20,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "4px 3px",
          marginBottom: 14,
          overflow: "hidden",
          flexShrink: 0,
        }}
      >
        <div style={{ flexShrink: 0 }}>
          <Logo />
        </div>
        {expanded && (
          <span
            style={{
              fontSize: 13,
              fontWeight: 700,
              letterSpacing: ".01em",
              color: "var(--text)",
              whiteSpace: "nowrap",
            }}
          >
            PolyPredictor
          </span>
        )}
      </div>
      {top.map(renderItem)}
      <div style={{ flex: 1 }} />
      {bottom.map(renderItem)}
    </div>
  );
}

function BottomNav({
  screen,
  onChange,
  unread,
}: {
  screen: ScreenId;
  onChange: (id: ScreenId) => void;
  unread: number;
}) {
  const items: NavItemDef[] = [
    { id: "markets", icon: <IconMarkets />, label: "Markets" },
    { id: "watchlist", icon: <IconWatch />, label: "Watch" },
    { id: "correlation", icon: <IconCorr />, label: "Signals" },
    { id: "alerts", icon: <IconAlerts />, label: "Alerts", badge: unread },
    { id: "settings", icon: <IconSettings />, label: "More" },
  ];
  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        background: "var(--surface)",
        borderTop: "1px solid var(--border)",
        display: "flex",
        zIndex: 50,
        height: 56,
      }}
    >
      {items.map((item) => {
        const active = screen === item.id;
        return (
          <div
            key={item.id}
            onClick={() => onChange(item.id)}
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 2,
              cursor: "pointer",
              color: active ? "var(--green)" : "var(--muted)",
              position: "relative",
            }}
          >
            <span style={{ display: "flex", alignItems: "center" }}>{item.icon}</span>
            <span style={{ fontSize: 9, fontWeight: active ? 600 : 400 }}>{item.label}</span>
            {(item.badge ?? 0) > 0 && (
              <span
                style={{
                  position: "absolute",
                  top: 10,
                  left: "50%",
                  marginLeft: 4,
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: "var(--red)",
                  border: "1.5px solid var(--bg)",
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter / search bar + summary stats
// ---------------------------------------------------------------------------

function FilterTabs({
  active,
  onChange,
  liveStr,
  sort,
  onSort,
  compact,
  onCompact,
  count,
  strongOnly,
  onStrongOnly,
  search,
  onSearch,
  cryptoOnly,
  onCryptoOnly,
}: {
  active: "All" | CategoryKey;
  onChange: (id: "All" | CategoryKey) => void;
  liveStr: string;
  sort: string;
  onSort: (s: string) => void;
  compact: boolean;
  onCompact: () => void;
  count: number;
  strongOnly: boolean;
  onStrongOnly: (v: boolean) => void;
  search: string;
  onSearch: (s: string) => void;
  cryptoOnly: boolean;
  onCryptoOnly: (v: boolean) => void;
}) {
  return (
    <div style={{ position: "sticky", top: 0, zIndex: 10, background: "var(--bg)", paddingBottom: 10, paddingTop: 2 }}>
      <div
        style={{
          background: "var(--surface2)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: "10px 14px",
          display: "flex",
          flexDirection: "column",
          gap: 9,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 7,
            padding: "6px 12px",
          }}
        >
          <span style={{ color: "var(--muted)", fontSize: 13, lineHeight: 1 }}>⌕</span>
          <input
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search markets…"
            style={{
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--text)",
              fontSize: 12,
              flex: 1,
            }}
          />
          {search ? (
            <span
              onClick={() => onSearch("")}
              style={{ cursor: "pointer", color: "var(--muted)", fontSize: 14, lineHeight: 1 }}
            >
              ×
            </span>
          ) : null}
        </div>
        <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
          {CATEGORY_TABS.map((t) => {
            const isActive = active === t.id && !strongOnly;
            return (
              <button
                key={t.id}
                onClick={() => {
                  onChange(t.id);
                  onStrongOnly(false);
                }}
                style={{
                  padding: "4px 11px",
                  borderRadius: 6,
                  border: "1px solid",
                  borderColor: isActive ? "var(--green)" : "var(--border)",
                  background: isActive ? "var(--green-dim)" : "var(--surface)",
                  color: isActive ? "var(--green)" : "var(--muted)",
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
          {strongOnly && (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "4px 10px",
                borderRadius: 6,
                border: "1px solid var(--green)",
                background: "var(--green-dim)",
                color: "var(--green)",
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              ⚡ Strong
              <button
                onClick={() => onStrongOnly(false)}
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--green)",
                  cursor: "pointer",
                  fontSize: 14,
                  padding: 0,
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </span>
          )}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 11, color: "var(--muted2)" }} className="font-mono">
            {count} mkt{count !== 1 ? "s" : ""}
          </span>
          <div
            style={{
              fontSize: 11,
              color: "var(--muted)",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
            className="font-mono"
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--green)",
                boxShadow: "0 0 5px var(--green)",
                display: "inline-block",
              }}
            />
            {liveStr}
          </div>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            paddingTop: 8,
            borderTop: "1px solid var(--border)",
          }}
        >
          <select
            value={sort}
            onChange={(e) => onSort(e.target.value)}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              color: "var(--text)",
              borderRadius: 6,
              padding: "4px 10px",
              fontSize: 11,
              cursor: "pointer",
              outline: "none",
            }}
          >
            <option value="edge-desc">Edge ↓</option>
            <option value="edge-asc">Edge ↑</option>
            <option value="days">Days left</option>
            <option value="volume">Volume</option>
          </select>
          <button
            onClick={onCompact}
            title={compact ? "Expanded view" : "Compact view"}
            style={{
              padding: "4px 10px",
              borderRadius: 6,
              border: `1px solid ${compact ? "var(--green)" : "var(--border)"}`,
              background: compact ? "var(--green-dim)" : "var(--surface)",
              color: compact ? "var(--green)" : "var(--muted)",
              cursor: "pointer",
              fontSize: 12,
              lineHeight: 1,
              transition: "all .15s",
              fontWeight: 500,
            }}
          >
            {compact ? "▤" : "▦"}
          </button>
          <button
            onClick={() => onCryptoOnly(!cryptoOnly)}
            title="Toggle crypto-only filter"
            style={{
              padding: "4px 10px",
              borderRadius: 6,
              border: `1px solid ${cryptoOnly ? "var(--green)" : "var(--border)"}`,
              background: cryptoOnly ? "var(--green-dim)" : "var(--surface)",
              color: cryptoOnly ? "var(--green)" : "var(--muted)",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 500,
            }}
          >
            crypto-only
          </button>
          <div style={{ flex: 1 }} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Market card
// ---------------------------------------------------------------------------

type EnrichedMarket = MarketRow & {
  cat: CategoryKey;
  estimatePct: number | null;
  marketPct: number | null;
  edgePp: number | null;
  daysLeft: number | null;
};

function enrich(rows: MarketRow[]): EnrichedMarket[] {
  return rows.map((m) => ({
    ...m,
    cat: classifyMarket(m),
    estimatePct: pctOf(m.model_prob ?? null),
    marketPct: pctOf(m.mid ?? null),
    edgePp: ppFromBps(m.edge_bps ?? null),
    daysLeft: ttrToDays(m.time_to_resolution_s ?? null),
  }));
}

function MarketCard({
  market,
  onClick,
  active,
  compact,
  search,
  tracked,
  onTrack,
}: {
  market: EnrichedMarket;
  onClick: () => void;
  active: boolean;
  compact: boolean;
  search: string;
  tracked: boolean;
  onTrack: (id: string) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const edge = market.edgePp ?? 0;
  const tier = edgeTier(edge);
  const col = edgeColor(edge);
  const isStrong = tier === "strong";
  const catCol = categoryColor(market.cat);
  const days = market.daysLeft;
  const urgent = days != null && days <= 7;
  const daysCol =
    days == null ? "var(--muted)" : days <= 4 ? "var(--red)" : days <= 7 ? "var(--amber)" : "var(--muted)";

  const highlight = (text: string): ReactNode => {
    if (!search || search.length < 2) return text;
    const idx = text.toLowerCase().indexOf(search.toLowerCase());
    if (idx < 0) return text;
    return (
      <>
        {text.slice(0, idx)}
        <mark style={{ background: "var(--amber)", color: "var(--bg)", borderRadius: 2 }}>
          {text.slice(idx, idx + search.length)}
        </mark>
        {text.slice(idx + search.length)}
      </>
    );
  };

  const m3Chips = useMemo(() => {
    const chips: Array<{ key: string; label: string; color: string; tip: string }> = [];
    if (market.smart_money_consensus != null && market.smart_money_sample_wallets) {
      const consensus = market.smart_money_consensus;
      const dom = market.smart_money_dominant ?? "";
      const colSm = consensus > 0.2 ? "var(--green)" : consensus < -0.2 ? "var(--red)" : "var(--muted)";
      chips.push({
        key: "sm",
        label: `SM ${consensus > 0 ? "+" : ""}${(consensus * 100).toFixed(0)}`,
        color: colSm,
        tip: `Smart money: ${(market.smart_money_sample_wallets ?? 0)} wallets, dominant ${dom || "n/a"}`,
      });
    }
    if (market.concentration_score != null) {
      const high = (market.concentration_score ?? 0) >= 0.7;
      chips.push({
        key: "conc",
        label: high ? "Whale" : "Holders",
        color: high ? "var(--amber)" : "var(--muted)",
        tip: `Concentration score ${market.concentration_score?.toFixed(2)}`,
      });
    }
    if (market.resolution_risk_flagged) {
      chips.push({
        key: "rr",
        label: "Res-risk",
        color: "var(--amber)",
        tip: `Resolution risk: ${market.resolution_risk_level ?? "elevated"}`,
      });
    }
    if (market.adversarial_flow_flagged) {
      chips.push({
        key: "adv",
        label: "Adv flow",
        color: "var(--red)",
        tip: "Adversarial flow flagged",
      });
    }
    if (market.thin_book) {
      chips.push({ key: "thin", label: "Thin book", color: "var(--amber)", tip: "Thin order book" });
    }
    return chips;
  }, [market]);

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: active
          ? "var(--surface2)"
          : isStrong
          ? edge > 0
            ? "oklch(0.75 0.20 155 / 0.07)"
            : "oklch(0.68 0.20 25 / 0.07)"
          : "var(--surface)",
        border: `1px solid ${active ? "var(--border2)" : isStrong ? `${col}4d` : "var(--border)"}`,
        borderRadius: 10,
        padding: compact ? "11px 16px" : "15px 18px",
        cursor: "pointer",
        transition: "border-color .15s, background .15s",
        position: "relative",
        overflow: "hidden",
        boxShadow:
          isStrong && !active
            ? `0 0 0 1px ${col}26, 0 4px 20px ${col}0f`
            : active
            ? "0 0 0 1px var(--border2)"
            : "none",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 4,
          background: col,
          borderRadius: "10px 0 0 10px",
          opacity: Math.abs(edge) >= 5 ? 1 : 0.3,
          boxShadow: isStrong ? `0 0 14px ${col}, 0 0 5px ${col}` : "",
        }}
      />
      <div
        style={{
          position: "absolute",
          right: 12,
          top: "50%",
          transform: `translateY(-50%) translateX(${hovered && !active ? 0 : 6}px)`,
          opacity: hovered && !active ? 0.45 : 0,
          transition: "opacity .15s, transform .15s",
          color: "var(--muted)",
          fontSize: 14,
          pointerEvents: "none",
        }}
      >
        →
      </div>
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "flex-start",
          marginBottom: compact ? 7 : 10,
          paddingRight: hovered && !active ? 16 : 0,
          transition: "padding .15s",
        }}
      >
        <span
          style={{
            background: `${catCol}22`,
            color: catCol,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: ".08em",
            padding: "2px 7px",
            borderRadius: 4,
            flexShrink: 0,
            marginTop: 2,
            border: `1px solid ${catCol}40`,
          }}
        >
          {categoryShort(market.cat)}
        </span>
        <span style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.45, flex: 1, textWrap: "pretty" } as CSSProperties}>
          {highlight(market.question)}
        </span>
        {days != null && (
          <span
            className="font-mono"
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: daysCol,
              flexShrink: 0,
              marginTop: 2,
              ...(urgent
                ? {
                    background: `${daysCol}18`,
                    padding: "2px 6px",
                    borderRadius: 4,
                    border: `1px solid ${daysCol}38`,
                  }
                : { opacity: 0.6 }),
            }}
          >
            {days}d
          </span>
        )}
        <span
          onClick={(e) => {
            e.stopPropagation();
            onTrack(market.condition_id);
          }}
          title={tracked ? "Stop tracking" : "Track market"}
          style={{
            fontSize: 13,
            color: tracked ? "var(--amber)" : "var(--muted2)",
            cursor: "pointer",
            flexShrink: 0,
            transition: "color .15s",
            lineHeight: 1,
            padding: "1px 2px",
            display: "inline-block",
            marginTop: 1,
          }}
        >
          {tracked ? "★" : "☆"}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div>
          <div
            style={{
              fontSize: 10,
              color: "var(--muted)",
              marginBottom: 2,
              letterSpacing: ".05em",
              whiteSpace: "nowrap",
            }}
          >
            PP Estimate
          </div>
          <div
            className="font-mono"
            style={{
              fontSize: 26,
              fontWeight: 700,
              color: "var(--green)",
              lineHeight: 1,
            }}
          >
            {market.estimatePct != null ? `${market.estimatePct.toFixed(0)}%` : "—"}
          </div>
        </div>
        <div style={{ width: 1, height: 32, background: "var(--border)" }} />
        <div>
          <div
            style={{
              fontSize: 10,
              color: "var(--muted)",
              marginBottom: 2,
              letterSpacing: ".05em",
              whiteSpace: "nowrap",
            }}
          >
            Market
          </div>
          <div
            className="font-mono"
            style={{
              fontSize: 15,
              fontWeight: 500,
              color: "var(--text)",
              opacity: 0.6,
              lineHeight: 1,
            }}
          >
            {market.marketPct != null ? `${market.marketPct.toFixed(0)}%` : "—"}
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div
            className="font-mono"
            style={{
              fontSize: 18,
              fontWeight: 800,
              color: col,
              background: `${col}1a`,
              padding: "6px 14px",
              borderRadius: 7,
              letterSpacing: ".02em",
              boxShadow: isStrong ? `0 0 16px ${col}44, inset 0 0 10px ${col}18` : "",
              border: `1px solid ${col}38`,
            }}
          >
            {market.edgePp != null ? `${edge > 0 ? "+" : ""}${edge.toFixed(1)}pp` : "—"}
          </div>
        </div>
      </div>
      {!compact && (
        <div style={{ display: "flex", gap: 4, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
          {m3Chips.length === 0 ? (
            <span style={{ fontSize: 10, color: "var(--muted2)" }}>No live signals</span>
          ) : (
            m3Chips.map((c) => (
              <div
                key={c.key}
                title={c.tip}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 3,
                  padding: "3px 7px",
                  borderRadius: 5,
                  background: `${c.color}14`,
                  border: `1px solid ${c.color}28`,
                  cursor: "default",
                }}
              >
                <span
                  style={{
                    width: 5,
                    height: 5,
                    borderRadius: "50%",
                    background: c.color,
                    flexShrink: 0,
                    display: "inline-block",
                  }}
                />
                <span className="font-mono" style={{ fontSize: 10, fontWeight: 600, color: c.color }}>
                  {c.label}
                </span>
              </div>
            ))
          )}
          <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
            Vol {fmtUsd(market.volume_usdc)} · Liq {fmtUsd(market.liquidity_usdc)}
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail panel: edge gauge, history chart, signals tabs
// ---------------------------------------------------------------------------

function EdgeGauge({ edge, compact = false }: { edge: number; compact?: boolean }) {
  const [animPct, setAnimPct] = useState(0);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    const target = Math.min(Math.abs(edge) / 15, 1);
    const start = performance.now();
    const dur = 700;
    const ease = (t: number) => (t < 0.5 ? 2 * t * t : (4 - 2 * t) * t - 1);
    const tick = (now: number) => {
      const t = Math.min((now - start) / dur, 1);
      setAnimPct(ease(t) * target);
      if (t < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    };
  }, [edge]);

  const isYes = edge > 0;
  const col =
    Math.abs(edge) >= 8
      ? isYes
        ? "var(--green)"
        : "var(--red)"
      : Math.abs(edge) >= 4
      ? "var(--amber)"
      : "var(--muted)";
  const W = compact ? 130 : 180;
  const H = compact ? 68 : 95;
  const cx = compact ? 65 : 90;
  const cy = compact ? 58 : 82;
  const r = compact ? 42 : 58;
  const PI = Math.PI;
  const arc = (a1: number, a2: number, rr: number) => {
    const x1 = cx + rr * Math.cos(a1);
    const y1 = cy + rr * Math.sin(a1);
    const x2 = cx + rr * Math.cos(a2);
    const y2 = cy + rr * Math.sin(a2);
    return `M${x1} ${y1} A${rr} ${rr} 0 ${a2 - a1 > PI ? 1 : 0} 1 ${x2} ${y2}`;
  };
  const na = PI + PI * (0.5 + (isYes ? animPct : -animPct) * 0.5);
  const nx = cx + (r - 14) * Math.cos(na);
  const ny = cy + (r - 14) * Math.sin(na);
  const fs = compact ? 16 : 20;
  const fss = compact ? 8 : 9;
  return (
    <div style={{ textAlign: "center", flexShrink: 0 }}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        <defs>
          <linearGradient id="ppGaugeGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="oklch(0.68 0.20 25)" stopOpacity=".3" />
            <stop offset="50%" stopColor="var(--muted2)" stopOpacity=".4" />
            <stop offset="100%" stopColor="var(--green)" stopOpacity=".3" />
          </linearGradient>
          <filter id="ppGaugeGlow">
            <feGaussianBlur stdDeviation="2" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <path d={arc(PI, 2 * PI, r)} fill="none" stroke="url(#ppGaugeGrad)" strokeWidth="7" strokeLinecap="round" />
        {animPct > 0.01 && (
          <path
            d={arc(
              Math.min(PI + PI * 0.5, PI + PI * (0.5 + (isYes ? animPct : -animPct) * 0.5)),
              Math.max(PI + PI * 0.5, PI + PI * (0.5 + (isYes ? animPct : -animPct) * 0.5)),
              r,
            )}
            fill="none"
            stroke={col}
            strokeWidth="7"
            strokeLinecap="round"
            filter="url(#ppGaugeGlow)"
            opacity=".9"
          />
        )}
        <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={col} strokeWidth="2.5" strokeLinecap="round" />
        <circle cx={cx} cy={cy} r="5" fill={col} filter="url(#ppGaugeGlow)" />
        <text x={compact ? 12 : 20} y={H - 5} fill="var(--red)" fontSize={fss} opacity=".6">
          NO
        </text>
        <text x={compact ? 105 : 150} y={H - 5} fill="var(--green)" fontSize={fss} opacity=".6">
          YES
        </text>
        <text
          x={cx}
          y={cy - 18}
          textAnchor="middle"
          fill={col}
          fontSize={fs}
          fontFamily="JetBrains Mono"
          fontWeight="600"
          filter="url(#ppGaugeGlow)"
        >
          {edge > 0 ? "+" : ""}
          {edge.toFixed(1)}pp
        </text>
        <text x={cx} y={cy - 5} textAnchor="middle" fill="var(--muted)" fontSize={fss}>
          edge vs. market
        </text>
      </svg>
    </div>
  );
}

function ProbabilityChart({
  history,
  bandLo,
  bandHi,
  showConf,
}: {
  history: MarketHistoryPoint[];
  bandLo: number | null;
  bandHi: number | null;
  showConf: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 460;
  const H = 150;
  const pad = { t: 16, r: 10, b: 26, l: 32 };
  const IW = W - pad.l - pad.r;
  const IH = H - pad.t - pad.b;
  if (history.length === 0) {
    return (
      <div
        style={{
          height: 150,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--muted)",
          fontSize: 12,
        }}
      >
        No history available yet.
      </div>
    );
  }
  const allVals = history.flatMap((p) => [p.market_mid * 100, (p.model_prob ?? p.market_mid) * 100]);
  if (bandLo != null) allVals.push(bandLo * 100);
  if (bandHi != null) allVals.push(bandHi * 100);
  const mn = Math.max(0, Math.min(...allVals) - 8);
  const mx = Math.min(100, Math.max(...allVals) + 8);
  const sx = (i: number) => pad.l + (i / Math.max(1, history.length - 1)) * IW;
  const sy = (v: number) => pad.t + IH - ((v - mn) / Math.max(0.0001, mx - mn)) * IH;
  const pmPath = history
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(i)} ${sy(p.market_mid * 100)}`)
    .join(" ");
  const ppPathPts = history.filter((p) => p.model_prob != null);
  const ppPath = ppPathPts.length
    ? history
        .map((p, i) =>
          p.model_prob == null
            ? null
            : `${ppPathPts[0] === p ? "M" : "L"}${sx(i)} ${sy(p.model_prob * 100)}`,
        )
        .filter(Boolean)
        .join(" ")
    : "";

  const ticks = [mn + 5, Math.round((mn + mx) / 2), mx - 5];
  const fmtTime = (iso: string) => {
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  };
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: ".09em",
            textTransform: "uppercase",
            color: "var(--muted)",
          }}
        >
          7-Day Probability History
        </span>
        <div style={{ display: "flex", gap: 10 }}>
          {[
            ["var(--violet)", "Market"],
            ["var(--green)", "PP Estimate"],
            ...(showConf && bandLo != null && bandHi != null ? [["var(--green)", "Conf. band"]] : []),
          ].map(([c, l], i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {l === "Conf. band" ? (
                <div
                  style={{
                    width: 14,
                    height: 8,
                    background: c,
                    opacity: 0.15,
                    border: `1px solid ${c}55`,
                    borderRadius: 2,
                  }}
                />
              ) : (
                <div style={{ width: 14, height: 2, background: c, borderRadius: 1 }} />
              )}
              <span style={{ fontSize: 9, color: "var(--muted)" }}>{l}</span>
            </div>
          ))}
        </div>
      </div>
      <svg
        width="100%"
        viewBox={`0 0 ${W} ${H}`}
        style={{ overflow: "visible" }}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const x = ((e.clientX - rect.left) / rect.width) * W;
          setHover(Math.max(0, Math.min(history.length - 1, Math.round(((x - pad.l) / IW) * (history.length - 1)))));
        }}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} y1={sy(v)} x2={W - pad.r} y2={sy(v)} stroke="var(--border)" strokeWidth="1" />
            <text
              x={pad.l - 5}
              y={sy(v) + 4}
              textAnchor="end"
              fontSize="8"
              fill="var(--muted)"
              fontFamily="JetBrains Mono"
            >
              {Math.round(v)}%
            </text>
          </g>
        ))}
        {[0, Math.floor(history.length / 2), history.length - 1].map((i) =>
          history[i] ? (
            <text key={i} x={sx(i)} y={H} textAnchor="middle" fontSize="7.5" fill="var(--muted)">
              {fmtTime(history[i].event_time)}
            </text>
          ) : null,
        )}
        {showConf && bandLo != null && bandHi != null && (
          <>
            <rect
              x={pad.l}
              y={sy(bandHi * 100)}
              width={IW}
              height={Math.max(0, sy(bandLo * 100) - sy(bandHi * 100))}
              fill="var(--green)"
              opacity=".07"
            />
            <line
              x1={pad.l}
              y1={sy(bandLo * 100)}
              x2={W - pad.r}
              y2={sy(bandLo * 100)}
              stroke="var(--green)"
              strokeWidth="1"
              strokeDasharray="3,3"
              opacity=".3"
            />
            <line
              x1={pad.l}
              y1={sy(bandHi * 100)}
              x2={W - pad.r}
              y2={sy(bandHi * 100)}
              stroke="var(--green)"
              strokeWidth="1"
              strokeDasharray="3,3"
              opacity=".3"
            />
          </>
        )}
        <path d={pmPath} fill="none" stroke="var(--violet)" strokeWidth="2" strokeLinejoin="round" />
        {ppPath && (
          <path d={ppPath} fill="none" stroke="var(--green)" strokeWidth="2" strokeLinejoin="round" />
        )}
        {hover != null && history[hover] && (
          <>
            <line
              x1={sx(hover)}
              y1={pad.t}
              x2={sx(hover)}
              y2={H - pad.b}
              stroke="var(--border2)"
              strokeWidth="1"
              strokeDasharray="3,3"
            />
            <circle
              cx={sx(hover)}
              cy={sy(history[hover].market_mid * 100)}
              r="4"
              fill="var(--violet)"
              stroke="var(--bg)"
              strokeWidth="2"
            />
            {history[hover].model_prob != null && (
              <circle
                cx={sx(hover)}
                cy={sy((history[hover].model_prob as number) * 100)}
                r="4"
                fill="var(--green)"
                stroke="var(--bg)"
                strokeWidth="2"
              />
            )}
          </>
        )}
      </svg>
    </div>
  );
}

function FeatureRow({ attr }: { attr: FeatureAttribution }) {
  const s = attr.score_contribution;
  const dir = s > 0.005 ? 1 : s < -0.005 ? -1 : 0;
  const col = dir > 0 ? "var(--green)" : dir < 0 ? "var(--red)" : "var(--muted)";
  const arr = dir > 0 ? "↑" : dir < 0 ? "↓" : "→";
  const strength = Math.min(1, Math.abs(s) / 0.5);
  return (
    <div style={{ padding: "11px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
        <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{attr.label || attr.feature_name}</span>
        <span style={{ color: col, fontSize: 13, fontWeight: 700 }}>{arr}</span>
        <span className="font-mono" style={{ fontSize: 11, color: col }}>
          {s > 0 ? "+" : ""}
          {s.toFixed(3)}
        </span>
      </div>
      <div style={{ height: 3, background: "var(--border)", borderRadius: 2, marginBottom: 6 }}>
        <div
          style={{
            height: "100%",
            width: `${strength * 100}%`,
            background: col,
            borderRadius: 2,
            opacity: 0.8,
            transition: "width .8s ease",
          }}
        />
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
        feature value{" "}
        <span className="font-mono" style={{ color: "var(--text)" }}>
          {attr.feature_value != null ? attr.feature_value.toFixed(3) : "n/a"}
        </span>
        {" · transformed "}
        <span className="font-mono" style={{ color: "var(--text)" }}>
          {attr.transformed_value.toFixed(3)}
        </span>
      </div>
    </div>
  );
}

function SignalEventRow({ event }: { event: SignalEvent }) {
  const dirVal = event.direction === "buy" || event.direction === "yes" ? 1 : event.direction === "sell" || event.direction === "no" ? -1 : 0;
  const col = dirVal > 0 ? "var(--green)" : dirVal < 0 ? "var(--red)" : "var(--muted)";
  const arr = dirVal > 0 ? "↑" : dirVal < 0 ? "↓" : "→";
  return (
    <div style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span
          style={{
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: ".08em",
            color: col,
            background: `${col}14`,
            border: `1px solid ${col}33`,
            padding: "2px 6px",
            borderRadius: 4,
          }}
        >
          {event.event_type}
        </span>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>{event.actor || "—"}</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: col, fontSize: 13, fontWeight: 700 }}>{arr}</span>
        <span className="font-mono" style={{ fontSize: 11, color: "var(--muted)" }}>
          sev {event.severity.toFixed(1)}
        </span>
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
        {event.size_delta_usdc != null && (
          <>
            Δ size <span className="font-mono">{fmtUsd(event.size_delta_usdc)}</span>
            {" · "}
          </>
        )}
        <span className="font-mono">{fmtDaysAgo(event.event_time)}</span>
      </div>
    </div>
  );
}

function DetailPanel({
  market,
  onClose,
  showConf,
  inline,
  isMobile,
}: {
  market: EnrichedMarket;
  onClose: () => void;
  showConf: boolean;
  inline: boolean;
  isMobile: boolean;
}) {
  const [tab, setTab] = useState<"overview" | "signals" | "sizing">("overview");

  const modelQ = useQuery({
    queryKey: ["model", market.condition_id],
    queryFn: () => fetchMarketModel(market.condition_id),
    refetchInterval: 30_000,
  });

  const historyQ = useQuery({
    queryKey: ["history", market.condition_id],
    queryFn: () => fetchMarketHistory(market.condition_id, 168, 96),
    refetchInterval: 60_000,
  });

  const signalsQ = useQuery({
    queryKey: ["signals", market.condition_id],
    queryFn: () => fetchSignals({ conditionId: market.condition_id, limit: 30, lookbackHours: 168 }),
    refetchInterval: 30_000,
  });

  const model: MarketModel | undefined = modelQ.data;
  const estimatePct = model?.model_prob != null ? Math.round((model.model_prob ?? 0) * 1000) / 10 : market.estimatePct;
  const marketPct = model?.mid != null ? Math.round((model.mid ?? 0) * 1000) / 10 : market.marketPct;
  const edge = estimatePct != null && marketPct != null ? Math.round((estimatePct - marketPct) * 10) / 10 : 0;
  const edgeCol = edgeColor(edge);
  const eLabel = edgeLabel(edge);

  const bandLo = model?.band_lo ?? null;
  const bandHi = model?.band_hi ?? null;
  const conflicted = useMemo(() => {
    if (!model) return false;
    return Boolean(model.adversarial_flow_flagged) || Boolean(model.resolution_risk_flagged);
  }, [model]);

  const featureAttributions = (model?.feature_attributions ?? []).slice().sort((a, b) => Math.abs(b.score_contribution) - Math.abs(a.score_contribution));

  const kFraction = model?.kelly_fraction ?? 0;
  const kSide = model?.kelly_side ?? null;
  const [bankroll, setBankroll] = useState(1000);

  const TB = ({ id, label, badge }: { id: typeof tab; label: string; badge?: boolean }) => (
    <button
      onClick={() => setTab(id)}
      style={{
        flex: 1,
        padding: "9px 4px",
        background: "none",
        border: "none",
        borderBottom: `2px solid ${tab === id ? "var(--green)" : "transparent"}`,
        color: tab === id ? "var(--green)" : "var(--muted)",
        fontSize: 12,
        fontWeight: tab === id ? 600 : 400,
        cursor: "pointer",
        transition: "all .15s",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 5,
      }}
    >
      {label}
      {badge && (
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "var(--amber)",
            display: "inline-block",
          }}
        />
      )}
    </button>
  );

  const wrap: CSSProperties = inline
    ? {
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        background: "var(--surface)",
      }
    : {
        position: "fixed",
        top: 0,
        right: 0,
        width: isMobile ? "100vw" : "min(600px,100vw)",
        height: "100dvh",
        background: "var(--surface)",
        borderLeft: "1px solid var(--border2)",
        display: "flex",
        flexDirection: "column",
        zIndex: 100,
        animation: "pp-slide-in-right .25s ease",
        boxShadow: "-20px 0 60px #000a",
      };

  return (
    <div style={wrap}>
      {/* header */}
      <div
        style={{
          padding: "10px 20px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          gap: 12,
          alignItems: "flex-start",
          flexShrink: 0,
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap", marginBottom: 6 }}>
            <span
              style={{
                background: "var(--surface3)",
                color: "var(--muted)",
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: ".09em",
                padding: "2px 7px",
                borderRadius: 4,
              }}
            >
              {categoryShort(market.cat)}
            </span>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              Vol {fmtUsd(market.volume_usdc)} · Liq {fmtUsd(market.liquidity_usdc)}
            </span>
            {market.daysLeft != null && (
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: market.daysLeft <= 4 ? "var(--red)" : "var(--amber)",
                }}
              >
                ⏱ {market.daysLeft}d left
              </span>
            )}
            {conflicted && (
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: "var(--amber)",
                  background: "var(--amber-dim)",
                  padding: "2px 7px",
                  borderRadius: 4,
                }}
              >
                ⚡ Risk flagged
              </span>
            )}
          </div>
          <div style={{ fontSize: 13, fontWeight: 500, lineHeight: 1.45 }}>{market.question}</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, flexShrink: 0 }}>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--muted)",
              cursor: "pointer",
              fontSize: 20,
              padding: 4,
              lineHeight: 1,
            }}
          >
            ✕
          </button>
          <span className="font-mono" style={{ fontSize: 9, color: "var(--muted2)", letterSpacing: ".04em" }}>
            esc
          </span>
        </div>
      </div>

      {/* gauge row */}
      <div
        style={{
          padding: "8px 20px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          gap: 12,
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <EdgeGauge edge={edge} compact />
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 9 }}>
            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: ".08em",
                color: "var(--muted)",
                textTransform: "uppercase",
                marginBottom: 3,
              }}
            >
              PP Estimate
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
              <span className="font-mono" style={{ fontSize: 28, fontWeight: 600, color: "var(--green)" }}>
                {estimatePct != null ? `${estimatePct.toFixed(1)}%` : "—"}
              </span>
            </div>
            {showConf && bandLo != null && bandHi != null && (
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 1 }}>
                Band: {(bandLo * 100).toFixed(0)}% – {(bandHi * 100).toFixed(0)}%
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
            <div>
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 2 }}>Market</div>
              <div className="font-mono" style={{ fontSize: 18, color: "var(--violet)" }}>
                {marketPct != null ? `${marketPct.toFixed(1)}%` : "—"}
              </div>
            </div>
            <span
              style={{
                padding: "3px 10px",
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 600,
                background: `${edgeCol}22`,
                color: edgeCol,
                border: `1px solid ${edgeCol}44`,
              }}
            >
              {eLabel}
            </span>
          </div>
        </div>
      </div>

      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <TB id="overview" label="Chart" />
        <TB id="signals" label="Signals" badge={conflicted} />
        <TB id="sizing" label="Kelly" />
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
        {tab === "overview" && (
          <>
            {historyQ.isLoading ? (
              <div style={{ color: "var(--muted)", fontSize: 12, padding: "40px 0", textAlign: "center" }}>
                Loading history…
              </div>
            ) : (
              <ProbabilityChart
                history={historyQ.data ?? []}
                bandLo={bandLo}
                bandHi={bandHi}
                showConf={showConf}
              />
            )}
            <div style={{ marginTop: 18 }}>
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: ".09em",
                  textTransform: "uppercase",
                  color: "var(--muted)",
                  marginBottom: 10,
                }}
              >
                Drivers
              </div>
              {modelQ.isLoading && (
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Loading model…</div>
              )}
              {model?.driver_summaries?.length ? (
                <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text)", fontSize: 12, lineHeight: 1.6 }}>
                  {model.driver_summaries.slice(0, 6).map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              ) : (
                !modelQ.isLoading && (
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>No driver summaries yet.</div>
                )
              )}
            </div>
            <div style={{ height: 12 }} />
          </>
        )}

        {tab === "signals" && (
          <>
            {conflicted && (
              <div
                style={{
                  background: "var(--amber-dim)",
                  border: "1px solid var(--amber)",
                  borderRadius: 8,
                  padding: "10px 14px",
                  marginBottom: 12,
                  fontSize: 11,
                  color: "var(--amber)",
                  lineHeight: 1.5,
                }}
              >
                ⚡ <strong>Risk flagged.</strong>{" "}
                {model?.resolution_risk_flagged
                  ? `Resolution risk: ${model.resolution_risk_level ?? "elevated"}.`
                  : ""}{" "}
                {model?.adversarial_flow_flagged ? "Adversarial flow detected." : ""}
              </div>
            )}
            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: ".1em",
                textTransform: "uppercase",
                color: "var(--muted)",
                padding: "10px 0 6px",
                borderBottom: "1px solid var(--border)",
                marginBottom: 2,
              }}
            >
              Feature Drivers
            </div>
            {modelQ.isLoading && <div style={{ fontSize: 12, color: "var(--muted)", padding: "12px 0" }}>Loading…</div>}
            {!modelQ.isLoading && featureAttributions.length === 0 && (
              <div style={{ fontSize: 12, color: "var(--muted)", padding: "12px 0" }}>
                No feature attributions returned for this market.
              </div>
            )}
            {featureAttributions.slice(0, 8).map((a) => (
              <FeatureRow key={a.feature_name} attr={a} />
            ))}

            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: ".1em",
                textTransform: "uppercase",
                color: "var(--muted)",
                padding: "16px 0 6px",
                borderBottom: "1px solid var(--border)",
                marginTop: 12,
              }}
            >
              Live Signals
            </div>
            {signalsQ.isLoading && <div style={{ fontSize: 12, color: "var(--muted)", padding: "12px 0" }}>Loading…</div>}
            {!signalsQ.isLoading && (signalsQ.data ?? []).length === 0 && (
              <div style={{ fontSize: 12, color: "var(--muted)", padding: "12px 0" }}>
                No signal events in the last 7 days.
              </div>
            )}
            {(signalsQ.data ?? []).slice(0, 12).map((evt) => (
              <SignalEventRow key={evt.event_id} event={evt} />
            ))}
            <div style={{ height: 12 }} />
          </>
        )}

        {tab === "sizing" && (
          <>
            <div style={{ border: "1px solid var(--border2)", borderRadius: 8, padding: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4, display: "flex", gap: 8, alignItems: "center" }}>
                ₭ Kelly Position Sizing
                {kFraction > 0 && (
                  <span className="font-mono" style={{ fontSize: 11, color: "var(--green)" }}>
                    {(kFraction * 100).toFixed(1)}% Kelly
                    {kSide ? ` (${kSide.toUpperCase()})` : ""}
                  </span>
                )}
              </div>
              {kFraction <= 0 ? (
                <div style={{ fontSize: 12, color: "var(--red)", padding: "8px 0" }}>
                  No edge — Kelly recommends no position at current prices.
                </div>
              ) : (
                <>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
                    PP{" "}
                    <span className="font-mono" style={{ color: "var(--green)" }}>
                      {estimatePct?.toFixed(1)}%
                    </span>{" "}
                    · PM{" "}
                    <span className="font-mono" style={{ color: "var(--violet)" }}>
                      {marketPct?.toFixed(1)}%
                    </span>{" "}
                    · cap{" "}
                    <span className="font-mono">
                      {(model?.kelly_cap ?? 0) > 0 ? `${((model?.kelly_cap ?? 0) * 100).toFixed(0)}%` : "n/a"}
                    </span>
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      marginBottom: 12,
                      background: "var(--surface2)",
                      border: "1px solid var(--border)",
                      borderRadius: 7,
                      padding: "7px 12px",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>$</span>
                    <input
                      type="number"
                      value={bankroll}
                      min={10}
                      onChange={(e) => setBankroll(Math.max(0, parseInt(e.target.value, 10) || 0))}
                      className="font-mono"
                      style={{
                        flex: 1,
                        background: "none",
                        border: "none",
                        outline: "none",
                        color: "var(--text)",
                        fontSize: 14,
                        fontWeight: 600,
                      }}
                    />
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>bankroll</span>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8, marginBottom: 12 }}>
                    {(
                      [
                        [kFraction, "Full Kelly", "var(--green)"],
                        [kFraction / 2, "Half Kelly", "var(--amber)"],
                        [kFraction / 4, "Quarter", "var(--muted)"],
                      ] as const
                    ).map(([k, l, c]) => (
                      <div
                        key={l}
                        style={{
                          background: "var(--surface2)",
                          border: "1px solid var(--border)",
                          borderRadius: 7,
                          padding: "10px 12px",
                        }}
                      >
                        <div
                          style={{
                            fontSize: 9,
                            color: "var(--muted)",
                            fontWeight: 700,
                            textTransform: "uppercase",
                            letterSpacing: ".07em",
                            marginBottom: 4,
                          }}
                        >
                          {l}
                        </div>
                        <div className="font-mono" style={{ fontSize: 16, fontWeight: 700, color: c }}>
                          ${Math.round(bankroll * k).toLocaleString()}
                        </div>
                        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                          {(k * 100).toFixed(1)}%
                        </div>
                      </div>
                    ))}
                  </div>
                  <div style={{ height: 5, background: "var(--border)", borderRadius: 3 }}>
                    <div
                      style={{
                        height: "100%",
                        width: `${Math.min(kFraction * 100, 100)}%`,
                        background: "var(--green)",
                        borderRadius: 3,
                        transition: "width .4s",
                        boxShadow: "0 0 8px var(--green)",
                      }}
                    />
                  </div>
                  <div style={{ fontSize: 10, color: "var(--muted2)", marginTop: 10, lineHeight: 1.5 }}>
                    Half Kelly reduces variance while capturing ~75% of max long-run growth.
                    {model?.kelly_uncapped_fraction != null && model.kelly_uncapped_fraction > kFraction && (
                      <>
                        {" "}
                        Backend uncapped Kelly: {(model.kelly_uncapped_fraction * 100).toFixed(1)}%.
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
            <div style={{ height: 20 }} />
          </>
        )}
      </div>

      <div
        style={{
          padding: "12px 20px",
          borderTop: "1px solid var(--border)",
          display: "flex",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <a
          href={`https://polymarket.com/market/${market.slug}`}
          target="_blank"
          rel="noreferrer"
          style={{
            flex: 1,
            padding: 9,
            borderRadius: 8,
            border: "1px solid var(--border2)",
            background: "none",
            color: "var(--text)",
            fontSize: 12,
            fontWeight: 500,
            textAlign: "center",
            textDecoration: "none",
            display: "block",
          }}
        >
          View on Polymarket ↗
        </a>
        <Link
          href={`/markets/${market.condition_id}` as Route}
          style={{
            flex: 1,
            padding: 9,
            borderRadius: 8,
            border: "none",
            background: "var(--green)",
            color: "#001a10",
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
            textAlign: "center",
            textDecoration: "none",
          }}
        >
          Open full detail →
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty / placeholder screens
// ---------------------------------------------------------------------------

function EmptyState({ icon, title, sub }: { icon: string; title: string; sub?: string }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        flex: 1,
        padding: "60px 24px",
        gap: 12,
        color: "var(--muted)",
        minHeight: 200,
      }}
    >
      <div style={{ fontSize: 36, opacity: 0.25 }}>{icon}</div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>{title}</div>
      {sub && (
        <div style={{ fontSize: 12, color: "var(--muted)", textAlign: "center", maxWidth: 320, lineHeight: 1.5 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function WatchlistScreen({
  trackedIds,
  markets,
  onSelect,
}: {
  trackedIds: Set<string>;
  markets: EnrichedMarket[];
  onSelect: (m: EnrichedMarket) => void;
}) {
  const tracked = markets.filter((m) => trackedIds.has(m.condition_id));
  if (tracked.length === 0) {
    return (
      <EmptyState
        icon="★"
        title="No tracked markets"
        sub="Click ☆ on any market card to start tracking it. Tracked markets appear here with quick edge updates."
      />
    );
  }
  const totalEdge = tracked.reduce((a, m) => a + (m.edgePp ?? 0), 0);
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 22 }}>
        {[
          { label: "Tracked", val: String(tracked.length), col: "var(--text)" },
          { label: "Total edge", val: `${totalEdge >= 0 ? "+" : ""}${totalEdge.toFixed(1)}pp`, col: "var(--green)" },
          {
            label: "Strong edges",
            val: String(tracked.filter((m) => Math.abs(m.edgePp ?? 0) >= 8).length),
            col: "var(--amber)",
          },
        ].map((s) => (
          <div
            key={s.label}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: "12px 16px",
            }}
          >
            <div
              style={{
                fontSize: 10,
                color: "var(--muted)",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: ".08em",
                marginBottom: 6,
              }}
            >
              {s.label}
            </div>
            <div className="font-mono" style={{ fontSize: 20, fontWeight: 600, color: s.col }}>
              {s.val}
            </div>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {tracked.map((m) => (
          <MarketCard
            key={m.condition_id}
            market={m}
            active={false}
            compact={false}
            search=""
            tracked
            onTrack={() => {}}
            onClick={() => onSelect(m)}
          />
        ))}
      </div>
      <div style={{ height: 20 }} />
    </div>
  );
}

function PlaceholderScreen({
  icon,
  title,
  sub,
  cta,
}: {
  icon: string;
  title: string;
  sub: string;
  cta?: { label: string; onClick: () => void };
}) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
      <EmptyState icon={icon} title={title} sub={sub} />
      {cta && (
        <div style={{ textAlign: "center", paddingBottom: 24 }}>
          <button
            type="button"
            onClick={cta.onClick}
            style={{
              padding: "8px 16px",
              borderRadius: 8,
              border: "1px solid var(--green)",
              color: "var(--green)",
              background: "var(--green-dim)",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            {cta.label} →
          </button>
        </div>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: SystemStatus | undefined }) {
  if (!status) return null;
  const col =
    status.state === "operational"
      ? "var(--green)"
      : status.state === "degraded"
      ? "var(--amber)"
      : status.state === "down"
      ? "var(--red)"
      : "var(--muted)";
  return (
    <Link
      href="/status"
      title={`System: ${status.state}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        color: col,
        textDecoration: "none",
        padding: "4px 9px",
        borderRadius: 6,
        border: `1px solid ${col}44`,
        background: `${col}14`,
      }}
      className="font-mono"
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: col,
          boxShadow: `0 0 5px ${col}`,
        }}
      />
      {status.state}
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Main app
// ---------------------------------------------------------------------------

function useIsMobile() {
  const [m, setM] = useState(false);
  useEffect(() => {
    const update = () => setM(window.innerWidth < 768);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return m;
}

export default function HomeDashboard() {
  const isMobile = useIsMobile();
  const [screen, setScreen] = useState<ScreenId>("markets");
  const [selected, setSelected] = useState<EnrichedMarket | null>(null);
  const [filter, setFilter] = useState<"All" | CategoryKey>("All");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("edge-desc");
  const [compact, setCompact] = useState(false);
  const [strongOnly, setStrongOnly] = useState(false);
  const [cryptoOnly, setCryptoOnly] = useState(true);
  const [showConf] = useState(true);
  const [trackedIds, setTrackedIds] = useState<Set<string>>(() => new Set());
  const [liveTs, setLiveTs] = useState(Date.now());
  const [liveStr, setLiveStr] = useState("just now");

  const marketsQ = useQuery({
    queryKey: ["markets-dashboard", cryptoOnly],
    queryFn: () => fetchMarkets({ cryptoOnly }),
    refetchInterval: 5_000,
  });

  const statusQ = useQuery({
    queryKey: ["system-status"],
    queryFn: () => fetchSystemStatus(),
    refetchInterval: 30_000,
  });

  useEffect(() => {
    if (marketsQ.dataUpdatedAt) setLiveTs(marketsQ.dataUpdatedAt);
  }, [marketsQ.dataUpdatedAt]);

  useEffect(() => {
    const t = setInterval(() => setLiveStr(fmtLiveAge(Date.now() - liveTs)), 1000);
    return () => clearInterval(t);
  }, [liveTs]);

  // Persist tracked IDs in localStorage
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("pp_tracked") ?? "[]");
      if (Array.isArray(saved)) setTrackedIds(new Set(saved.filter((s) => typeof s === "string")));
    } catch {
      /* noop */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("pp_tracked", JSON.stringify([...trackedIds]));
    } catch {
      /* noop */
    }
  }, [trackedIds]);

  const trackMarket = useCallback((id: string) => {
    setTrackedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const enriched = useMemo(() => enrich(marketsQ.data ?? []), [marketsQ.data]);

  const filtered = useMemo(() => {
    let list = enriched;
    if (filter !== "All") list = list.filter((m) => m.cat === filter);
    if (search.trim().length >= 2) {
      const q = search.toLowerCase();
      list = list.filter(
        (m) =>
          m.question.toLowerCase().includes(q) ||
          (m.category ?? "").toLowerCase().includes(q) ||
          (m.tags ?? []).some((t) => t.toLowerCase().includes(q)),
      );
    }
    if (strongOnly) list = list.filter((m) => Math.abs(m.edgePp ?? 0) >= 8);
    list = [...list];
    if (sort === "edge-desc") list.sort((a, b) => Math.abs(b.edgePp ?? 0) - Math.abs(a.edgePp ?? 0));
    else if (sort === "edge-asc") list.sort((a, b) => Math.abs(a.edgePp ?? 0) - Math.abs(b.edgePp ?? 0));
    else if (sort === "days") list.sort((a, b) => (a.daysLeft ?? 1e9) - (b.daysLeft ?? 1e9));
    else if (sort === "volume") list.sort((a, b) => (b.volume_usdc ?? 0) - (a.volume_usdc ?? 0));
    return list;
  }, [enriched, filter, search, strongOnly, sort]);

  const showSplit = !!selected && !isMobile;
  const handleNav = (id: ScreenId) => {
    setScreen(id);
    setSelected(null);
  };

  const strongCount = enriched.filter((m) => Math.abs(m.edgePp ?? 0) >= 8).length;
  const avgConfidence = useMemo(() => {
    const vals = enriched.map((m) => m.confidence).filter((c): c is number => typeof c === "number");
    if (!vals.length) return null;
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    return Math.round(avg * 100);
  }, [enriched]);
  const flaggedCount = enriched.filter((m) => m.resolution_risk_flagged || m.adversarial_flow_flagged || m.thin_book).length;

  const summaryStats = [
    {
      label: "Strong edges",
      val: String(strongCount),
      col: "var(--green)",
      action: () => {
        setStrongOnly((v) => !v);
        setFilter("All");
      },
      tip: "Click to filter",
    },
    {
      label: "Avg confidence",
      val: avgConfidence != null ? `${avgConfidence}%` : "—",
      col: "var(--violet)",
    },
    {
      label: "Flagged",
      val: String(flaggedCount),
      col: "var(--amber)",
      tip: "Risk-flagged markets",
    },
    {
      label: "Tracked",
      val: String(trackedIds.size),
      col: "var(--muted)",
      action: () => handleNav("watchlist"),
      tip: "Go to watchlist",
    },
  ] as Array<{ label: string; val: string; col: string; action?: () => void; tip?: string }>;

  const screenTitles: Record<ScreenId, { title: string; sub: string }> = {
    markets: { title: "Markets", sub: `Updated ${liveStr}` },
    watchlist: { title: "Watchlist", sub: `${trackedIds.size} tracked` },
    correlation: { title: "Signals", sub: "Recent signal activity" },
    backtest: { title: "Backtest", sub: "Historical accuracy" },
    alerts: { title: "Alerts", sub: "Edge & signal updates" },
    settings: { title: "Settings", sub: "Preferences & configuration" },
  };

  return (
    <div className="pp-shell">
      {!isMobile && <Sidebar screen={screen} onChange={handleNav} unread={0} />}
      <div
        style={{
          flex: 1,
          display: "flex",
          overflow: "hidden",
          paddingBottom: isMobile ? 56 : 0,
        }}
      >
        <div
          style={{
            width: showSplit ? "clamp(340px, 38%, 480px)" : "100%",
            minWidth: showSplit ? 300 : undefined,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            transition: "width .3s ease",
            borderRight: showSplit ? "1px solid var(--border)" : "none",
          }}
        >
          <div
            style={{
              padding: "14px 20px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              alignItems: "center",
              gap: 12,
              flexShrink: 0,
            }}
          >
            {isMobile && <Logo />}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 15, fontWeight: 700 }}>{screenTitles[screen].title}</div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>{screenTitles[screen].sub}</div>
            </div>
            {showSplit && selected && (
              <div
                style={{
                  flex: 2,
                  minWidth: 0,
                  padding: "0 12px",
                  borderLeft: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: "var(--muted)",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: ".08em",
                    marginBottom: 2,
                  }}
                >
                  Viewing
                </div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--text)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    maxWidth: 280,
                  }}
                >
                  {selected.question}
                </div>
              </div>
            )}
            <StatusPill status={statusQ.data} />
          </div>

          {screen === "markets" && (
            <div style={{ flex: 1, overflowY: "auto", padding: "10px 20px 20px" }}>
              <FilterTabs
                active={filter}
                onChange={setFilter}
                liveStr={liveStr}
                sort={sort}
                onSort={setSort}
                compact={compact}
                onCompact={() => setCompact((v) => !v)}
                count={filtered.length}
                strongOnly={strongOnly}
                onStrongOnly={setStrongOnly}
                search={search}
                onSearch={setSearch}
                cryptoOnly={cryptoOnly}
                onCryptoOnly={setCryptoOnly}
              />
              <div
                style={{
                  display: "flex",
                  padding: "8px 12px",
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  marginBottom: 12,
                  alignItems: "stretch",
                  overflow: "hidden",
                }}
              >
                {summaryStats.map((s, i) => (
                  <div key={s.label} style={{ display: "contents" }}>
                    {i > 0 && (
                      <div
                        style={{
                          width: 1,
                          background: "var(--border2)",
                          flexShrink: 0,
                          margin: "0 12px",
                          alignSelf: "stretch",
                        }}
                      />
                    )}
                    <div
                      onClick={s.action}
                      title={s.tip}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        cursor: s.action ? "pointer" : "default",
                        borderRadius: 6,
                        padding: "4px 8px",
                        flex: 1,
                        ...(s.action && strongOnly && s.label === "Strong edges"
                          ? { background: "var(--green-dim)" }
                          : {}),
                      }}
                    >
                      <span
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: s.col,
                          flexShrink: 0,
                          opacity: 0.85,
                          boxShadow: s.action ? `0 0 6px ${s.col}` : "none",
                        }}
                      />
                      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                        <span
                          style={{
                            fontSize: 9,
                            color: "var(--muted)",
                            fontWeight: 600,
                            textTransform: "uppercase",
                            letterSpacing: ".09em",
                            whiteSpace: "nowrap",
                            lineHeight: 1,
                          }}
                        >
                          {s.label}
                        </span>
                        <span
                          className="font-mono"
                          style={{
                            fontSize: 14,
                            fontWeight: 700,
                            color: s.col,
                            lineHeight: 1,
                          }}
                        >
                          {s.val}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              {marketsQ.isLoading && (
                <div style={{ color: "var(--muted)", fontSize: 12, textAlign: "center", padding: "40px 0" }}>
                  Loading markets…
                </div>
              )}
              {marketsQ.error != null && !marketsQ.isLoading && (
                <div style={{ color: "var(--red)", fontSize: 12, textAlign: "center", padding: "20px 0" }}>
                  Failed to load markets. The API may be offline.
                </div>
              )}
              {!marketsQ.isLoading && filtered.length === 0 && (
                <EmptyState
                  icon="⌕"
                  title="No markets match"
                  sub={search ? `No results for "${search}". Try a different keyword or clear filters.` : "Try clearing filters or toggling crypto-only."}
                />
              )}
              {filtered.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                  {filtered.map((m) => (
                    <MarketCard
                      key={m.condition_id}
                      market={m}
                      active={selected?.condition_id === m.condition_id}
                      compact={compact}
                      search={search}
                      tracked={trackedIds.has(m.condition_id)}
                      onTrack={trackMarket}
                      onClick={() =>
                        setSelected((cur) => (cur?.condition_id === m.condition_id ? null : m))
                      }
                    />
                  ))}
                </div>
              )}
              <div style={{ height: 20 }} />
            </div>
          )}

          {screen === "watchlist" && (
            <WatchlistScreen
              trackedIds={trackedIds}
              markets={enriched}
              onSelect={(m) => {
                setSelected(m);
                setScreen("markets");
              }}
            />
          )}

          {screen === "correlation" && (
            <PlaceholderScreen
              icon="◎"
              title="Signal correlation"
              sub="Cross-market correlation view is coming soon. Live signal events for any single market are available on its detail panel under the Signals tab."
            />
          )}

          {screen === "backtest" && (
            <PlaceholderScreen
              icon="∿"
              title="Walk-forward backtests"
              sub="The full calibration view (calibration curves, Brier scores, regime breakdowns) is under Settings → Diagnostics."
              cta={{ label: "Open Diagnostics", onClick: () => setScreen("settings") }}
            />
          )}

          {screen === "alerts" && (
            <PlaceholderScreen
              icon="◑"
              title="Alerts"
              sub="Live signal alerts are surfaced in each market's Signals tab. Configure push delivery and the global feed under Settings → Notifications."
              cta={{ label: "Open Notifications", onClick: () => setScreen("settings") }}
            />
          )}

          {screen === "settings" && <Settings />}
        </div>

        {showSplit && selected && (
          <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", minWidth: 0 }}>
            <DetailPanel
              market={selected}
              onClose={() => setSelected(null)}
              showConf={showConf}
              inline
              isMobile={false}
            />
          </div>
        )}
      </div>

      {isMobile && <BottomNav screen={screen} onChange={handleNav} unread={0} />}

      {isMobile && selected && (
        <DetailPanel
          market={selected}
          onClose={() => setSelected(null)}
          showConf={showConf}
          inline={false}
          isMobile
        />
      )}
    </div>
  );
}
