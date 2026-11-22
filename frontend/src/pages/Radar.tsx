import { useEffect, useMemo, useRef, useState } from "react";
import { qs, useApi } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { ItemCard } from "../lib/types";
import { emptyFilters, Filters, type FilterState } from "../components/Filters";
import { ItemList } from "../components/ItemCard";
import { Empty, ErrorBox } from "../components/ui";

const WINDOWS: [string, string][] = [
  ["5m", "Last 5 min"],
  ["15m", "15 min"],
  ["30m", "30 min"],
  ["60m", "60 min"],
  ["today", "Today"],
];

interface RadarData {
  window: string;
  counts: Record<string, number>;
  items: ItemCard[];
  since: string;
  last_discovery_at: string | null;
}

export function Radar() {
  const [win, setWin] = useState("60m");
  const [basis, setBasis] = useState("discovered");
  const [filters, setFilters] = useState<FilterState>(emptyFilters);
  const tz = new Date().getTimezoneOffset();
  const path = `/api/radar${qs({ window: win, basis, tz_offset: tz, ...filters })}`;
  const { data, error, reload, setData } = useApi<RadarData>(path);
  const [live, setLive] = useState<"connecting" | "live" | "polling">("connecting");
  const [newIds, setNewIds] = useState<Set<number>>(new Set());
  const latest = useRef(0);

  // Live updates via Server-Sent Events; falls back to polling every 30 s if the stream drops.
  useEffect(() => {
    const es = new EventSource("/api/stream");
    es.addEventListener("hello", () => setLive("live"));
    es.addEventListener("item", (ev) => {
      const item = JSON.parse((ev as MessageEvent).data) as ItemCard;
      latest.current = Math.max(latest.current, item.id);
      setNewIds((s) => new Set(s).add(item.id));
      setData((d) => (d && !d.items.some((i) => i.id === item.id) ? { ...d, items: [item, ...d.items], counts: Object.fromEntries(Object.entries(d.counts).map(([k, v]) => [k, v + 1])) } : d));
    });
    es.onerror = () => setLive("polling");
    const poll = setInterval(() => {
      if (es.readyState !== EventSource.OPEN) reload();
    }, 30000);
    return () => {
      es.close();
      clearInterval(poll);
    };
  }, [reload, setData]);

  const items = useMemo(() => data?.items ?? [], [data]);
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">AI Radar</h1>
        <span className="text-sm" title="Updates stream in while this page is open; collection itself runs in the background worker">
          <span aria-hidden style={{ color: live === "live" ? "var(--status-good)" : "var(--status-warning)" }}>
            ●
          </span>{" "}
          {live === "live" ? "live" : live === "polling" ? "polling (stream reconnecting)" : "connecting…"}
        </span>
        <span className="text-sm text-slate-500">last discovery {relativeTime(data?.last_discovery_at)}</span>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-2" role="tablist">
        {WINDOWS.map(([k, label]) => (
          <button
            key={k}
            role="tab"
            aria-selected={win === k}
            onClick={() => setWin(k)}
            className={`rounded-full border px-3 py-1 text-sm ${win === k ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900" : "border-slate-300 dark:border-slate-700"}`}
          >
            {label} <span className="tabular-nums opacity-75">{data?.counts?.[k] ?? "…"}</span>
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1 text-sm">
          time basis
          <select value={basis} onChange={(e) => setBasis(e.target.value)} className="rounded border border-slate-300 bg-white px-1 py-0.5 dark:border-slate-700 dark:bg-slate-900">
            <option value="discovered">discovered by hub</option>
            <option value="published">published by source</option>
          </select>
        </label>
      </div>
      <Filters value={filters} onChange={setFilters} />
      <ErrorBox error={error} />
      <div className="mt-3">
        {items.length ? (
          <ItemList items={items} newIds={newIds} onChange={(it) => setData((d) => d && { ...d, items: d.items.map((x) => (x.id === it.id ? it : x)) })} />
        ) : (
          <Empty>
            No items {basis === "discovered" ? "discovered" : "published"} in this window. Sources poll every 5–60 minutes
            (respecting their limits), so short windows are often empty — try “Today”.
          </Empty>
        )}
      </div>
    </div>
  );
}
