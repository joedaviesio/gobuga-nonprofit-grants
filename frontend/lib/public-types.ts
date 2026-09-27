// Shapes returned by the backend's keyless /api/v1 (api/public_v1.py and
// api/public_data.py). The public pages render these and nothing else.

export interface Provenance {
  source_url: string | null;
  verified_at: string | null;
  excerpt: string | null;
}

export interface GrantBrief {
  id: string;
  title: string | null;
  canonical_url: string;
}

export interface GrantRecord {
  id: string;
  country: string;
  status: "live" | "closed" | "stale";
  title: string | null;
  funder: { name: string | null; slug: string | null; url: string | null };
  deadline: {
    state: string | null;
    date: string | null;
    verified_at: string | null;
    source_url: string | null;
    excerpt: string | null;
  };
  amount: { min: number | null; max: number | null; currency: string | null };
  region: string[];
  tags: string[];
  eligibility: string | null;
  eligible_entities: string[];
  summary: string | null;
  source_url: string | null;
  apply_url: string;
  canonical_url: string;
  json_url: string;
  first_seen: string | null;
  last_seen: string | null;
  closed_at: string | null;
  verified_at: string | null;
  verified_by: string | null;
  provenance: Record<string, Provenance>;
  attribution: { source: string; url: string; retrieved_at: string; licence: string };
  notice?: string;
  related?: GrantBrief[];
}

export interface ListMeta {
  published_at: string | null;
  sweep_month: string | null;
}

export interface ListBody<T> {
  data: T[];
  total: number;
  offset: number;
  limit: number;
  meta: ListMeta;
}

export interface FitItem extends GrantRecord {
  score: number;
  why: string[];
}

export interface FitBody extends ListBody<FitItem> {
  canonical_query: string;
  canonical_url: string;
  json_url: string;
  feed_url: string;
}

export interface FunderSummary {
  name: string;
  slug: string;
  url: string;
  json_url: string;
  live_count: number;
  closed_count: number;
}

export interface FunderBody extends FunderSummary {
  live: GrantRecord[];
  closed: GrantRecord[];
  meta: ListMeta;
}

export interface Taxonomy {
  country: string;
  country_label: string;
  currency: string;
  tags: { slug: string; label: string | null }[];
  regions: string[];
  entity_vocab: string[];
  size_bands: { slug: string; revenue_min: number | null; revenue_max: number | null; currency: string }[];
  needs: string[];
  amount_bands: { slug: string; min: number | null; max: number | null; currency: string }[];
  deadline_buckets: { slug: string; description: string }[];
  list_statuses: string[];
  sorts: { slug: string; description: string }[];
  fit_params: string[];
}

export interface ChangeEntry {
  published_at: string | null;
  sweep_month: string | null;
  new: GrantBrief[];
  changed: (GrantBrief & { fields: string[] })[];
  closed: GrantBrief[];
}

export interface StatsBody {
  country: string;
  generated_at: string;
  usage: {
    month: string;
    clickouts: { total: number; by_referrer: Record<string, number> };
    fit_urls_built: number;
    api_calls: { total: number; by_endpoint: Record<string, number> };
    mcp_calls: { total: number; by_tool: Record<string, number> };
    crawler_hits: { total: number; by_agent: Record<string, number> };
  };
  dataset?: {
    live_count: number;
    closed_count: number;
    funder_count: number;
    last_sweep: string | null;
    last_sweep_run: string | null;
    published_at: string | null;
    next_sweep_due: string | null;
  };
  subscribers?: { confirmed: number };
}

export interface ApiIndex {
  name: string;
  description: string;
  country: string;
  country_label: string;
  base_url: string;
  api_base_url: string;
  openapi_url: string;
  taxonomy_url: string;
  licence: { id: string; summary: string };
  url_scheme: { page: string | null; public: string; backend: string; format: string }[];
  meta: ListMeta;
}

export interface IdsBody {
  data: { id: string; status: string; last_seen: string | null; canonical_url: string }[];
  total: number;
}

export interface CountryConfig {
  country: string;
  country_label: string;
  currency: string;
  content_language: string;
  ui_languages: string[];
  timezone?: string;
}

export interface ApiError {
  error: { code: string; message: string };
}
