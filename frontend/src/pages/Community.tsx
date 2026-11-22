// Community publishing (private-first). All HTML shown here was rendered + sanitized by the server.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, serverSanitizedHtml, useApi } from "../lib/api";
import { relativeTime } from "../lib/format";
import { Button, Card, Empty, ErrorBox, SectionTitle } from "../components/ui";

interface Article {
  id: number; slug: string; title: string; article_type: string; tags: string[]; status: string; visibility: string;
  published_at: string | null; updated_at: string; likes: number; liked: boolean; can_edit: boolean;
  author: { handle: string; display_name: string } | null; body_md?: string; body_html?: string;
  comments?: { id: number; body_html: string; created_at: string; status: string; author: { handle: string; display_name: string } }[];
}
const TYPES = ["tutorial", "announcement", "review", "experiment", "comparison", "workflow", "project"];

export function Community() {
  const { data, error } = useApi<Article[]>("/api/articles");
  return (
    <div className="max-w-4xl">
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-xl font-bold">Community</h1>
        <Link to="/community/new"><Button variant="primary">Write</Button></Link>
      </div>
      <p className="mb-3 text-sm text-slate-500">Tutorials, reviews, experiments and project write-ups. Private by default; public sharing only exists after a reviewed public deployment. No activity is ever fabricated.</p>
      <ErrorBox error={error} />
      {data?.length === 0 && <Empty>No posts yet.</Empty>}
      <div className="flex flex-col gap-2">
        {(data ?? []).map((a) => (
          <Card key={a.id} className="!p-3">
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="rounded bg-slate-100 px-1 dark:bg-slate-800">{a.article_type}</span>
              <span>{a.status}</span><span>· {a.visibility}</span><span>· by {a.author?.display_name}</span><span>· {relativeTime(a.updated_at)}</span>
              <span>· ♥ {a.likes}</span>
            </div>
            <Link to={`/community/${a.slug}`} className="text-base font-semibold hover:underline">{a.title}</Link>
            {a.tags.length > 0 && <div className="text-xs text-slate-500">{a.tags.map((t) => `#${t}`).join(" ")}</div>}
          </Card>
        ))}
      </div>
    </div>
  );
}

export function ArticleEditor() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: existing } = useApi<Article>(id ? `/api/articles/${id}` : null);
  const [form, setForm] = useState<{ title: string; body_md: string; article_type: string; tags: string; status: string } | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const f = form ?? (existing ? { title: existing.title, body_md: existing.body_md ?? "", article_type: existing.article_type, tags: existing.tags.join(", "), status: existing.status } : { title: "", body_md: "", article_type: "tutorial", tags: "", status: "draft" });
  const set = (k: keyof typeof f, v: string) => setForm({ ...f, [k]: v });
  const save = async (status: string) => {
    setErr(null);
    try {
      const body = { title: f.title, body_md: f.body_md, article_type: f.article_type, tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean), status, visibility: "private" };
      const r = await api<Article>(id ? `/api/articles/${id}` : "/api/articles", { method: id ? "PUT" : "POST", json: body });
      nav(`/community/${r.slug}`);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="max-w-5xl">
      <h1 className="mb-3 text-xl font-bold">{id ? "Edit post" : "New post"}</h1>
      <ErrorBox error={err} />
      <div className="flex flex-col gap-2">
        <input value={f.title} onChange={(e) => set("title", e.target.value)} placeholder="Title" className="rounded border border-slate-300 px-2 py-1.5 text-lg dark:border-slate-700 dark:bg-slate-900" />
        <div className="flex gap-2">
          <select value={f.article_type} onChange={(e) => set("article_type", e.target.value)} className="rounded border border-slate-300 bg-white px-1 dark:border-slate-700 dark:bg-slate-900">
            {TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          <input value={f.tags} onChange={(e) => set("tags", e.target.value)} placeholder="tags, comma separated" className="flex-1 rounded border border-slate-300 px-2 dark:border-slate-700 dark:bg-slate-900" />
        </div>
        <div className="grid gap-2 lg:grid-cols-2">
          <textarea value={f.body_md} onChange={(e) => set("body_md", e.target.value)} rows={22} placeholder="Markdown: headings, **bold**, lists, tables, ```code```, ![images](https://…), links" className="rounded border border-slate-300 p-2 font-mono text-sm dark:border-slate-700 dark:bg-slate-900" />
          <Card>{preview !== null ? <div className="prose-report text-sm" dangerouslySetInnerHTML={serverSanitizedHtml(preview)} /> : <p className="text-sm text-slate-500">Preview appears here (rendered and sanitized by the server).</p>}</Card>
        </div>
        <div className="flex gap-2">
          <Button onClick={async () => setPreview((await api<{ html: string }>("/api/articles/preview", { method: "POST", json: { body_md: f.body_md } })).html)}>Preview</Button>
          <Button onClick={() => save("draft")}>Save draft</Button>
          <Button variant="primary" onClick={() => save("published")}>Publish (private instance)</Button>
        </div>
      </div>
    </div>
  );
}

export function ArticleView() {
  const { slug } = useParams();
  const { data: a, error, reload } = useApi<Article>(`/api/articles/${slug}`);
  const [comment, setComment] = useState("");
  if (error) return <ErrorBox error={error} />;
  if (!a) return <p className="text-slate-500">Loading…</p>;
  return (
    <div className="max-w-3xl">
      <div className="text-xs text-slate-500">{a.article_type} · {a.status} · {a.visibility} · by <Link className="underline" to={`/community?author=${a.author?.handle}`}>{a.author?.display_name}</Link> · {relativeTime(a.published_at ?? a.updated_at)}</div>
      <h1 className="mt-1 text-2xl font-bold">{a.title}</h1>
      <div className="my-2 flex gap-2">
        <Button onClick={async () => { await api(`/api/reactions/article/${a.id}`, { method: "POST" }); reload(); }}>{a.liked ? "♥" : "♡"} {a.likes}</Button>
        {a.can_edit && <Link to={`/community/edit/${a.id}`}><Button>Edit</Button></Link>}
      </div>
      <Card><div className="prose-report text-sm" dangerouslySetInnerHTML={serverSanitizedHtml(a.body_html ?? "")} /></Card>
      <SectionTitle>Comments ({a.comments?.length ?? 0})</SectionTitle>
      {(a.comments ?? []).map((c) => (
        <div key={c.id} className="mb-2 border-l-2 border-slate-300 pl-3 text-sm dark:border-slate-700">
          <div className="text-xs text-slate-500">{c.author.display_name} · {relativeTime(c.created_at)}{c.status !== "visible" ? ` · ${c.status}` : ""}</div>
          <div className="prose-report" dangerouslySetInnerHTML={serverSanitizedHtml(c.body_html)} />
        </div>
      ))}
      <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} className="mt-2 w-full rounded border border-slate-300 p-2 text-sm dark:border-slate-700 dark:bg-slate-900" placeholder="Add a comment (Markdown)" />
      <Button onClick={async () => { await api("/api/comments", { method: "POST", json: { article_id: a.id, body_md: comment } }); setComment(""); reload(); }} disabled={!comment.trim()}>Comment</Button>
    </div>
  );
}
