import { useState } from "react";
import { api, useApi } from "../lib/api";
import { absoluteTime, duration, relativeTime } from "../lib/format";
import type { Source } from "../lib/types";
import { Button, Card, ErrorBox, ExternalLink, HealthBadge, KV } from "../components/ui";

interface SourceDetail extends Source {
  runs: { id: number; started_at: string; status: string; records: number; new: number; updated: number; merged: number; filtered: number; http_requests: number; error: string }[];
  checkpoints: Record<string, unknown>;
  raw_records: number;
}

function Detail({ id }: { id: number }) {
  const { data } = useApi<SourceDetail>(`/api/sources/${id}`);
  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;
  return (
    <div className="grid gap-4 text-sm md:grid-cols-2">
      <dl className="flex flex-col gap-1">
        <KV k="Access" v={`${data.access_method} — auth: ${data.auth_method}`} />
        <KV k="Endpoint" v={<code className="break-all text-xs">{data.endpoint}</code>} />
        <KV k="Allowed hosts" v={data.allowed_hosts.join(", ")} />
        <KV k="Cost" v={data.cost_status} />
        <KV k="Quota / limits" v={data.quota_notes} />
        <KV k="Terms" v={<>{data.terms_status} (checked {absoluteTime(data.terms_checked_at)}) <ExternalLink href={data.terms_url}>terms ↗</ExternalLink></>} />
        <KV k="Storage / display" v={data.storage_notes} />
        <KV k="Attribution" v={data.attribution} />
        <KV k="Interval" v={`${duration(data.interval_s)} (minimum ${duration(data.min_interval_s)})`} />
        <KV k="Raw records" v={data.raw_records.toLocaleString()} />
        <KV k="Checkpoints" v={<code className="text-xs">{JSON.stringify(data.checkpoints)}</code>} />
      </dl>
      <div>
        <div className="mb-1 font-medium">Recent runs</div>
        <table className="w-full text-xs">
          <thead className="text-slate-500"><tr><th className="text-left">When</th><th>Status</th><th>Rec</th><th>New</th><th>Upd</th><th>Merged</th><th>Filtered</th><th>HTTP</th></tr></thead>
          <tbody>
            {data.runs.slice(0, 15).map((r) => (
              <tr key={r.id} className="border-t border-slate-200 dark:border-slate-800" title={r.error}>
                <td>{relativeTime(r.started_at)}</td><td className="text-center">{r.status}</td><td className="text-center">{r.records}</td>
                <td className="text-center">{r.new}</td><td className="text-center">{r.updated}</td><td className="text-center">{r.merged}</td>
                <td className="text-center">{r.filtered}</td><td className="text-center">{r.http_requests}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.last_error && <p className="mt-2 break-words text-xs text-rose-700 dark:text-rose-300">Last error: {data.last_error}</p>}
      </div>
    </div>
  );
}

export function Sources() {
  const { data, error, reload } = useApi<Source[]>("/api/sources");
  const [open, setOpen] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const act = async (s: Source, action: string) => {
    setErr(null);
    try {
      let json: unknown = {};
      if (action === "enable" && s.terms_status !== "verified") {
        const ok = window.confirm(
          `${s.name}: terms are NOT verified.\n\n${s.disabled_reason}\n\nTerms: ${s.terms_url || "(none listed)"}\n\nConfirm you reviewed the publisher's terms and accept feed-reader use (title, excerpt, link back)?`,
        );
        if (!ok) return;
        json = { acknowledge_terms: true };
      }
      await api(`/api/sources/${s.id}/${action}`, { method: "POST", json });
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div>
      <h1 className="mb-1 text-xl font-bold">Source registry</h1>
      <p className="mb-3 text-sm text-slate-500">
        Every source is an independent connector with documented access method, limits, terms and attribution. Sources whose
        terms were not verified ship disabled. Only free sources can be enabled (zero-cost mode).
      </p>
      <ErrorBox error={error || err} />
      <div className="flex flex-col gap-2">
        {(data ?? []).map((s) => (
          <Card key={s.id} className="!p-3">
            <div className="flex flex-wrap items-center gap-3">
              <button className="text-left font-medium hover:underline" onClick={() => setOpen(open === s.id ? null : s.id)}>
                {open === s.id ? "▾" : "▸"} {s.name}
              </button>
              <span className="text-xs text-slate-500">{s.connector_type} · {s.source_class}</span>
              {s.enabled ? (s.paused ? <span className="text-xs font-medium">⏸ paused</span> : <HealthBadge health={s.health} />) : <span className="text-xs text-slate-500">disabled</span>}
              <span className={`text-xs ${s.terms_status === "verified" ? "text-slate-600 dark:text-slate-400" : "text-amber-700 dark:text-amber-300"}`}>
                terms {s.terms_status}
              </span>
              <span className="text-xs text-slate-500">
                {s.stats.items ?? 0} items · {s.stats.requests_24h ?? 0} req/24h{s.stats.request_errors_24h ? ` (${s.stats.request_errors_24h} errors)` : ""}
              </span>
              <span className="text-xs text-slate-500">
                last success {relativeTime(s.last_success_at)} · next {s.enabled ? relativeTime(s.next_run_at) : "—"} · every {duration(s.interval_s)}
              </span>
              <div className="ml-auto flex gap-1">
                {s.enabled ? (
                  <>
                    <Button onClick={() => act(s, "run-now")}>Run now</Button>
                    {s.paused ? <Button onClick={() => act(s, "resume")}>Resume</Button> : <Button onClick={() => act(s, "pause")}>Pause</Button>}
                    <Button variant="danger" onClick={() => act(s, "disable")}>Disable</Button>
                  </>
                ) : (
                  <Button onClick={() => act(s, "enable")}>Enable…</Button>
                )}
              </div>
            </div>
            {!s.enabled && s.disabled_reason && <p className="mt-1 text-xs text-slate-500">{s.disabled_reason}</p>}
            {s.enabled && s.last_error && <p className="mt-1 truncate text-xs text-rose-700 dark:text-rose-300" title={s.last_error}>{s.last_error}</p>}
            {open === s.id && <div className="mt-3 border-t border-slate-200 pt-3 dark:border-slate-800"><Detail id={s.id} /></div>}
          </Card>
        ))}
      </div>
    </div>
  );
}
