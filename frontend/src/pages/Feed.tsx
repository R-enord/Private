import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { qs, useApi } from "../lib/api";
import type { ItemCard, Paged } from "../lib/types";
import { emptyFilters, Filters, type FilterState } from "../components/Filters";
import { ItemList } from "../components/ItemCard";
import { Button, Empty, ErrorBox } from "../components/ui";

export function Feed() {
  const [params] = useSearchParams();
  const [filters, setFilters] = useState<FilterState>({ ...emptyFilters, category: params.get("category") ?? "", kind: params.get("kind") ?? "" });
  const [sort, setSort] = useState("score");
  const [page, setPage] = useState(1);
  const [bookmarked, setBookmarked] = useState(false);
  const { data, error, setData } = useApi<Paged<ItemCard>>(`/api/items${qs({ ...filters, sort, page, page_size: 30, bookmarked })}`);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Discovery feed</h1>
        <label className="text-sm">
          sort{" "}
          <select value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }} className="rounded border border-slate-300 bg-white px-1 py-0.5 dark:border-slate-700 dark:bg-slate-900">
            <option value="score">rank (explained)</option>
            <option value="discovered">newest discovered</option>
            <option value="published">newest published</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-sm">
          <input type="checkbox" checked={bookmarked} onChange={(e) => { setBookmarked(e.target.checked); setPage(1); }} /> bookmarks only
        </label>
        <span className="text-sm text-slate-500">{data ? `${data.total.toLocaleString()} items` : ""}</span>
      </div>
      <Filters value={filters} onChange={(f) => { setFilters(f); setPage(1); }} />
      <ErrorBox error={error} />
      <div className="mt-3">
        {data && data.items.length === 0 ? <Empty>No items match these filters.</Empty> : null}
        {data && <ItemList items={data.items} onChange={(it) => setData({ ...data, items: data.items.map((x) => (x.id === it.id ? it : x)) })} />}
      </div>
      <div className="mt-4 flex items-center gap-2">
        <Button disabled={page <= 1} onClick={() => setPage(page - 1)}>← prev</Button>
        <span className="text-sm">page {page} / {pages}</span>
        <Button disabled={page >= pages} onClick={() => setPage(page + 1)}>next →</Button>
      </div>
    </div>
  );
}
