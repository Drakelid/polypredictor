"use client";

// Shared UI primitives for the settings screens.
// Pure presentational; matches the dashboard's design tokens.

import type { CSSProperties, ReactNode } from "react";

export function Card({
  title,
  meta,
  children,
  style,
}: {
  title?: string;
  meta?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: "16px 18px",
        ...style,
      }}
    >
      {(title || meta) && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 14,
            gap: 10,
          }}
        >
          {title && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: ".09em",
                textTransform: "uppercase",
                color: "var(--muted)",
              }}
            >
              {title}
            </span>
          )}
          {meta && (
            <span
              style={{
                fontSize: 10,
                color: "var(--muted2)",
                fontFamily: "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace",
                letterSpacing: ".04em",
                textAlign: "right",
              }}
            >
              {meta}
            </span>
          )}
        </div>
      )}
      {children}
    </div>
  );
}

export type BadgeTone = "neutral" | "accent" | "warn" | "bull" | "bear";

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: BadgeTone }) {
  const palette: Record<BadgeTone, { color: string; bg: string }> = {
    neutral: { color: "var(--muted)", bg: "var(--surface3)" },
    accent: { color: "var(--violet)", bg: "var(--violet-dim)" },
    warn: { color: "var(--amber)", bg: "var(--amber-dim)" },
    bull: { color: "var(--green)", bg: "var(--green-dim)" },
    bear: { color: "var(--red)", bg: "var(--red-dim)" },
  };
  const p = palette[tone];
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 600,
        padding: "2px 7px",
        borderRadius: 4,
        background: p.bg,
        color: p.color,
        border: `1px solid ${p.color}38`,
        letterSpacing: ".04em",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

export function StatTile({
  label,
  value,
  tone = "neutral",
  sub,
}: {
  label: string;
  value: string;
  tone?: "neutral" | "bull" | "bear" | "warn" | "accent";
  sub?: string;
}) {
  const colorMap: Record<string, string> = {
    neutral: "var(--text)",
    bull: "var(--green)",
    bear: "var(--red)",
    warn: "var(--amber)",
    accent: "var(--violet)",
  };
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "12px 14px",
      }}
    >
      <div
        style={{
          fontSize: 9,
          color: "var(--muted)",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: ".09em",
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontFamily: "var(--font-jetbrains-mono), 'JetBrains Mono', ui-monospace, monospace",
          fontSize: 20,
          fontWeight: 700,
          color: colorMap[tone] ?? colorMap.neutral,
          lineHeight: 1.1,
        }}
      >
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>{sub}</div>
      )}
    </div>
  );
}

export function FormRow({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          textTransform: "uppercase",
          color: "var(--muted)",
          letterSpacing: ".08em",
          marginBottom: 5,
        }}
      >
        {label}
        {required ? <span style={{ color: "var(--red)" }}> *</span> : null}
      </div>
      {children}
      {hint && (
        <div style={{ fontSize: 10, color: "var(--muted2)", marginTop: 4, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
    </label>
  );
}

export const inputStyle: CSSProperties = {
  width: "100%",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  padding: "7px 12px",
  borderRadius: 7,
  outline: "none",
  fontSize: 13,
  fontFamily: "inherit",
};

export const selectStyle: CSSProperties = {
  ...inputStyle,
  cursor: "pointer",
};

export const textareaStyle: CSSProperties = {
  ...inputStyle,
  resize: "none" as const,
  minHeight: 80,
  fontFamily: "inherit",
};

export function PrimaryButton({
  children,
  onClick,
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "7px 16px",
        borderRadius: 7,
        border: "none",
        background: disabled ? "var(--surface3)" : "var(--green)",
        color: disabled ? "var(--muted)" : "#001a10",
        fontSize: 12,
        fontWeight: 700,
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "all .15s",
      }}
    >
      {children}
    </button>
  );
}

export function SecondaryButton({
  children,
  onClick,
  disabled,
  tone = "neutral",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  tone?: "neutral" | "danger";
}) {
  const color = tone === "danger" ? "var(--red)" : "var(--text)";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "7px 14px",
        borderRadius: 7,
        border: `1px solid var(--border2)`,
        background: "transparent",
        color: disabled ? "var(--muted)" : color,
        fontSize: 12,
        fontWeight: 600,
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "all .15s",
      }}
    >
      {children}
    </button>
  );
}

export function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div
      onClick={() => onChange(!checked)}
      style={{
        width: 36,
        height: 20,
        borderRadius: 10,
        background: checked ? "var(--green)" : "var(--border2)",
        cursor: "pointer",
        position: "relative",
        transition: "background .2s",
        flexShrink: 0,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: 2,
          left: checked ? 18 : 2,
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: "white",
          transition: "left .2s",
          boxShadow: "0 1px 3px #0006",
        }}
      />
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 9,
        cursor: "pointer",
        fontSize: 12,
        color: "var(--text)",
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{
          marginTop: 3,
          width: 14,
          height: 14,
          accentColor: "var(--green)",
          cursor: "pointer",
        }}
      />
      <div>
        <div style={{ fontWeight: 500 }}>{label}</div>
        {hint && (
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3, lineHeight: 1.5 }}>{hint}</div>
        )}
      </div>
    </label>
  );
}

export function ErrorText({ children }: { children: ReactNode }) {
  return (
    <div style={{ fontSize: 11, color: "var(--red)", marginTop: 8 }}>{children}</div>
  );
}

export function HintText({ children }: { children: ReactNode }) {
  return (
    <div style={{ fontSize: 11, color: "var(--muted2)", marginTop: 6, lineHeight: 1.5 }}>{children}</div>
  );
}
