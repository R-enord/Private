// Small, accessible charts following the data-viz method: single series (no legend box; the title names it),
// series color #2a78d6 / #3987e5 validated against both surfaces, 2px lines, <=24px bars with 4px rounded
// data-ends square at the baseline, recessive hairline grid, text in text tokens, hover tooltips, and a
// table view for every chart.
import { useState } from "react";
import type { ScoreComponent } from "../lib/types";
import { absoluteTime, compactNumber } from "../lib/format";

const COMPONENT_NAMES: Record<string, string> = {
  recency: "Recency", credibility: "Source credibility", relevance: "AI relevance", novelty: "Novelty",
  adoption: "Adoption / growth", utility: "Practical utility", corroboration: "Corroboration",
};

/** Horizontal bars: each component's contribution (points of the 0–100 score). */
export function ScoreBreakdown({ components }: { components: Record<string, ScoreComponent> }) {
  const [hover, setHover] = useState<string | null>(null);
  const rows = Object.entries(components).sort((a, b) => b[1].contribution - a[1].contribution);
  const max = Math.max(1, ...rows.map(([, c]) => c.contribution));
  return (
    <div className="viz-root" role="figure" aria-label="Score breakdown by component">
      <div className="flex flex-col gap-1.5">
        {rows.map(([k, c]) => (
          <div
            key={k}
            className="relative grid grid-cols-[9.5rem_1fr_3.5rem] items-center gap-2 text-sm"
            onMouseEnter={() => setHover(k)}
            onMouseLeave={() => setHover(null)}
          >
            <span style={{ color: "var(--text-primary)" }}>
              {COMPONENT_NAMES[k] ?? k}
              <span className="ml-1 text-[10px] uppercase" style={{ color: "var(--text-secondary)" }}>
                {c.kind}
              </span>
            </span>
            <div className="h-5 py-1" style={{ borderLeft: "1px solid var(--grid)" }}>
              <div
                className="h-3"
                style={{
                  width: `${(c.contribution / max) * 100}%`,
                  minWidth: c.contribution > 0 ? 2 : 0,
                  background: "var(--series-1)",
                  borderRadius: "0 4px 4px 0",
                }}
              />
            </div>
            <span className="text-right tabular-nums" style={{ color: "var(--text-secondary)" }}>
              +{c.contribution.toFixed(1)}
            </span>
            {hover === k && (
              <div className="absolute left-40 top-6 z-10 max-w-md rounded border border-slate-200 bg-white p-2 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-900">
                <div className="font-semibold">{COMPONENT_NAMES[k] ?? k}</div>
                <div>
                  value {c.value.toFixed(2)} × weight {c.weight} → +{c.contribution.toFixed(1)} pts
                </div>
                <div className="mt-1">{c.evidence}</div>
                <div className="mt-1 italic">{c.kind === "measured" ? "Measured from source data / our snapshots" : c.kind === "inferred" ? "Inferred by deterministic rules" : "No signal"}</div>
              </div>
            )}
          </div>
        ))}
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-slate-500">Table view</summary>
        <table className="mt-1 w-full text-left">
          <thead>
            <tr className="text-slate-500">
              <th>Component</th><th>Kind</th><th>Value</th><th>Weight</th><th>Points</th><th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([k, c]) => (
              <tr key={k} className="border-t border-slate-200 dark:border-slate-800">
                <td>{COMPONENT_NAMES[k] ?? k}</td><td>{c.kind}</td><td>{c.value.toFixed(2)}</td><td>{c.weight}</td>
                <td>{c.contribution.toFixed(1)}</td><td>{c.evidence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

/** One metric over time (one chart per metric; never a dual axis). */
export function MetricLine({ title, points }: { title: string; points: [string, number][] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 320, H = 90, PL = 6, PR = 44, PT = 8, PB = 18;
  if (points.length === 0) return null;
  const xs = points.map(([t]) => new Date(t).getTime());
  const ys = points.map(([, v]) => v);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const pad = yMax === yMin ? Math.max(1, Math.abs(yMax) * 0.05) : 0;
  const lo = yMin - pad, hi = yMax + pad;
  const sx = (t: number) => PL + (x1 === x0 ? (W - PL - PR) / 2 : ((t - x0) / (x1 - x0)) * (W - PL - PR));
  const sy = (v: number) => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
  const d = points.map(([, v], i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(v).toFixed(1)}`).join(" ");
  const last = points.length - 1;
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * W;
    let best = 0;
    xs.forEach((t, i) => {
      if (Math.abs(sx(t) - mx) < Math.abs(sx(xs[best]) - mx)) best = i;
    });
    setHover(best);
  };
  const hv = hover ?? last;
  const short = x1 - x0 < 2 * 86400000;
  const tick = (t: number) => (short ? new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : new Date(t).toLocaleDateString());
  return (
    <figure className="viz-root rounded border border-slate-200 p-2 dark:border-slate-800" style={{ background: "var(--surface-1)" }}>
      <figcaption className="mb-1 flex justify-between text-xs" style={{ color: "var(--text-secondary)" }}>
        <span style={{ color: "var(--text-primary)" }}>{title}</span>
        <span>{points.length} snapshot{points.length === 1 ? "" : "s"}</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${title}: ${compactNumber(ys[0])} to ${compactNumber(ys[last])}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <line x1={PL} x2={W - PR} y1={H - PB} y2={H - PB} stroke="var(--grid)" strokeWidth={1} />
        <path d={d} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null && <line x1={sx(xs[hv])} x2={sx(xs[hv])} y1={PT} y2={H - PB} stroke="var(--grid)" strokeWidth={1} />}
        <circle cx={sx(xs[hv])} cy={sy(ys[hv])} r={4} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
        <text x={W - PR + 6} y={sy(ys[last]) + 4} fontSize={11} fill="var(--text-primary)">
          {compactNumber(ys[last])}
        </text>
        <text x={PL} y={H - 4} fontSize={10} fill="var(--text-secondary)">
          {tick(x0)}
        </text>
        <text x={W - PR} y={H - 4} fontSize={10} fill="var(--text-secondary)" textAnchor="end">
          {tick(x1)}
        </text>
      </svg>
      <div className="h-4 text-xs" style={{ color: "var(--text-secondary)" }}>
        {hover !== null && `${absoluteTime(points[hv][0])}: ${compactNumber(ys[hv])}`}
      </div>
      <details className="text-xs">
        <summary className="cursor-pointer text-slate-500">Table view</summary>
        <table className="mt-1 w-full">
          <tbody>
            {points.map(([t, v]) => (
              <tr key={t}>
                <td>{absoluteTime(t)}</td>
                <td className="text-right tabular-nums">{v.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
