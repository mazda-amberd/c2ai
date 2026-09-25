/**
 * Types for GET /api/v2/metrics. Drop into `frontend/src/types/` as-is.
 * Contract: metrics-v2.md
 */

export type MetricLevel = "cluster" | "tier" | "application";

/**
 * Read this instead of assuming a unit — it varies per metric, not just per level.
 * `percent` 0-100 | `count` integer tally | `cores` | `bytes` | `bytes_per_second`
 * `seconds` | `ops` (per second) | `celsius` | `watts`
 * `gpus` is whole GPUs, NOT a percentage — never render it with a % suffix.
 */
export type MetricUnit =
  | "percent"
  | "cores"
  | "bytes"
  | "bytes_per_second"
  | "count"
  | "seconds"
  | "ops"
  | "celsius"
  | "watts"
  | "gpus";

/** The PRD timeline dropdown. `6h` is the default; `30s` was removed on review. */
export const RANGE_PRESETS = [
  "1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "24h", "2d",
] as const;
export const DEFAULT_RANGE: RangePreset = "6h";
export type RangePreset = (typeof RANGE_PRESETS)[number];

export interface MetricValue {
  /** null means "no data"; 0 means "measured zero". Never coalesce with `?? 0`. */
  value: number | null;
  unit: MetricUnit;
  /** Reserved for per-metric health; currently always null. */
  status: string | null;
  /** False when the query failed or returned nothing. The key is always present. */
  available: boolean;
  /** Samples across the requested window, oldest first (empty when unavailable). */
  points?: MetricSample[];
}

export interface MetricSample {
  /** Milliseconds since the epoch. */
  timestamp: number;
  value: number;
}

export interface Scope {
  /** "cluster" | "tier" | "application" today; new levels add new values. */
  kind: string;
  /** Unique within a response — use as the React key. */
  id: string;
  name: string;
  /** 1-4 at tier level, null elsewhere. */
  tier: number | null;
  /** Application level only: the namespace, for the logs and deployment endpoints. */
  subdomain: string | null;
  client_name: string | null;
  instance_name: string | null;
}

export interface MetricSeries {
  scope: Scope;
  /** Keyed by metric name. Every scope at a level carries the same key set. Iterate it. */
  metrics: Record<string, MetricValue>;
}

export interface MetricsWindow {
  from: string;
  to: string;
  /** null for a custom from/to window. */
  preset: RangePreset | null;
  step_seconds: number;
}

export interface MetricsError {
  metric: string;
  message: string;
}

export interface MetricsResponse {
  level: MetricLevel;
  window: MetricsWindow;
  generated_at: string;
  /** Poll interval in seconds — currently 30. */
  refresh_after_seconds: number;
  series: MetricSeries[];
  /** true when some metrics failed; the rest of the payload is still valid. */
  degraded: boolean;
  errors: MetricsError[];
}

/** Metric keys per level, mirroring the Story 1.2-1.4 dashboards. */
export const CLUSTER_METRICS = [
  "nodes_ready",
  "nodes_not_ready",
  "pods_running",
  "pods_pending",
  "pods_failed",
  "pod_restarts",
  "network_receive_bps",
  "network_transmit_bps",
  "disk_read_bps",
  "disk_write_bps",
  "cpu_allocated_percent",
  "memory_allocated_percent",
  "pod_capacity_percent",
] as const;

export const TIER_METRICS = [
  "gpus_available",
  "gpus_allocated",
  "active_deployments",
  "gpu_utilization_percent",
  "gpu_temperature_celsius",
  "gpu_power_watts",
  "gpu_memory_percent",
] as const;

export const APPLICATION_METRICS = [
  "health",
  "uptime_seconds",
  "replicas_running",
  "restarts",
  "cpu_cores",
  "memory_bytes",
  "prompt_tokens_per_second",
  "completion_tokens_per_second",
  "total_tokens_per_second",
  "llm_latency_seconds",
  "request_success_percent",
  "request_error_percent",
] as const;

/** Either a preset or a custom window, never both. */
export type MetricsQuery = {
  level: MetricLevel;
  tier?: 1 | 2 | 3 | 4;
} & ({ range?: RangePreset; from?: never; to?: never } | { range?: never; from: string; to: string });

export function buildMetricsQuery(query: MetricsQuery): string {
  const params = new URLSearchParams({ level: query.level });
  if (query.tier !== undefined) params.set("tier", String(query.tier));
  if ("range" in query && query.range) params.set("range", query.range);
  if ("from" in query && query.from) {
    params.set("from", query.from);
    params.set("to", query.to);
  }
  return `/api/v2/metrics?${params.toString()}`;
}
