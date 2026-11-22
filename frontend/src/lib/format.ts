export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "unknown";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "unknown";
  const s = Math.round((now.getTime() - t) / 1000);
  const abs = Math.abs(s);
  const fmt = (n: number, u: string) => `${n} ${u}${n === 1 ? "" : "s"}`;
  let out: string;
  if (abs < 60) out = fmt(abs, "second");
  else if (abs < 3600) out = fmt(Math.round(abs / 60), "minute");
  else if (abs < 86400) out = fmt(Math.round(abs / 3600), "hour");
  else if (abs < 86400 * 60) out = fmt(Math.round(abs / 86400), "day");
  else return new Date(iso).toISOString().slice(0, 10);
  return s >= 0 ? `${out} ago` : `in ${out}`;
}

export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function compactNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (a >= 1e4) return `${(n / 1e3).toFixed(0)}K`;
  if (a >= 1e3) return n.toLocaleString("en-US");
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function bytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${+(seconds / 3600).toFixed(1)} h`;
  return `${+(seconds / 86400).toFixed(1)} d`;
}

const METRIC_LABELS: Record<string, string> = {
  stars: "GitHub stars",
  forks: "forks",
  open_issues: "open issues",
  hf_likes: "HF likes",
  hf_downloads: "HF downloads (30 d)",
  hf_trending: "HF trending score",
  hn_points: "HN points",
  hn_comments: "HN comments",
  hf_upvotes: "HF paper upvotes",
  hf_comments: "HF comments",
};
export function metricLabel(k: string): string {
  return METRIC_LABELS[k] ?? k.replace(/_/g, " ");
}

export const LABEL_DESCRIPTIONS: Record<string, string> = {
  announcement: "Official announcement by the publisher",
  research: "Research paper / preprint (not peer-review status)",
  preview: "Preview, waitlist, alpha or release candidate",
  beta: "Beta",
  release: "New release / newly published artifact",
  update: "Update or improvement to something existing",
  deprecation: "Deprecation, retirement or shutdown",
  rumor: "Rumor or unconfirmed report",
  opportunity: "Job, grant, hackathon, beta program or similar",
  discussion: "Community discussion",
  unlabeled: "No rule matched",
};

export const VERIFICATION_DESCRIPTIONS: Record<string, string> = {
  official: "From the publisher's own official channel",
  corroborated: "Reported by 2+ independent sources",
  primary: "Self-published by the author/maintainer (e.g. repo, paper, model card)",
  community: "Reported by a community/aggregator source only",
  unverified: "No verification evidence",
};
