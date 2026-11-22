import { useEffect } from "react";
import { api, useApi } from "../lib/api";
import { bytes, relativeTime } from "../lib/format";
import { Button, Card, ErrorBox, HealthBadge, SectionTitle, Stat } from "../components/ui";

interface Overview {
  workers: { worker_id: string; leader: boolean; alive: boolean; last_heartbeat_at: string; jobs_done: number; jobs_failed: number; current_job_id: number | null; stopped_at: string | null }[];
  scheduler_leader: string | null;
  queue: Record<string, Record<string, number>>;
  recent_failures: { id: number; type: string; status: string; attempts: number; last_error: string; run_after: string }[];
  http_24h: { host: string; n: number; retries: number; errors: number; rate_limited: number; blocked: number; not_modified: number; avg_ms: number }[];
  sources: Record<string, number>;
  duplicates_open: number;
  moderation_open: number;
  ai: { providers: { key: string; status: string; detail: string; model: string; embed_model: string; is_local: boolean; requests_today: number; avg_latency_ms: number | null; last_ok_at: string | null; last_error: string; circuit_open_until: string | null }[]; usage_24h: Record<string, number>; items_pending_summary: number; verified_free_cloud_providers: string[] };
  zero_cost: { mode: string; blocked_paid_requests_24h: number; telemetry: string; external_transfer: string };
  database: { size_bytes: number; tables: Record<string, number> };
  disk: { total: number; free: number };
  backups: { file: string; size: number; created_at: string }[];
  settings: { global_pause: boolean; paused_job_types: string[]; low_resource_mode: boolean };
}

interface Dupe { id: number; similarity: number; reason: string; a: { id: number; title: string; url: string }; b: { id: number; title: string; url: string } }
interface Audit { id: number; action: string; actor: string; target_type: string; target_id: string; created_at: string }

export function Ops() {
  const { data: o, error, reload } = useApi<Overview>("/api/ops/overview");
  const { data: dupes, reload: reloadDupes } = useApi<Dupe[]>("/api/ops/duplicates");
  const { data: auditLog } = useApi<Audit[]>("/api/ops/audit?limit=30");
  useEffect(() => {
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
  }, [reload]);
  if (error) return <ErrorBox error={error} />;
  if (!o) return <p className="text-slate-500">Loading…</p>;
  const post = async (path: string, json: unknown = {}) => {
    await api(path, { method: "POST", json });
    reload();
    reloadDupes();
  };
  const queueRows = Object.entries(o.queue);
  const totalQueued = queueRows.reduce((s, [, v]) => s + (v.queued ?? 0), 0);
  const aliveWorkers = o.workers.filter((w) => w.alive);
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Operations</h1>
        <Button variant={o.settings.global_pause ? "primary" : "danger"} onClick={() => post("/api/ops/pause", { paused: !o.settings.global_pause })}>
          {o.settings.global_pause ? "Resume all collection" : "Pause all collection"}
        </Button>
        <Button onClick={() => post("/api/ops/backup")}>Create backup</Button>
        <span className="text-xs text-slate-500">auto-refreshes every 15 s</span>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Workers alive" value={aliveWorkers.length} hint={o.scheduler_leader ? `scheduler leader: ${o.scheduler_leader}` : "no scheduler leader!"} />
        <Stat label="Jobs queued" value={totalQueued} />
        <Stat label="Paid requests blocked (24 h)" value={o.zero_cost.blocked_paid_requests_24h} hint={o.zero_cost.mode} />
        <Stat label="Database" value={bytes(o.database.size_bytes)} />
        <Stat label="Disk free" value={bytes(o.disk.free)} />
      </div>
      {aliveWorkers.length === 0 && (
        <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          ▲ No worker is running, so nothing is being collected. Start one with <code>hub worker</code> (or <code>hub start</code>).
        </p>
      )}

      <div className="grid gap-x-6 lg:grid-cols-2">
        <section>
          <SectionTitle>Zero-cost & privacy</SectionTitle>
          <Card>
            <ul className="text-sm">
              <li>Mode: <b>{o.zero_cost.mode}</b></li>
              <li>Verified free cloud AI providers: <b>{o.ai.verified_free_cloud_providers.length ? o.ai.verified_free_cloud_providers.join(", ") : "none (cloud AI blocked)"}</b></li>
              <li>Telemetry: {o.zero_cost.telemetry}</li>
              <li>External transfer: {o.zero_cost.external_transfer}</li>
            </ul>
          </Card>
          <SectionTitle>Workers & scheduler</SectionTitle>
          <div className="overflow-x-auto"><table className="w-full min-w-[28rem] text-sm">
            <tbody>
              {o.workers.slice(0, 6).map((w) => (
                <tr key={w.worker_id} className="border-t border-slate-200 dark:border-slate-800">
                  <td className="py-1"><HealthBadge health={w.alive ? "healthy" : "unknown"} /> <span className="text-xs">{w.worker_id}</span></td>
                  <td className="text-xs">{w.leader ? "scheduler leader" : ""}</td>
                  <td className="text-xs">heartbeat {relativeTime(w.last_heartbeat_at)}</td>
                  <td className="text-xs tabular-nums">{w.jobs_done} done / {w.jobs_failed} failed</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <SectionTitle>Queue by job type</SectionTitle>
          <div className="overflow-x-auto"><table className="w-full min-w-[28rem] text-sm">
            <thead className="text-xs text-slate-500"><tr><th className="text-left">Type</th><th>queued</th><th>running</th><th>succeeded</th><th>dead</th><th></th></tr></thead>
            <tbody>
              {queueRows.map(([t, c]) => (
                <tr key={t} className="border-t border-slate-200 text-center dark:border-slate-800">
                  <td className="text-left">{t}</td><td>{c.queued ?? 0}</td><td>{c.running ?? 0}</td><td>{c.succeeded ?? 0}</td><td>{c.dead ?? 0}</td>
                  <td>
                    <button className="text-xs underline" onClick={() => post("/api/ops/pause", { paused: !o.settings.paused_job_types.includes(t), job_type: t })}>
                      {o.settings.paused_job_types.includes(t) ? "resume" : "pause"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <SectionTitle>Recent job problems</SectionTitle>
          <ul className="text-xs">
            {o.recent_failures.slice(0, 10).map((f) => (
              <li key={f.id} className="mb-1 border-l-2 border-rose-300 pl-2">
                #{f.id} {f.type} — {f.status} (attempt {f.attempts}) {f.status === "dead" && <button className="underline" onClick={() => post(`/api/ops/jobs/${f.id}/retry`)}>retry</button>}
                <div className="truncate text-slate-500" title={f.last_error}>{f.last_error.split("\n")[0]}</div>
              </li>
            ))}
            {o.recent_failures.length === 0 && <li className="text-slate-500">none</li>}
          </ul>
        </section>
        <section>
          <SectionTitle>AI providers</SectionTitle>
          {o.ai.providers.map((p) => (
            <Card key={p.key} className="mb-2 !p-3 text-sm">
              <div className="flex items-center gap-2">
                <HealthBadge health={p.status} /> <b>{p.key}</b> {p.is_local ? "(local)" : "(cloud)"} · model {p.model || "—"} · embeddings {p.embed_model || "—"}
              </div>
              <div className="text-xs text-slate-500">
                {p.detail} · {p.requests_today} requests today · avg {p.avg_latency_ms ? `${(p.avg_latency_ms / 1000).toFixed(1)} s` : "—"} · last ok {relativeTime(p.last_ok_at)}
                {p.circuit_open_until && ` · circuit open until ${relativeTime(p.circuit_open_until)}`}
              </div>
              {p.last_error && <div className="truncate text-xs text-rose-700 dark:text-rose-300">{p.last_error}</div>}
            </Card>
          ))}
          <p className="text-xs text-slate-500">Items awaiting a summary: {o.ai.items_pending_summary} · usage 24 h: {Object.entries(o.ai.usage_24h).map(([k, v]) => `${k}=${v}`).join(", ") || "none"}</p>
          <SectionTitle>Outbound requests (24 h)</SectionTitle>
          <div className="overflow-x-auto"><table className="w-full min-w-[28rem] text-sm">
            <thead className="text-xs text-slate-500"><tr><th className="text-left">Host</th><th>req</th><th>304</th><th>retries</th><th>errors</th><th>rate-limited</th><th>blocked</th><th>avg ms</th></tr></thead>
            <tbody>
              {o.http_24h.map((h) => (
                <tr key={h.host} className="border-t border-slate-200 text-center tabular-nums dark:border-slate-800">
                  <td className="text-left">{h.host}</td><td>{h.n}</td><td>{h.not_modified}</td><td>{h.retries}</td><td>{h.errors}</td><td>{h.rate_limited}</td><td>{h.blocked}</td><td>{h.avg_ms}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <SectionTitle>Duplicate review ({o.duplicates_open})</SectionTitle>
          {(dupes ?? []).slice(0, 10).map((d) => (
            <Card key={d.id} className="mb-2 !p-2 text-xs">
              <div>{Math.round(d.similarity * 100)}% — {d.reason}</div>
              <div>A: <a className="underline" href={`/items/${d.a.id}`}>{d.a.title}</a></div>
              <div>B: <a className="underline" href={`/items/${d.b.id}`}>{d.b.title}</a></div>
              <div className="mt-1 flex gap-1">
                <Button onClick={() => post(`/api/ops/duplicates/${d.id}`, { action: "merge", keep_id: d.a.id })}>Merge into A</Button>
                <Button onClick={() => post(`/api/ops/duplicates/${d.id}`, { action: "dismiss" })}>Not duplicates</Button>
              </div>
            </Card>
          ))}
          {dupes?.length === 0 && <p className="text-xs text-slate-500">No open candidates. Duplicates sharing an identity key are merged automatically; similar titles are listed here and never merged without review.</p>}
          <SectionTitle>Data & backups</SectionTitle>
          <p className="text-xs">{Object.entries(o.database.tables).map(([t, n]) => `${t}: ${n.toLocaleString()}`).join(" · ")}</p>
          <ul className="mt-1 text-xs">{o.backups.slice(0, 5).map((b) => <li key={b.file}>{b.file} — {bytes(b.size)} — {relativeTime(b.created_at)}</li>)}</ul>
          {o.backups.length === 0 && <p className="text-xs text-slate-500">No backups yet.</p>}
          <SectionTitle>Audit log</SectionTitle>
          <ul className="text-xs">{(auditLog ?? []).map((a) => <li key={a.id}>{relativeTime(a.created_at)} · {a.actor} · {a.action} {a.target_type}:{a.target_id}</li>)}</ul>
        </section>
      </div>
    </div>
  );
}
