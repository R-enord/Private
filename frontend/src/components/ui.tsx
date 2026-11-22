import type { ReactNode } from "react";
import { LABEL_DESCRIPTIONS, VERIFICATION_DESCRIPTIONS } from "../lib/format";

const LABEL_STYLE: Record<string, string> = {
  announcement: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  research: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  preview: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  beta: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  release: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  update: "bg-teal-100 text-teal-900 dark:bg-teal-950 dark:text-teal-200",
  deprecation: "bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200",
  rumor: "bg-fuchsia-100 text-fuchsia-900 dark:bg-fuchsia-950 dark:text-fuchsia-200",
  opportunity: "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200",
  discussion: "bg-slate-200 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
  unlabeled: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

export function LabelBadge({ label, evidence }: { label: string; evidence?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium uppercase tracking-wide ${LABEL_STYLE[label] ?? LABEL_STYLE.unlabeled}`}
      title={`${LABEL_DESCRIPTIONS[label] ?? label}${evidence ? ` — rule: ${evidence}` : ""}`}
    >
      {label === "rumor" ? "⚠ rumor" : label}
    </span>
  );
}

const VER_ICON: Record<string, string> = { official: "✔", corroborated: "✔✔", primary: "◆", community: "◇", unverified: "?" };

export function VerificationBadge({ level }: { level: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded border border-slate-300 px-1.5 py-0.5 text-xs text-slate-700 dark:border-slate-600 dark:text-slate-300"
      title={VERIFICATION_DESCRIPTIONS[level] ?? level}
    >
      <span aria-hidden>{VER_ICON[level] ?? "?"}</span>
      {level}
    </span>
  );
}

// Status palette (reserved) + icon + text: never color alone.
const HEALTH: Record<string, { color: string; icon: string }> = {
  healthy: { color: "var(--status-good)", icon: "●" },
  ok: { color: "var(--status-good)", icon: "●" },
  degraded: { color: "var(--status-warning)", icon: "▲" },
  rate_limited: { color: "var(--status-serious)", icon: "◷" },
  failing: { color: "var(--status-critical)", icon: "✖" },
  blocked: { color: "var(--status-critical)", icon: "⛔" },
  error: { color: "var(--status-critical)", icon: "✖" },
  unavailable: { color: "var(--status-serious)", icon: "○" },
  unknown: { color: "#8a8985", icon: "○" },
};

export function HealthBadge({ health }: { health: string }) {
  const h = HEALTH[health] ?? HEALTH.unknown;
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium">
      <span aria-hidden style={{ color: h.color }}>
        {h.icon}
      </span>
      {health.replace("_", " ")}
    </span>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}>
      {children}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-2 mt-6 flex items-baseline justify-between gap-2">
      <h2 className="text-lg font-semibold">{children}</h2>
      {right}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">{children}</div>;
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="my-2 rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">{error}</div>;
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const styles = {
    default: "border border-slate-300 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800",
    primary: "bg-blue-600 text-white hover:bg-blue-700",
    danger: "border border-rose-300 text-rose-700 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-300 dark:hover:bg-rose-950",
    ghost: "hover:bg-slate-100 dark:hover:bg-slate-800",
  }[variant];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick} className={`rounded px-2.5 py-1 text-sm disabled:opacity-50 ${styles}`}>
      {children}
    </button>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-40 shrink-0 text-slate-500">{k}</dt>
      <dd className="min-w-0 break-words">{v}</dd>
    </div>
  );
}

export function ExternalLink({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  if (!href) return <span className="text-slate-500">{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-blue-700 underline decoration-blue-300 hover:decoration-blue-700 dark:text-blue-400">
      {children}
    </a>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900" title={hint}>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}
