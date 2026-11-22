export interface ScoreReason {
  component: string;
  contribution: number;
  kind: "measured" | "inferred" | "none" | string;
  evidence: string;
}

export interface CategoryRef {
  slug: string;
  name: string;
  method: string;
  confidence: number;
}

export interface SourceRef {
  key: string;
  name: string;
  publisher: string;
  class: string;
  url: string | null;
}

export interface MediaInfo {
  platform: string;
  format: string;
  id?: string | null;
  embed?: string | null;
  thumbnail?: string | null;
  duration_s?: number | null;
  channel?: string;
  vertical?: boolean;
}

export interface ItemCard {
  id: number;
  kind: string;
  title: string;
  url: string | null;
  canonical_url: string | null;
  summary: string;
  author: string;
  organization: string;
  published_at: string | null;
  discovered_at: string;
  updated_at: string;
  label: string;
  label_evidence: string;
  verification: string;
  license: string | null;
  tags: string[];
  metrics: Record<string, number>;
  links: Record<string, string>;
  score: number;
  score_reasons: ScoreReason[];
  relevance: number;
  categories: CategoryRef[];
  sources: SourceRef[];
  source_count: number;
  ai_summary: { summary: string; model: string; status: string } | null;
  bookmarked: boolean;
  is_fixture: boolean;
  media?: MediaInfo | null;
  relation?: string;
}

export interface ScoreComponent {
  value: number;
  kind: string;
  evidence: string;
  weight: number;
  contribution: number;
}

export interface ItemDetail extends ItemCard {
  extra: Record<string, unknown>;
  identity_keys: string[];
  license_note: string;
  score_components: Record<string, ScoreComponent>;
  scored_at: string | null;
  categories_detail: { slug: string; name: string; method: string; confidence: number; evidence: string }[];
  provenance: {
    source_key: string;
    source_name: string;
    publisher: string;
    source_class: string;
    attribution: string;
    external_id: string;
    source_url: string | null;
    title_at_source: string;
    published_at: string | null;
    first_seen_at: string;
    last_seen_at: string;
    metrics: Record<string, number>;
    raw_record_id: number | null;
    raw_versions: number;
  }[];
  metric_history: Record<string, [string, number][]>;
  release_events: {
    event_type: string;
    version: string;
    title: string;
    event_date: string | null;
    source_url: string | null;
    confidence: string;
    verification: string;
    recorded_at: string;
  }[];
  opportunity: {
    type: string;
    organization: string | null;
    role: string;
    remote: string;
    compensation: string | null;
    deadline: string | null;
    status: string;
    application_url: string | null;
    evidence: Record<string, string>;
    last_checked_at: string | null;
  } | null;
  ai_summary_full: {
    status?: string;
    summary?: string;
    what_it_is?: string;
    why_it_matters?: string;
    key_points?: string[];
    category_suggestions?: string[];
    maturity?: string;
    provider?: string;
    model?: string;
    generated_at?: string;
    cache_hit?: boolean;
    notice?: string;
    grounding?: { checked: boolean; removed: { field: string; sentence: string; reasons: string[] }[] };
  } | null;
  related: ItemCard[];
  dedupe_candidates: { id: number; other_id: number; similarity: number; reason: string; status: string }[];
  redirect_to?: number;
}

export interface User {
  id: number;
  email: string;
  handle: string;
  display_name: string;
  role: string;
}

export interface Source {
  id: number;
  key: string;
  name: string;
  connector_type: string;
  endpoint: string;
  homepage: string;
  publisher: string;
  source_class: string;
  access_method: string;
  auth_method: string;
  cost_status: string;
  quota_notes: string;
  terms_url: string;
  terms_status: string;
  terms_checked_at: string | null;
  attribution: string;
  storage_notes: string;
  allowed_hosts: string[];
  min_interval_s: number;
  interval_s: number;
  enabled: boolean;
  paused: boolean;
  disabled_reason: string;
  health: string;
  last_attempt_at: string | null;
  last_success_at: string | null;
  next_run_at: string | null;
  backoff_until: string | null;
  consecutive_failures: number;
  last_error: string;
  rate_limit_remaining: number | null;
  stats: { items?: number; requests_24h?: number; request_errors_24h?: number };
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
