// Settings, watchlists, alerts, reports, opportunities, timeline and stack explorer.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, qs, serverSanitizedHtml, useApi } from "../lib/api";
import { absoluteTime, relativeTime } from "../lib/format";
import type { ItemCard, User } from "../lib/types";
import { ItemList } from "../components/ItemCard";
import { Button, Card, Empty, ErrorBox, ExternalLink, SectionTitle } from "../components/ui";

// ----------------------------------------------------------------------------- Settings

interface SettingsData {
  editable: { ai_enabled: boolean; embeddings_enabled: boolean; ai_backfill_batch: number; low_resource_mode: boolean; reports_enabled: boolean; report_hour_utc: number; score_weights: Record<string, number> };
  invariants: Record<string, string | boolean>;
}
const WEIGHT_KEYS = ["recency", "credibility", "relevance", "novelty", "adoption", "utility", "corroboration"];
const DEFAULT_W: Record<string, number> = { recency: 0.22, credibility: 0.15, relevance: 0.15, novelty: 0.12, adoption: 0.18, utility: 0.08, corroboration: 0.1 };

function Toggle({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" disabled={disabled} checked={checked} onChange={(ev) => onChange(ev.target.checked)} /> {label}
    </label>
  );
}

export function Settings({ user }: { user: User }) {
  const { data, error, setData } = useApi<SettingsData>("/api/settings");
  const { data: ai } = useApi<{ policy: Record<string, unknown> }>("/api/ai/providers");
  const [err, setErr] = useState<string | null>(null);
  const owner = user.role === "owner";
  if (!data) return <ErrorBox error={error} />;
  const save = async (patch: Partial<SettingsData["editable"]>) => {
    setErr(null);
    try {
      setData(await api<SettingsData>("/api/settings", { method: "PATCH", json: patch }));
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const e = data.editable;
  const weights = { ...DEFAULT_W, ...e.score_weights };
  const toggle = (k: "ai_enabled" | "embeddings_enabled" | "low_resource_mode" | "reports_enabled", label: string) => (
    <Toggle label={label} checked={e[k]} disabled={!owner} onChange={(v) => save({ [k]: v })} />
  );
  return (
    <div className="max-w-3xl">
      <h1 className="mb-3 text-xl font-bold">Settings</h1>
      <ErrorBox error={err} />
      {!owner && <p className="mb-2 text-sm text-slate-500">Only the owner can change settings.</p>}
      <SectionTitle>Local AI & resources</SectionTitle>
      <Card>
        <div className="flex flex-col gap-2">
          {toggle("ai_enabled", "Generate source-grounded summaries with the local model (Ollama)")}
          {toggle("embeddings_enabled", "Compute local embeddings for semantic search")}
          {toggle("low_resource_mode", "Low-resource mode (slower AI backfill, no embeddings, fewer worker threads)")}
          <label className="flex items-center gap-2 text-sm">
            Summaries per backfill run (every 10 min)
            <input type="number" min={0} max={50} disabled={!owner} value={e.ai_backfill_batch} onChange={(ev) => save({ ai_backfill_batch: Number(ev.target.value) })} className="w-16 rounded border border-slate-300 px-1 dark:border-slate-700 dark:bg-slate-900" />
          </label>
        </div>
      </Card>
      <SectionTitle>Reports</SectionTitle>
      <Card>
        {toggle("reports_enabled", "Generate daily (and Monday weekly) reports automatically")}
        <label className="mt-2 flex items-center gap-2 text-sm">
          Hour (UTC)
          <input type="number" min={0} max={23} disabled={!owner} value={e.report_hour_utc} onChange={(ev) => save({ report_hour_utc: Number(ev.target.value) })} className="w-16 rounded border border-slate-300 px-1 dark:border-slate-700 dark:bg-slate-900" />
        </label>
      </Card>
      <SectionTitle>Ranking weights</SectionTitle>
      <Card>
        {WEIGHT_KEYS.map((k) => (
          <label key={k} className="grid grid-cols-[10rem_1fr_3rem] items-center gap-2 text-sm">
            {k}
            <input type="range" min={0} max={0.5} step={0.01} disabled={!owner} value={weights[k]} onChange={(ev) => save({ score_weights: { ...e.score_weights, [k]: Number(ev.target.value) } })} />
            <span className="tabular-nums">{weights[k].toFixed(2)}</span>
          </label>
        ))}
        <p className="mt-2 text-xs text-slate-500">Applied at the next rescore (every 15 min). Popularity alone never dominates: adoption uses growth, not totals.</p>
        {owner && <Button onClick={() => save({ score_weights: {} })}>Reset to defaults</Button>}
      </Card>
      <SectionTitle>Invariants (not changeable here)</SectionTitle>
      <Card>
        <ul className="text-sm">
          {Object.entries(data.invariants).map(([k, v]) => <li key={k}><code>{k}</code>: <b>{String(v)}</b></li>)}
          {ai && <li className="mt-2 text-slate-500">{String(ai.policy.note)}</li>}
        </ul>
      </Card>
    </div>
  );
}

// ----------------------------------------------------------------------------- Watchlists & alerts

interface Watchlist { id: number; name: string; alerts_enabled: boolean; min_score: number; rules: { rule_type: string; value: string }[] }
const RULE_TYPES = ["keyword", "category", "source", "organization", "author", "label", "kind", "identity"];

export function Watchlists() {
  const { data, reload } = useApi<Watchlist[]>("/api/watchlists");
  const [name, setName] = useState("");
  const [rules, setRules] = useState<{ rule_type: string; value: string }[]>([{ rule_type: "keyword", value: "" }]);
  const [preview, setPreview] = useState<{ id: number; items: ItemCard[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    setErr(null);
    try {
      await api("/api/watchlists", { method: "POST", json: { name, rules: rules.filter((r) => r.value.trim()) } });
      setName("");
      setRules([{ rule_type: "keyword", value: "" }]);
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="max-w-4xl">
      <h1 className="mb-3 text-xl font-bold">Watchlists</h1>
      <p className="mb-3 text-sm text-slate-500">Follow models, projects, organizations, categories, keywords or release events. New matching discoveries create one in-app alert each (never duplicated).</p>
      <Card>
        <div className="flex flex-col gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Watchlist name" className="rounded border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-900" />
          {rules.map((r, i) => (
            <div key={i} className="flex gap-2">
              <select value={r.rule_type} onChange={(e) => setRules(rules.map((x, j) => (j === i ? { ...x, rule_type: e.target.value } : x)))} className="rounded border border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900">
                {RULE_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
              <input value={r.value} onChange={(e) => setRules(rules.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} placeholder={r.rule_type === "identity" ? "e.g. github:ollama/ollama" : r.rule_type === "category" ? "e.g. mcp" : "value"} className="flex-1 rounded border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-900" />
            </div>
          ))}
          <div className="flex gap-2">
            <Button onClick={() => setRules([...rules, { rule_type: "keyword", value: "" }])}>+ rule</Button>
            <Button variant="primary" disabled={!name.trim()} onClick={create}>Create watchlist</Button>
          </div>
          <ErrorBox error={err} />
        </div>
      </Card>
      {(data ?? []).map((w) => (
        <Card key={w.id} className="mt-2">
          <div className="flex items-center gap-2">
            <b>{w.name}</b>
            <span className="text-xs text-slate-500">{w.rules.map((r) => `${r.rule_type}: ${r.value}`).join(" OR ")}</span>
            <div className="ml-auto flex gap-1">
              <Button onClick={async () => setPreview({ id: w.id, items: (await api<{ items: ItemCard[] }>(`/api/watchlists/${w.id}/matches`)).items })}>Preview matches</Button>
              <Button variant="danger" onClick={async () => { await api(`/api/watchlists/${w.id}`, { method: "DELETE" }); reload(); }}>Delete</Button>
            </div>
          </div>
          {preview?.id === w.id && <div className="mt-2">{preview.items.length ? <ItemList items={preview.items} /> : <Empty>No matches in the last 7 days.</Empty>}</div>}
        </Card>
      ))}
    </div>
  );
}

export function Alerts() {
  const { data, reload } = useApi<{ unread: number; alerts: { id: number; reason: string; created_at: string; read: boolean; item: ItemCard | null }[] }>("/api/alerts");
  return (
    <div className="max-w-4xl">
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-xl font-bold">Alerts</h1>
        <span className="text-sm text-slate-500">{data?.unread ?? 0} unread</span>
        <Button onClick={async () => { await api("/api/alerts/read", { method: "POST", json: {} }); reload(); }}>Mark all read</Button>
      </div>
      {data && data.alerts.length === 0 && <Empty>No alerts yet. Create a watchlist to get in-app alerts.</Empty>}
      <div className="flex flex-col gap-2">
        {(data?.alerts ?? []).map((a) => (
          <div key={a.id} className={a.read ? "opacity-70" : ""}>
            <div className="text-xs text-slate-500">{relativeTime(a.created_at)} · {a.reason}</div>
            {a.item && <ItemList items={[a.item]} />}
          </div>
        ))}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------- Reports

export function Reports({ user }: { user: User }) {
  const { data, reload } = useApi<{ id: number; period: string; period_start: string; items: number; generator: string }[]>("/api/reports");
  const [sel, setSel] = useState<number | null>(null);
  const { data: rep } = useApi<{ content_html: string; period: string; period_start: string; generator: string }>(sel ? `/api/reports/${sel}` : null);
  const canGen = ["admin", "owner"].includes(user.role);
  const gen = async (period: string) => {
    const r = await api<{ id: number }>("/api/reports/generate", { method: "POST", json: { period } });
    reload();
    setSel(r.id);
  };
  return (
    <div className="grid gap-6 lg:grid-cols-4">
      <div>
        <h1 className="mb-3 text-xl font-bold">Reports</h1>
        {canGen && (
          <div className="mb-3 flex gap-2">
            <Button onClick={() => gen("daily")}>Generate today</Button>
            <Button onClick={() => gen("weekly")}>Last 7 days</Button>
          </div>
        )}
        <ul className="text-sm">
          {(data ?? []).map((r) => (
            <li key={r.id}>
              <button className={`w-full rounded px-2 py-1 text-left hover:bg-slate-100 dark:hover:bg-slate-800 ${sel === r.id ? "bg-slate-100 dark:bg-slate-800" : ""}`} onClick={() => setSel(r.id)}>
                {r.period} · {r.period_start.slice(0, 10)} <span className="text-xs text-slate-500">({r.items} items)</span>
              </button>
            </li>
          ))}
        </ul>
        {data?.length === 0 && <p className="text-sm text-slate-500">No reports yet; they are generated daily by the worker.</p>}
      </div>
      <div className="lg:col-span-3">
        {rep ? (
          <Card>
            <p className="mb-2 text-xs text-slate-500">Generator: {rep.generator} (deterministic, local; no AI claims added). Links go to original sources.</p>
            <div className="prose-report text-sm" dangerouslySetInnerHTML={serverSanitizedHtml(rep.content_html)} />
          </Card>
        ) : (
          <Empty>Select a report.</Empty>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------- Opportunities

interface Opp { item: ItemCard; type: string; organization: string | null; remote: string; compensation: string | null; deadline: string | null; status: string; application_url: string | null; last_checked_at: string | null }

export function Opportunities() {
  const [status, setStatus] = useState("");
  const { data } = useApi<Opp[]>(`/api/opportunities${qs({ status })}`);
  const ns = <span className="italic text-slate-500">not stated</span>;
  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-xl font-bold">Opportunities</h1>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border border-slate-300 bg-white px-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          <option value="">any status</option><option value="open">open</option><option value="expired">expired</option><option value="unknown">unknown</option>
        </select>
      </div>
      <p className="mb-3 text-sm text-slate-500">Only details stated by the source are shown; compensation, deadlines and eligibility are never inferred. Expired deadlines are marked automatically.</p>
      {data && data.length === 0 && <Empty>No opportunities discovered yet (AI roles, evaluation/annotation work, internships, grants, hackathons, beta programs).</Empty>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-slate-500"><tr><th>Role / listing</th><th>Type</th><th>Organization</th><th>Remote</th><th>Compensation</th><th>Deadline</th><th>Status</th><th>Discovered</th></tr></thead>
          <tbody>
            {(data ?? []).map((o) => (
              <tr key={o.item.id} className="border-t border-slate-200 align-top dark:border-slate-800">
                <td className="py-1.5"><Link className="hover:underline" to={`/items/${o.item.id}`}>{o.item.title}</Link><br /><ExternalLink href={o.application_url}>apply / source ↗</ExternalLink></td>
                <td>{o.type}</td><td>{o.organization ?? ns}</td><td>{o.remote === "unknown" ? ns : o.remote}</td><td>{o.compensation ?? ns}</td>
                <td>{o.deadline ? absoluteTime(o.deadline) : ns}</td><td>{o.status}</td><td>{relativeTime(o.item.discovered_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------- Timeline

interface Ev { id: number; event_type: string; version: string; title: string; event_date: string | null; source_url: string | null; confidence: string; verification: string; item_id: number; item_title: string; kind: string }

export function Timeline() {
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const { data } = useApi<Ev[]>(`/api/timeline${qs({ event_type: type, q, days: 30 })}`);
  const byDay = new Map<string, Ev[]>();
  for (const e of data ?? []) {
    const d = (e.event_date ?? "unknown").slice(0, 10);
    byDay.set(d, [...(byDay.get(d) ?? []), e]);
  }
  return (
    <div className="max-w-4xl">
      <h1 className="mb-2 text-xl font-bold">Release timeline (30 days)</h1>
      <p className="mb-3 text-sm text-slate-500">Append-only history of papers, model publications, versions, previews, betas and deprecations — each with its source link. History is never overwritten.</p>
      <div className="mb-3 flex gap-2">
        <select value={type} onChange={(e) => setType(e.target.value)} className="rounded border border-slate-300 bg-white px-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          <option value="">all event types</option>
          {["paper", "version", "preview", "beta", "ga", "announcement", "deprecation", "shutdown"].map((t) => <option key={t}>{t}</option>)}
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter by title" className="rounded border border-slate-300 px-2 text-sm dark:border-slate-700 dark:bg-slate-900" />
      </div>
      {[...byDay.entries()].map(([day, evs]) => (
        <div key={day} className="mb-3">
          <div className="text-sm font-semibold">{day}</div>
          <ul className="ml-2 border-l-2 border-slate-300 pl-3 text-sm dark:border-slate-700">
            {evs.map((e) => (
              <li key={e.id} className="py-0.5">
                <span className="mr-1 rounded bg-slate-100 px-1 text-xs dark:bg-slate-800">{e.event_type}</span>
                <Link className="hover:underline" to={`/items/${e.item_id}`}>{e.item_title}</Link> {e.version && <span className="text-xs text-slate-500">{e.version}</span>}
                <span className="ml-1 text-xs text-slate-500">· {e.confidence} · {e.verification}</span> <ExternalLink href={e.source_url}>source</ExternalLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {data?.length === 0 && <Empty>No release events in this period yet.</Empty>}
    </div>
  );
}

// ----------------------------------------------------------------------------- Stack explorer

interface Stack { nodes: { id: number; title: string; kind: string; score: number; organization: string }[]; edges: { from: number; to: number; relation: string; confidence: string }[]; legend: Record<string, string> }

export function StackExplorer() {
  const { data } = useApi<Stack>("/api/stack");
  if (!data) return <p className="text-slate-500">Loading…</p>;
  const nodes = new Map(data.nodes.map((n) => [n.id, n]));
  const groups = new Map<string, Stack["edges"]>();
  for (const e of data.edges) groups.set(e.relation, [...(groups.get(e.relation) ?? []), e]);
  const REL: Record<string, string> = { release_of: "is a release of", implemented_in: "is implemented in", cites_paper: "cites / implements paper", built_on: "is built on (base model)" };
  return (
    <div className="max-w-5xl">
      <h1 className="mb-2 text-xl font-bold">AI Stack Explorer</h1>
      <p className="mb-3 text-sm text-slate-500">
        Relationships between models, papers, repositories and releases, built only from links the sources themselves state
        (model-card tags, paper code links, release ownership). No inferred edges are shown.
      </p>
      <ul className="mb-3 text-xs text-slate-500">{Object.entries(data.legend).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}</ul>
      {data.edges.length === 0 && <Empty>No source-stated relationships among collected items yet.</Empty>}
      {[...groups.entries()].map(([rel, edges]) => (
        <section key={rel}>
          <SectionTitle>{REL[rel] ?? rel} ({edges.length})</SectionTitle>
          <ul className="text-sm">
            {edges.map((e, i) => (
              <li key={i} className="py-0.5">
                <Link className="hover:underline" to={`/items/${e.from}`}>{nodes.get(e.from)?.title}</Link>
                <span className="mx-2 text-slate-500">→ {REL[rel] ?? rel} →</span>
                <Link className="hover:underline" to={`/items/${e.to}`}>{nodes.get(e.to)?.title}</Link>
                <span className="ml-2 text-xs text-slate-500">({e.confidence})</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
