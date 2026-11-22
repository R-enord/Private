import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { qs, useApi } from "../lib/api";
import type { ItemCard } from "../lib/types";
import { emptyFilters, Filters, type FilterState } from "../components/Filters";
import { ItemList } from "../components/ItemCard";
import { Button, Empty, ErrorBox } from "../components/ui";

interface SearchResult {
  query: string;
  interpreted_query: string;
  interpreted_filters: string[];
  method: string;
  semantic: string | null;
  items: ItemCard[];
}

export function Search() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const [text, setText] = useState(q);
  const [mode, setMode] = useState("hybrid");
  const [filters, setFilters] = useState<FilterState>(emptyFilters);
  const { data, error, loading } = useApi<SearchResult>(q ? `/api/search${qs({ q, mode, ...filters })}` : null);
  return (
    <div>
      <h1 className="mb-3 text-xl font-bold">Search</h1>
      <form
        className="mb-3 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setParams(text ? { q: text } : {});
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='e.g. "MCP servers for databases this week", "papers on agent evaluation"'
          className="min-w-[18rem] flex-1 rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          maxLength={300}
        />
        <select value={mode} onChange={(e) => setMode(e.target.value)} className="rounded border border-slate-300 bg-white px-2 dark:border-slate-700 dark:bg-slate-900" title="Keyword works without AI; semantic uses local embeddings when available">
          <option value="hybrid">hybrid</option>
          <option value="keyword">keyword</option>
          <option value="semantic">semantic (local)</option>
        </select>
        <Button type="submit" variant="primary">Search</Button>
      </form>
      <Filters value={filters} onChange={setFilters} />
      <ErrorBox error={error} />
      {data && (
        <div className="my-2 text-xs text-slate-500">
          method: {data.method}
          {data.interpreted_filters.length > 0 && <> · interpreted: {data.interpreted_filters.join(", ")} → “{data.interpreted_query}”</>}
          {data.semantic && <> · {data.semantic}</>}
        </div>
      )}
      {loading && <p className="text-sm text-slate-500">Searching…</p>}
      {data && (data.items.length ? <ItemList items={data.items} /> : <Empty>No matches.</Empty>)}
    </div>
  );
}
