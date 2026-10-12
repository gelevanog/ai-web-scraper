export type AppConfig = {
  provider: string;
  model: string;
  require_free_models: boolean;
  browser: string;
  browser_available: boolean;
  respect_robots: boolean;
  min_seconds_per_domain: number;
  user_agent: string;
  pii_guard: boolean;
  injection_guard: boolean;
  today: string;
};

export type Site = { id: string; name: string; vertical: string; mode: string; start_url: string; intent: string; traps: string[]; records: number };

export type FieldType = "string" | "integer" | "number" | "boolean" | "money" | "date" | "datetime" | "url" | "enum" | "list" | "object";

export type FieldSpec = {
  name: string;
  type: FieldType;
  description?: string;
  required?: boolean;
  enum?: string[] | null;
  items?: FieldSpec | null;
  fields?: FieldSpec[] | null;
  currency?: string | null;
  unit?: string | null;
  contact?: "none" | "business";
  key?: boolean;
};

export type Schema = { name?: string; description?: string; fields: FieldSpec[] };

export type Rejected = { name: string | null; reason: string };

export type FieldProvenance = { rule?: string; node?: string; raw?: unknown; matches?: number };

export type Provenance = {
  url: string;
  strategy: "learned" | "direct";
  extractor?: string;
  model?: string;
  item?: string;
  fields?: Record<string, FieldProvenance>;
  detail?: { url: string; fields: Record<string, FieldProvenance> };
};

export type SiftRecord = {
  id: string;
  key: string;
  data: Record<string, unknown>;
  confidence: Record<string, number>;
  record_confidence: number;
  provenance: Provenance;
  flags: string[];
  notes: Record<string, string[]>;
};

export type LlmStats = { calls: number; cached: number; failed: number; by_task: Record<string, number>; input_tokens: number; output_tokens: number; seconds: number };

export type JobStats = {
  pages: number;
  pages_ok: number;
  pages_unchanged: number;
  records: number;
  errors: number;
  robots_blocked: number;
  duplicates_merged: number;
  sparse_dropped: number;
  anomalies: number;
  pii_removed: number;
  injection_findings: number;
  browser_pages: number;
  llm: LlmStats;
  seconds: number;
  strategies: string[];
};

export type HostPoliteness = { requests: number; subresources: number; robots_fetches: number; min_gap_s: number | null; configured_interval_s: number; max_requests_per_s_10s: number };

export type Job = {
  id: string;
  name: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "cancelling";
  start_urls: string[];
  description: string;
  schema: Schema | null;
  options: Record<string, unknown>;
  stats: Partial<JobStats>;
  politeness: { hosts?: Record<string, HostPoliteness>; robots_blocked?: string[] };
  rejected_fields: Rejected[];
  progress: { pages?: number; records?: number; llm_calls?: number; errors?: number };
  model: string;
  parent_id: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  record_count?: number;
};

export type PageRow = {
  id: number;
  url: string;
  status: number;
  via: string;
  role: string;
  depth: number;
  strategy: string;
  extractor: string | null;
  records: number;
  content_hash: string;
  fingerprint: string;
  similarity: number | null;
  unchanged: boolean;
  error: string | null;
  ms: number;
};

export type EventRow = { id: number; job_id: string | null; ts: string; kind: string; level: string; message: string; data: Record<string, unknown> };

export type Rule = { css?: string | null; xpath?: string | null; attr?: string; all?: boolean; regex?: string | null; value_map?: Record<string, string> | null; fields?: Record<string, Rule> | null; fallback?: Rule | null; negate?: boolean };

export type Program = { kind: string; item?: string | null; fields: Record<string, Rule>; next_page?: Rule | null; detail_link?: Rule | null; missing?: string[] };

export type ExtractorRow = {
  pk: number;
  id: string;
  version: number;
  host: string;
  kind: string;
  origin: string;
  parent: string | null;
  accepted: boolean;
  calls: number;
  state: { program: Program; baseline_items: number; baseline_fill: Record<string, number>; check: { errors?: string[]; warnings?: string[]; history?: unknown[] } };
};

export type Proposal = { schema: Schema | null; rejected: Rejected[]; error: string | null; injection_findings: { sentence: string; rules: string[]; where: string }[]; llm_calls: LlmStats };

export type Preview = { schema: Schema | null; records: SiftRecord[]; events: { kind: string; message: string; level: string; data: Record<string, unknown> }[]; stats: JobStats; extractors: { program: Program }[]; error: string | null };
