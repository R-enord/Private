import { Link } from "react-router-dom";
import { useApi } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { ItemCard } from "../lib/types";
import { ItemList } from "../components/ItemCard";
import { Empty, ErrorBox, SectionTitle, Stat } from "../components/ui";

interface HomeData {
  stats: { items_total: number; items_24h: number; sources_enabled: number; sources_healthy: number; last_discovery_at: string | null };
  sections: { key: string; title: string; items: ItemCard[]; empty_note?: string }[];
}

export function Home() {
  const { data, error } = useApi<HomeData>("/api/home");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <p className="text-slate-500">Loading…</p>;
  const s = data.stats;
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Discoveries (24 h)" value={s.items_24h.toLocaleString()} />
        <Stat label="Total items" value={s.items_total.toLocaleString()} />
        <Stat label="Healthy sources" value={`${s.sources_healthy} / ${s.sources_enabled}`} hint="Enabled sources reporting healthy" />
        <Stat label="Last discovery" value={relativeTime(s.last_discovery_at)} />
      </div>
      {s.items_total === 0 && (
        <div className="mt-4">
          <Empty>
            Nothing collected yet. Make sure the worker is running (<code>hub worker</code> or <code>hub start</code>) — it
            keeps collecting even when this page is closed. See <Link className="underline" to="/sources">Sources</Link>.
          </Empty>
        </div>
      )}
      <div className="grid gap-x-6 lg:grid-cols-2">
        {data.sections.map((sec) => (
          <section key={sec.key} className={sec.key === "important" ? "lg:col-span-2" : ""}>
            <SectionTitle>{sec.title}</SectionTitle>
            {sec.items.length ? <ItemList items={sec.items} /> : <Empty>{sec.empty_note ?? "Nothing in this section yet."}</Empty>}
          </section>
        ))}
      </div>
    </div>
  );
}
