// Raw source payloads are untrusted: rendered as escaped text inside <pre>, never as HTML.
export function JsonViewer({ value }: { value: unknown }) {
  return (
    <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded bg-slate-100 p-3 text-xs dark:bg-slate-950">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
