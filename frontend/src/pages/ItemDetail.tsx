import { useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { api, useApi } from "../lib/api";
import { absoluteTime, compactNumber, metricLabel, relativeTime } from "../lib/format";
import type { ItemDetail as Detail, User } from "../lib/types";
import { MetricLine, ScoreBreakdown } from "../components/charts";
import { ItemList } from "../components/ItemCard";
import { JsonViewer } from "../components/JsonViewer";
import { Button, Card, ErrorBox, ExternalLink, KV, LabelBadge, SectionTitle, VerificationBadge } from "../components/ui";

function NotStated() {
  return <span className="italic text-slate-500">not stated by source</span>;
}

export function ItemDetail({ user }: { user: User }) {
  const { id } = useParams();
  const { data: it, error, reload } = useApi<Detail>(`/api/items/${id}`);
  const [raw, setRaw] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!it) return <p className="text-slate-500">Loading…</p>;
  if (it.redirect_to) return <Navigate to={`/items/${it.redirect_to}`} replace />;
  const ai = it.ai_summary_full;
  const isAdmin = ["admin", "owner"].includes(user.role);
  const queueSummary = async () => {
    const r = await api<{ queued: boolean }>(`/api/ai/items/${it.id}/summarize`, { method: "POST" });
    setMsg(r.queued ? "Queued for the local model; the worker will process it (refresh in a minute)." : "Already queued.");
  };
  const showRaw = async (rawId: number) => setRaw(await api(`/api/items/${it.id}/raw/${rawId}`));
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <div className="flex flex-wrap items-center gap-2">
          <LabelBadge label={it.label} evidence={it.label_evidence} />
          <VerificationBadge level={it.verification} />
          <span className="text-sm text-slate-500">{it.kind}</span>
          {it.is_fixture && <span className="rounded bg-yellow-200 px-1 text-xs text-yellow-900">TEST FIXTURE — not live data</span>}
        </div>
        <h1 className="mt-2 text-2xl font-bold leading-tight">{it.title}</h1>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
          <ExternalLink href={it.url}>Open original ↗</ExternalLink>
          {Object.entries(it.links).filter(([k]) => k !== "original").map(([k, v]) => (
            <ExternalLink key={k} href={v}>{k.replace("discussion_", "discussion: ").replace("_", " ")} ↗</ExternalLink>
          ))}
        </div>
        <dl className="mt-3 flex flex-col gap-1">
          <KV k="Published (per source)" v={`${absoluteTime(it.published_at)} (${relativeTime(it.published_at)})`} />
          <KV k="Discovered by hub" v={`${absoluteTime(it.discovered_at)} (${relativeTime(it.discovered_at)})`} />
          <KV k="Author / publisher" v={it.author || <NotStated />} />
          {it.organization && it.organization !== it.author && <KV k="Organization" v={it.organization} />}
          <KV k="License" v={it.license ? `${it.license} (as stated by source)` : <NotStated />} />
          <KV k="Label rule" v={it.label_evidence} />
          {it.tags.length > 0 && <KV k="Tags (source)" v={it.tags.join(", ")} />}
        </dl>

        <SectionTitle>Source text</SectionTitle>
        <Card>
          {it.summary ? <p className="whitespace-pre-line text-sm leading-relaxed">{it.summary}</p> : <p className="text-sm italic text-slate-500">The source provided no description.</p>}
          <p className="mt-2 text-xs text-slate-500">Excerpt as provided by the source. Full content is at the original link.</p>
        </Card>

        <SectionTitle right={isAdmin && <Button onClick={queueSummary}>{ai?.status === "ok" ? "Regenerate" : "Summarize locally"}</Button>}>
          AI explanation
        </SectionTitle>
        {msg && <p className="mb-2 text-sm text-slate-600">{msg}</p>}
        <Card>
          {ai?.status === "ok" ? (
            <div className="text-sm">
              <p className="mb-2 rounded bg-slate-100 px-2 py-1 text-xs dark:bg-slate-800">
                ⓘ {ai.notice} Model: <b>{ai.model}</b> ({ai.provider}, local) · {relativeTime(ai.generated_at)}
                {ai.cache_hit ? " · from cache" : ""}
              </p>
              {ai.what_it_is && <p className="font-medium">{ai.what_it_is}</p>}
              <p className="mt-1">{ai.summary}</p>
              {ai.why_it_matters && <p className="mt-1"><span className="text-slate-500">Why it matters: </span>{ai.why_it_matters}</p>}
              {ai.key_points && ai.key_points.length > 0 && (
                <ul className="mt-2 list-disc pl-5">{ai.key_points.map((p) => <li key={p}>{p}</li>)}</ul>
              )}
              {ai.category_suggestions && ai.category_suggestions.length > 0 && (
                <p className="mt-2 text-xs text-slate-500">AI-suggested categories (not used for filtering): {ai.category_suggestions.join(", ")}</p>
              )}
              {ai.grounding && ai.grounding.removed.length > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-slate-500">{ai.grounding.removed.length} statement(s) removed by the grounding check</summary>
                  <ul className="mt-1 list-disc pl-5">
                    {ai.grounding.removed.map((r, i) => <li key={i}><i>{r.sentence}</i> — {r.reasons.join("; ")}</li>)}
                  </ul>
                </details>
              )}
            </div>
          ) : ai?.status === "insufficient_source_text" ? (
            <p className="text-sm text-slate-500">Not summarized: the source provides too little text to summarize without guessing.</p>
          ) : (
            <p className="text-sm text-slate-500">No AI explanation yet. Summaries are generated by the local model (Ollama) in the background when available; everything else works without it.</p>
          )}
        </Card>

        {it.opportunity && (
          <>
            <SectionTitle>Opportunity details (source-stated only)</SectionTitle>
            <Card>
              <dl className="flex flex-col gap-1">
                <KV k="Type" v={it.opportunity.type} />
                <KV k="Organization" v={it.opportunity.organization ?? <NotStated />} />
                <KV k="Remote" v={it.opportunity.remote === "unknown" ? <NotStated /> : it.opportunity.remote} />
                <KV k="Compensation" v={it.opportunity.compensation ?? <NotStated />} />
                <KV k="Deadline" v={it.opportunity.deadline ? absoluteTime(it.opportunity.deadline) : <NotStated />} />
                <KV k="Status" v={it.opportunity.status} />
                <KV k="Apply" v={<ExternalLink href={it.opportunity.application_url}>application link ↗</ExternalLink>} />
                <KV k="Last checked" v={absoluteTime(it.opportunity.last_checked_at)} />
              </dl>
            </Card>
          </>
        )}

        <SectionTitle>Provenance ({it.provenance.length} source{it.provenance.length === 1 ? "" : "s"})</SectionTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-slate-500">
              <tr><th>Source</th><th>Class</th><th>Seen</th><th>As reported</th><th>Raw</th></tr>
            </thead>
            <tbody>
              {it.provenance.map((p) => (
                <tr key={p.source_key + p.external_id} className="border-t border-slate-200 align-top dark:border-slate-800">
                  <td className="py-1.5">
                    <ExternalLink href={p.source_url}>{p.source_name}</ExternalLink>
                    <div className="text-xs text-slate-500">{p.attribution}</div>
                  </td>
                  <td>{p.source_class}</td>
                  <td className="text-xs">first {relativeTime(p.first_seen_at)}<br />last {relativeTime(p.last_seen_at)}</td>
                  <td className="text-xs">
                    <div className="line-clamp-2">{p.title_at_source}</div>
                    {Object.entries(p.metrics).map(([k, v]) => <span key={k} className="mr-2">{compactNumber(v)} {metricLabel(k)}</span>)}
                  </td>
                  <td>{p.raw_record_id && <Button variant="ghost" onClick={() => showRaw(p.raw_record_id!)}>view ({p.raw_versions} v)</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {raw !== null && (
          <div className="mt-3">
            <div className="mb-1 flex items-center justify-between text-sm">
              <span className="font-medium">Raw record (untrusted, exactly as received)</span>
              <Button variant="ghost" onClick={() => setRaw(null)}>close</Button>
            </div>
            <JsonViewer value={raw} />
          </div>
        )}

        {it.release_events.length > 0 && (
          <>
            <SectionTitle>Release history</SectionTitle>
            <ul className="text-sm">
              {it.release_events.map((e, i) => (
                <li key={i} className="border-l-2 border-slate-300 py-1 pl-3 dark:border-slate-700">
                  <b>{e.event_type}</b> {e.version} — {absoluteTime(e.event_date)} · {e.confidence} · <ExternalLink href={e.source_url}>source</ExternalLink>
                </li>
              ))}
            </ul>
          </>
        )}

        {it.related.length > 0 && (
          <>
            <SectionTitle>Related</SectionTitle>
            <ItemList items={it.related} />
          </>
        )}
      </div>

      <aside>
        <SectionTitle>Why it ranks: {it.score.toFixed(1)} / 100</SectionTitle>
        <Card>
          <ScoreBreakdown components={it.score_components} />
          <p className="mt-2 text-xs text-slate-500">
            “measured” = numbers and timestamps reported by sources or our own snapshots; “inferred” = deterministic
            rules. Weights are configurable in Settings. Scored {relativeTime(it.scored_at)}.
          </p>
        </Card>

        {Object.keys(it.metric_history).length > 0 && (
          <>
            <SectionTitle>Signals over time</SectionTitle>
            <div className="flex flex-col gap-2">
              {Object.entries(it.metric_history).map(([m, pts]) => <MetricLine key={m} title={metricLabel(m)} points={pts} />)}
            </div>
          </>
        )}

        <SectionTitle>Categories</SectionTitle>
        <ul className="text-sm">
          {it.categories_detail.map((c) => (
            <li key={c.slug} className="mb-1">
              <Link className="font-medium hover:underline" to={`/feed?category=${c.slug}`}>{c.name}</Link>{" "}
              <span className="text-xs text-slate-500">({c.method}, {Math.round(c.confidence * 100)}%) — {c.evidence}</span>
            </li>
          ))}
        </ul>

        <SectionTitle>Identity</SectionTitle>
        <ul className="break-all text-xs text-slate-600 dark:text-slate-400">
          {it.identity_keys.map((k) => <li key={k}>{k}</li>)}
        </ul>
        {it.dedupe_candidates.length > 0 && (
          <p className="mt-2 text-xs">
            Possible duplicates:{" "}
            {it.dedupe_candidates.map((d) => (
              <Link key={d.id} to={`/items/${d.other_id}`} className="mr-2 underline">#{d.other_id} ({d.status}, {Math.round(d.similarity * 100)}%)</Link>
            ))}
          </p>
        )}
        <div className="mt-3"><Button variant="ghost" onClick={reload}>refresh</Button></div>
      </aside>
    </div>
  );
}
