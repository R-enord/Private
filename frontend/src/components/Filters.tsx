import { useApi } from "../lib/api";

export interface FilterState {
  category: string;
  kind: string;
  label: string;
  source: string;
  verification: string;
}

export const emptyFilters: FilterState = { category: "", kind: "", label: "", source: "", verification: "" };

interface Facets {
  kinds: Record<string, number>;
  labels: Record<string, number>;
  verification: Record<string, number>;
  sources: { key: string; name: string; enabled: boolean }[];
}

function Select({ value, onChange, label, options }: { value: string; onChange: (v: string) => void; label: string; options: [string, string][] }) {
  return (
    <label className="flex items-center gap-1 text-sm">
      <span className="text-slate-500">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="max-w-[13rem] rounded border border-slate-300 bg-white px-1.5 py-1 text-sm dark:border-slate-700 dark:bg-slate-900">
        <option value="">all</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

/** One row of filter controls, placed above the results. */
export function Filters({ value, onChange }: { value: FilterState; onChange: (f: FilterState) => void }) {
  const { data: facets } = useApi<Facets>("/api/facets");
  const { data: cats } = useApi<{ slug: string; name: string; count: number }[]>("/api/categories");
  const set = (k: keyof FilterState) => (v: string) => onChange({ ...value, [k]: v });
  const entries = (o?: Record<string, number>) => Object.entries(o ?? {}).map(([k, n]) => [k, `${k} (${n})`] as [string, string]);
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
      <Select label="Category" value={value.category} onChange={set("category")} options={(cats ?? []).map((c) => [c.slug, `${c.name} (${c.count})`])} />
      <Select label="Type" value={value.kind} onChange={set("kind")} options={entries(facets?.kinds)} />
      <Select label="Label" value={value.label} onChange={set("label")} options={entries(facets?.labels)} />
      <Select label="Verification" value={value.verification} onChange={set("verification")} options={entries(facets?.verification)} />
      <Select label="Source" value={value.source} onChange={set("source")} options={(facets?.sources ?? []).filter((s) => s.enabled).map((s) => [s.key, s.name])} />
      {Object.values(value).some(Boolean) && (
        <button className="text-sm text-blue-700 underline dark:text-blue-400" onClick={() => onChange(emptyFilters)}>
          clear
        </button>
      )}
    </div>
  );
}
