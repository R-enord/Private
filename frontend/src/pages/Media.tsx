// Media section: Shorts (default) · News · Videos · Posts · Write-ups.
// Privacy: nothing loads from YouTube/TikTok until you tap Play/Open; thumbnails come through the hub (when enabled).
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, qs, useApi } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { ItemCard, MediaInfo, User } from "../lib/types";
import { ItemList } from "../components/ItemCard";
import { Button, Card, Empty, ErrorBox, ExternalLink, LabelBadge, VerificationBadge } from "../components/ui";

const TABS: [string, string][] = [
  ["shorts", "Shorts & Reels"],
  ["news", "News"],
  ["videos", "Videos"],
  ["posts", "Posts"],
  ["writeups", "Write-ups"],
];
const PLATFORM: Record<string, string> = {
  youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", x: "X", bluesky: "Bluesky", mastodon: "Mastodon",
  reddit: "Reddit", vimeo: "Vimeo", devto: "DEV", writeup: "Blog",
};
const OPEN_IN: Record<string, string> = { tiktok: "Open in TikTok", instagram: "Open in Instagram", x: "Open on X" };

interface MediaResponse {
  items: ItemCard[];
  total: number;
  counts: Record<string, number>;
  previews_enabled: boolean;
  oembed_enabled: boolean;
}
interface MediaSources {
  sources: { id: number; key: string; name: string; enabled: boolean; health: string; terms_status: string; terms_url: string; disabled_reason: string }[];
  settings: { media_previews_enabled: boolean; media_oembed_enabled: boolean };
  not_available: { platform: string; why: string }[];
}

function fmtDuration(s?: number | null) {
  if (!s) return "";
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`;
}

function Thumb({ item, media, previews, vertical }: { item: ItemCard; media: MediaInfo; previews: boolean; vertical: boolean }) {
  const [failed, setFailed] = useState(false);
  const show = previews && media.thumbnail && !failed;
  return (
    <div className={`relative flex items-start justify-center overflow-hidden bg-gradient-to-br from-slate-800 to-slate-950 ${vertical ? "mx-auto aspect-[9/16] h-[64dvh] md:h-auto md:w-full" : "aspect-video w-full"}`}>
      {show ? (
        <img src={`/api/media/thumb/${item.id}`} alt="" loading="lazy" onError={() => setFailed(true)} className="h-full w-full object-cover" />
      ) : (
        <div className="px-4 pt-[18%] text-center text-slate-200">
          <div className="text-xs uppercase tracking-widest text-slate-400">{PLATFORM[media.platform] ?? media.platform}</div>
          <div className="mt-2 line-clamp-5 text-base font-medium">{item.title}</div>
        </div>
      )}
      {media.duration_s ? <span className="absolute bottom-2 right-2 rounded bg-black/75 px-1 text-xs text-white">{fmtDuration(media.duration_s)}</span> : null}
    </div>
  );
}

function YouTubePlayer({ id, title, vertical }: { id: string; title: string; vertical: boolean }) {
  return (
    <iframe
      className={`${vertical ? "mx-auto block aspect-[9/16] h-[64dvh] md:h-auto md:w-full" : "aspect-video w-full"} bg-black`}
      src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&playsinline=1&rel=0`}
      title={title}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
      allowFullScreen
      referrerPolicy="strict-origin-when-cross-origin"
    />
  );
}

function MediaCard({ item, previews, vertical, onChange }: { item: ItemCard; previews: boolean; vertical: boolean; onChange: (i: ItemCard) => void }) {
  const [playing, setPlaying] = useState(false);
  const media = item.media ?? { platform: "web", format: "video" };
  const canPlay = media.embed === "youtube" && media.id;
  const toggleBookmark = async () => {
    const r = await api<{ bookmarked: boolean }>(`/api/items/${item.id}/bookmark`, { method: item.bookmarked ? "DELETE" : "POST" });
    onChange({ ...item, bookmarked: r.bookmarked });
  };
  return (
    <article className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {playing && canPlay ? (
        <YouTubePlayer id={media.id!} title={item.title} vertical={vertical} />
      ) : (
        <button className="block w-full bg-slate-950" onClick={() => (canPlay ? setPlaying(true) : window.open(item.url ?? "#", "_blank", "noopener,noreferrer"))} aria-label={canPlay ? `Play ${item.title}` : `Open ${item.title}`}>
          <div className="relative">
            <Thumb item={item} media={media} previews={previews} vertical={vertical} />
            <span className="absolute inset-x-0 bottom-5 flex justify-center">
              <span className="rounded-full bg-black/60 px-4 py-2 text-sm font-semibold text-white">{canPlay ? "▶ Play" : (OPEN_IN[media.platform] ?? "Open ↗")}</span>
            </span>
          </div>
        </button>
      )}
      <div className="p-3">
        <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="rounded bg-slate-900 px-1.5 py-0.5 font-semibold text-white dark:bg-slate-100 dark:text-slate-900">{PLATFORM[media.platform] ?? media.platform}</span>
          {item.is_fixture && <span className="rounded bg-yellow-200 px-1 text-yellow-900">TEST FIXTURE</span>}
          <LabelBadge label={item.label} evidence={item.label_evidence} />
          <VerificationBadge level={item.verification} />
        </div>
        <Link to={`/items/${item.id}`} className="line-clamp-2 text-sm font-semibold hover:underline">{item.title}</Link>
        <div className="mt-1 flex flex-wrap gap-x-2 text-xs text-slate-500">
          {(media.channel || item.author) && <span>{media.channel || item.author}</span>}
          <span title="published per source">{relativeTime(item.published_at)}</span>
          <span>· via {item.sources.map((s) => s.publisher || s.name).filter((v, i, a) => a.indexOf(v) === i).join(", ")}</span>
        </div>
        <div className="mt-2 flex items-center gap-3 text-sm">
          <ExternalLink href={item.url}>original ↗</ExternalLink>
          {Object.entries(item.links).filter(([k]) => k.startsWith("discussion_")).map(([k, v]) => (
            <ExternalLink key={k} href={v}>{k.replace("discussion_", "")} discussion ↗</ExternalLink>
          ))}
          <button onClick={toggleBookmark} className="ml-auto text-lg text-slate-400 hover:text-amber-500" aria-pressed={item.bookmarked} title="Bookmark">{item.bookmarked ? "★" : "☆"}</button>
        </div>
      </div>
    </article>
  );
}

function SaveLink({ onSaved }: { onSaved: (i: ItemCard) => void }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <Button onClick={() => setOpen(true)}>+ Save a link</Button>;
  return (
    <Card className="w-full">
      <p className="mb-2 text-sm text-slate-500">Paste a TikTok, Reel, YouTube, X, Bluesky or article link. The hub stores the link (it does not download the page or video).</p>
      <form className="flex flex-col gap-2" onSubmit={async (e) => {
        e.preventDefault();
        setErr(null);
        try {
          const r = await api<{ item: ItemCard }>("/api/media/links", { method: "POST", json: { url, note } });
          onSaved(r.item);
          setUrl("");
          setNote("");
          setOpen(false);
        } catch (ex) {
          setErr((ex as Error).message);
        }
      }}>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.tiktok.com/@…/video/…" inputMode="url" className="rounded border border-slate-300 px-2 py-2 dark:border-slate-700 dark:bg-slate-950" required />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className="rounded border border-slate-300 px-2 py-2 dark:border-slate-700 dark:bg-slate-950" />
        <ErrorBox error={err} />
        <div className="flex gap-2"><Button type="submit" variant="primary">Save</Button><Button onClick={() => setOpen(false)}>Cancel</Button></div>
      </form>
    </Card>
  );
}

function SourcesPanel({ user, onChanged }: { user: User; onChanged: () => void }) {
  const { data, reload } = useApi<MediaSources>("/api/media/sources");
  const [open, setOpen] = useState(false);
  if (!data) return null;
  const off = data.sources.filter((s) => !s.enabled);
  const admin = ["admin", "owner"].includes(user.role);
  const enable = async (s: MediaSources["sources"][number]) => {
    if (!window.confirm(`${s.name}\n\n${s.disabled_reason}\n\nTerms: ${s.terms_url || "(none listed)"}\n\nConfirm you reviewed the terms and accept: titles, links and short excerpts are stored; videos are never downloaded.`)) return;
    await api(`/api/sources/${s.id}/enable`, { method: "POST", json: { acknowledge_terms: true } });
    reload();
    onChanged();
  };
  const toggle = async (k: "media_previews_enabled" | "media_oembed_enabled") => {
    await api("/api/media/settings", { method: "PATCH", json: { [k]: !data.settings[k] } });
    reload();
    onChanged();
  };
  return (
    <div className="mb-3">
      <button onClick={() => setOpen(!open)} className="w-full rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-left text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
        {off.length} media/news source{off.length === 1 ? "" : "s"} waiting for your review · previews {data.settings.media_previews_enabled ? "on" : "off"} {open ? "▴" : "▾"}
      </button>
      {open && (
        <Card className="mt-2">
          <ul className="flex flex-col gap-2 text-sm">
            {data.sources.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{s.name}</span>
                <span className="text-xs text-slate-500">{s.enabled ? `on · ${s.health}` : "off"}</span>
                {!s.enabled && admin && <span className="ml-auto"><Button onClick={() => enable(s)}>Review & enable</Button></span>}
              </li>
            ))}
          </ul>
          {admin && (
            <div className="mt-3 flex flex-col gap-1 text-sm">
              <label className="flex items-center gap-2"><input type="checkbox" checked={data.settings.media_previews_enabled} onChange={() => toggle("media_previews_enabled")} /> Show thumbnails (the hub fetches them; your phone does not contact the platforms)</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={data.settings.media_oembed_enabled} onChange={() => toggle("media_oembed_enabled")} /> Fetch titles for saved YouTube/TikTok/X links via their official oEmbed endpoints</label>
            </div>
          )}
          <div className="mt-3 text-xs text-slate-500">
            {data.not_available.map((n) => <p key={n.platform} className="mb-1"><b>{n.platform}:</b> {n.why}</p>)}
          </div>
        </Card>
      )}
    </div>
  );
}

export function Media({ user }: { user: User }) {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "shorts";
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState(tab === "news" || tab === "shorts" ? "new" : "top");
  const fixtures = params.get("include_fixtures") === "1"; // test/demo only — fixture cards are badged
  const { data, error, setData, reload } = useApi<MediaResponse>(`/api/media${qs({ tab, q: query, sort, page_size: 60, include_fixtures: fixtures })}`);
  const update = (it: ItemCard) => setData((d) => d && { ...d, items: d.items.map((x) => (x.id === it.id ? it : x)) });
  const isGrid = tab === "shorts" || tab === "videos";
  return (
    <div>
      <div className="-mx-3 mb-2 flex gap-2 overflow-x-auto px-3 pb-1 md:mx-0 md:px-0" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setParams(fixtures ? { tab: k, include_fixtures: "1" } : { tab: k })}
            className={`shrink-0 rounded-full border px-3 py-1.5 text-sm ${tab === k ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900" : "border-slate-300 dark:border-slate-700"}`}>
            {label} <span className="tabular-nums opacity-75">{data?.counts?.[k] ?? "…"}</span>
          </button>
        ))}
      </div>
      <form className="mb-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${TABS.find(([k]) => k === tab)?.[1] ?? ""}… (e.g. agents, MCP, Ollama)`} className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
        <select value={sort} onChange={(e) => setSort(e.target.value)} className="rounded border border-slate-300 bg-white px-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          <option value="new">newest</option>
          <option value="top">top ranked</option>
        </select>
      </form>
      <div className="mb-3 flex flex-wrap gap-2"><SaveLink onSaved={(it) => { setData((d) => d && { ...d, items: [it, ...d.items] }); }} /></div>
      <SourcesPanel user={user} onChanged={reload} />
      <ErrorBox error={error} />
      {data && data.items.length === 0 && (
        <Empty>
          {tab === "shorts"
            ? "No shorts yet. Shorts arrive from YouTube (enable it above), from TikTok/Reels/YouTube links shared on HN, Mastodon or Bluesky, or links you save."
            : "Nothing here yet — enable the sources above, or save a link."}
        </Empty>
      )}
      {data && isGrid && (
        <div className={tab === "shorts" ? "flex snap-y snap-mandatory flex-col gap-4 md:grid md:snap-none md:grid-cols-3 lg:grid-cols-4" : "grid gap-4 sm:grid-cols-2 lg:grid-cols-3"}>
          {data.items.map((it) => (
            <div key={it.id} className={tab === "shorts" ? "snap-start" : ""}>
              <MediaCard item={it} previews={data.previews_enabled} vertical={tab === "shorts"} onChange={update} />
            </div>
          ))}
        </div>
      )}
      {data && !isGrid && <ItemList items={data.items} onChange={update} />}
    </div>
  );
}
