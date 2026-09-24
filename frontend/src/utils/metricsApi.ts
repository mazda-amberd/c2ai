import { getApplicationMetricsV2, getMetricsV2 } from "@/api/services/metricsV2";
import {
  RANGE_PRESETS,
  type MetricsError,
  type MetricSeries,
  type MetricUnit,
  type MetricValue,
  type RangePreset,
} from "@/types/metricsV2";

export type TimeRangeKey = RangePreset;

const RANGE_LABELS: Record<TimeRangeKey, string> = {
  "1m": "Last 1 minute",
  "5m": "Last 5 minutes",
  "15m": "Last 15 minutes",
  "30m": "Last 30 minutes",
  "1h": "Last 1 hour",
  "3h": "Last 3 hours",
  "6h": "Last 6 hours",
  "12h": "Last 12 hours",
  "24h": "Last 24 hours",
  "2d": "Last 2 days",
};

export const TIME_RANGE_OPTIONS: Array<{ value: TimeRangeKey; label: string }> =
  RANGE_PRESETS.map((value) => ({ value, label: RANGE_LABELS[value] }));

export type MetricPoint = {
  timestamp: number;
  value: number;
};

export type FormattedValue = { display: string; unit: string };

export type AppMetric = {
  key: string;
  label: string;
  /** Sub-line under the value, e.g. "ada-primary · vCPU utilization" */
  sub: string;
  /** Raw values in the metric's native unit (metrics-v2.md's unit table) — always
   *  the same unit across the series, so thresholds and the chart stay consistent
   *  even though `format` may pick a different display scale as the value changes
   *  (e.g. MiB vs GiB). */
  points: MetricPoint[];
  /** Formats a raw value for display (handles unit scaling — bytes, cores, etc). */
  format: (value: number) => FormattedValue;
  /** Metric is alerting (red) when latest raw value >= threshold */
  threshold?: number;
  /** Metric enters the amber band at/above this raw value. Defaults to
   *  80% of `threshold` when unset (and `noWarning` isn't set). */
  warningThreshold?: number;
  /** Metric is alerting when latest raw value < lowThreshold */
  lowThreshold?: number;
  /** Skip the intermediate amber "warning" band — red at/above threshold,
   *  green otherwise, nothing in between. */
  noWarning?: boolean;
  /** Only turn red once the value has stayed at/above `threshold` for this
   *  many minutes straight; a fresh breach shows amber until confirmed. */
  sustainedMinutes?: number;
  /** False when the backend has no data for this metric right now ("—", never 0). */
  available: boolean;
};

/* ------------------------------------------------------------------ */
/* Per-point history                                                   */
/*                                                                      */
/* The v2 metrics API returns one aggregated value per metric per call —
 * there is no per-point time series yet (tracked as an open item on the
 * backend: "Charting needs per-point data; that lands as an additive
 * `points` array on `MetricValue`"). Until then we build a short client-
 * side history by appending the latest value every time we poll, so the
 * chart fills in and starts looking like a real trend the longer a page
 * stays open. A freshly opened page will show a single point until a few
 * polls have landed — that's the real data, not a bug.
 * ------------------------------------------------------------------ */

const MAX_HISTORY_POINTS = 60;
const pointHistory = new Map<string, MetricPoint[]>();

function historyKey(level: string, scopeId: string, metricKey: string, range: TimeRangeKey): string {
  return `${level}:${scopeId}:${metricKey}:${range}`;
}

function recordPoint(key: string, timestamp: number, value: number | null): MetricPoint[] {
  const existing = pointHistory.get(key) ?? [];
  if (value === null) return existing;
  const next = [...existing, { timestamp, value }].slice(-MAX_HISTORY_POINTS);
  pointHistory.set(key, next);
  return next;
}

/* ------------------------------------------------------------------ */
/* Value formatting — per metrics-v2.md's unit table                   */
/* ------------------------------------------------------------------ */

function formatBytes(value: number): FormattedValue {
  const gib = value / 1024 ** 3;
  if (gib >= 1) return { display: gib.toFixed(2), unit: "GiB" };
  const mib = value / 1024 ** 2;
  if (mib >= 1) return { display: mib.toFixed(1), unit: "MiB" };
  const kib = value / 1024;
  return { display: kib.toFixed(1), unit: "KiB" };
}

function formatBytesPerSecond(value: number): FormattedValue {
  const mbps = value / 1024 ** 2;
  if (mbps >= 1) return { display: mbps.toFixed(2), unit: "MB/s" };
  const kbps = value / 1024;
  if (kbps >= 1) return { display: kbps.toFixed(1), unit: "KB/s" };
  return { display: value.toFixed(0), unit: "B/s" };
}

function formatCores(value: number): FormattedValue {
  if (value < 1) return { display: (value * 1000).toFixed(0), unit: "mCPU" };
  return { display: value.toFixed(2), unit: "cores" };
}

/** Generic fallback formatter, used when a metric doesn't need special handling. */
function formatByUnit(unit: MetricUnit, value: number): FormattedValue {
  switch (unit) {
    case "percent":
      return { display: value.toFixed(1), unit: "%" };
    case "gpus":
      return { display: value.toFixed(1), unit: " GPUs" };
    case "count":
      return { display: Math.round(value).toString(), unit: "" };
    case "cores":
      return formatCores(value);
    case "bytes":
      return formatBytes(value);
    case "bytes_per_second":
      return formatBytesPerSecond(value);
    case "celsius":
      return { display: value.toFixed(0), unit: "°C" };
    case "watts":
      return { display: value.toFixed(0), unit: "W" };
    case "seconds":
      return { display: value.toFixed(1), unit: "s" };
    case "ops":
      return { display: value.toFixed(0), unit: "tok/s" };
    default:
      return { display: value.toString(), unit: "" };
  }
}

type MetricDef = {
  label: string;
  sub: (series: MetricSeries) => string;
  /** Overrides the generic by-unit formatter for metrics that need custom framing. */
  format?: (value: number) => FormattedValue;
  /** Raw-unit threshold (same scale as the API value, not the display scale —
   *  e.g. llm_latency_seconds is thresholded in seconds, even though it's
   *  displayed in ms). */
  threshold?: number;
  /** Explicit amber-band floor, in the same raw units as `threshold`.
   *  Falls back to 80% of `threshold` when unset. */
  warningThreshold?: number;
  lowThreshold?: number;
  /** Skip the intermediate amber "warning" band — red at/above threshold,
   *  green otherwise, nothing in between. */
  noWarning?: boolean;
  /** Only turn red once sustained at/above `threshold` for this many minutes. */
  sustainedMinutes?: number;
};

const CLUSTER_METRIC_DEFS: Record<string, MetricDef> = {
  nodes_ready: { label: "Nodes Ready", sub: () => "Kubernetes nodes in Ready state" },
  nodes_not_ready: { label: "Nodes Not Ready", sub: () => "Should be 0", threshold: 1 },
  pods_running: { label: "Running Pod Count", sub: () => "Across all namespaces" },
  pods_pending: { label: "Pending Pod Count", sub: () => "Waiting on scheduler" },
  pods_failed: { label: "Failed Pod Count", sub: () => "Over selected window", threshold: 1 },
  pod_restarts: { label: "Pod Restart Count", sub: () => "Cumulative restarts, all pods" },
  network_receive_bps: { label: "Network Receive", sub: () => "Aggregate inbound traffic" },
  network_transmit_bps: { label: "Network Transmit", sub: () => "Aggregate outbound traffic" },
  disk_read_bps: { label: "Disk Read Throughput", sub: () => "Avg across node pool" },
  disk_write_bps: { label: "Disk Write Throughput", sub: () => "Avg across node pool" },
  // Total capacity vs allocated: red at >=90%, green otherwise — no amber band.
  cpu_allocated_percent: { label: "CPU Allocated", sub: () => "% of cluster capacity requested", threshold: 90, noWarning: true },
  memory_allocated_percent: { label: "Memory Allocated", sub: () => "% of cluster capacity requested", threshold: 90, noWarning: true },
  pod_capacity_percent: { label: "Pod Capacity Allocated", sub: () => "% of cluster pod capacity", threshold: 90, noWarning: true },
};

const TIER_METRIC_DEFS: Record<string, MetricDef> = {
  gpus_available: { label: "GPUs Available", sub: (s) => `Idle GPUs in ${s.scope.name}` },
  gpus_allocated: { label: "GPUs Allocated", sub: (s) => `In use in ${s.scope.name}` },
  active_deployments: { label: "Active Deployments", sub: (s) => `Healthy Ray Serve deployments in ${s.scope.name}` },
  // Green <85%, amber 85-95%, red >95% sustained for 5+ minutes.
  gpu_utilization_percent: {
    label: "GPU Utilization",
    sub: () => "Avg across tier GPUs",
    warningThreshold: 85,
    threshold: 95,
    sustainedMinutes: 5,
  },
  // Green <80°C, amber 80-85°C, red >85°C.
  gpu_temperature_celsius: {
    label: "GPU Temperature",
    sub: () => "Hottest GPU in the tier",
    warningThreshold: 80,
    threshold: 85,
  },
  // Thresholds are defined relative to the GPU power limit (90%/95%), which
  // the API doesn't expose (gpu_power_watts is raw aggregate wattage, no
  // limit field) — informational only until we have that number.
  gpu_power_watts: { label: "GPU Power Consumption", sub: () => "Aggregate draw across tier GPUs" },
  // Green <85%, amber 85-95%, red >95%.
  gpu_memory_percent: {
    label: "GPU Memory Utilization",
    sub: () => "Avg framebuffer usage across tier GPUs",
    warningThreshold: 85,
    threshold: 95,
  },
};

const APPLICATION_METRIC_DEFS: Record<string, MetricDef> = {
  health: {
    label: "Health",
    sub: (s) => `${s.scope.name} · 1 = healthy, 0 = unhealthy`,
    lowThreshold: 1,
  },
  uptime_seconds: {
    label: "Uptime",
    sub: () => "Since the workload was created",
    format: (v) => ({ display: (v / 3600).toFixed(1), unit: "h" }),
  },
  replicas_running: { label: "Running Replicas", sub: () => "Ready replicas" },
  restarts: { label: "Restart Count", sub: () => "Restarts in selected window", threshold: 3 },
  cpu_cores: { label: "CPU Usage", sub: (s) => `${s.scope.name} · vCPU cores used` },
  memory_bytes: { label: "Memory Usage", sub: (s) => `${s.scope.name} · working set` },
  prompt_tokens_per_second: { label: "Prompt Tokens / Second", sub: () => "Informational" },
  completion_tokens_per_second: { label: "Completion Tokens / Second", sub: () => "Informational" },
  total_tokens_per_second: { label: "Total Tokens / Second", sub: () => "Informational" },
  llm_latency_seconds: {
    label: "LLM Latency",
    sub: () => "Mean response time",
    format: (v) => ({ display: (v * 1000).toFixed(0), unit: "ms" }),
    // Raw threshold is in seconds (2s = 2000ms) since points store raw API values.
    threshold: 2,
  },
  request_success_percent: { label: "Request Success Rate", sub: () => "2xx responses over selected window", lowThreshold: 99 },
  request_error_percent: { label: "Request Error Rate", sub: () => "5xx + timeouts over selected window", threshold: 1 },
};

function buildAppMetrics(
  level: string,
  series: MetricSeries,
  defs: Record<string, MetricDef>,
  range: TimeRangeKey,
): AppMetric[] {
  const now = Date.now();

  return Object.entries(series.metrics).map(([key, metric]: [string, MetricValue]) => {
    const def = defs[key];
    const key_ = historyKey(level, series.scope.id, key, range);
    const points = recordPoint(key_, now, metric.value);
    const format = def?.format ?? ((v: number) => formatByUnit(metric.unit, v));

    return {
      key,
      label: def?.label ?? key,
      sub: def?.sub(series) ?? "",
      points,
      format,
      threshold: def?.threshold,
      warningThreshold: def?.warningThreshold,
      lowThreshold: def?.lowThreshold,
      noWarning: def?.noWarning,
      sustainedMinutes: def?.sustainedMinutes,
      available: metric.available && metric.value !== null,
    };
  });
}

export type MetricsResult = {
  metrics: AppMetric[];
  /** true when some metrics failed upstream; the rest of the payload is still valid. */
  degraded: boolean;
  errors: MetricsError[];
  /** Seconds until the server-side cache for this query refreshes — drives polling. */
  refreshAfterSeconds: number;
};

const EMPTY_RESULT: MetricsResult = {
  metrics: [],
  degraded: false,
  errors: [],
  refreshAfterSeconds: 60,
};

/** Cluster-level metrics, global across all tiers — the same for every tier tab. */
export async function fetchClusterMetrics(range: TimeRangeKey): Promise<MetricsResult> {
  const response = await getMetricsV2({ level: "cluster", range });
  const series = response.series[0];
  if (!series) return EMPTY_RESULT;
  return {
    metrics: buildAppMetrics("cluster", series, CLUSTER_METRIC_DEFS, range),
    degraded: response.degraded,
    errors: response.errors,
    refreshAfterSeconds: response.refresh_after_seconds,
  };
}

/** Per-tier GPU metrics — each tier tab issues its own call, scoped with `tier`. */
export async function fetchTierMetrics(
  urlTier: number,
  range: TimeRangeKey,
): Promise<MetricsResult> {
  const tier = urlTier as 1 | 2 | 3 | 4;
  const response = await getMetricsV2({ level: "tier", tier, range });
  const series = response.series.find((s) => s.scope.tier === urlTier) ?? response.series[0];
  if (!series) return EMPTY_RESULT;
  return {
    metrics: buildAppMetrics("tier", series, TIER_METRIC_DEFS, range),
    degraded: response.degraded,
    errors: response.errors,
    refreshAfterSeconds: response.refresh_after_seconds,
  };
}

/**
 * Per-application metrics. The application level returns one series per
 * workload (81+ on a real cluster) with no tier filter, so we fetch the
 * full set and match by scope — `subdomain` (the k8s namespace / nodename)
 * plus `name`, since the same app name can exist under multiple clients
 * (see application.json: two "ada" series, different subdomains).
 */
export async function fetchAppMetrics(
  app: { name: string; nodename: string },
  range: TimeRangeKey,
): Promise<MetricsResult> {
  // Scoped server-side by `application` — the namespace/deployment id, same
  // shape as scope.id (e.g. "amberd-adrian-prerelease/ada") — instead of
  // fetching every workload and filtering client-side.
  const application = `${app.nodename}/${app.name}`;
  const response = await getApplicationMetricsV2(application, range);
  const series = response.series[0];
  if (!series) return EMPTY_RESULT;
  return {
    metrics: buildAppMetrics("application", series, APPLICATION_METRIC_DEFS, range),
    degraded: response.degraded,
    errors: response.errors,
    refreshAfterSeconds: response.refresh_after_seconds,
  };
}

export function formatMetricTime(ts: number, range: TimeRangeKey): string {
  const d = new Date(ts);
  if (["1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h"].includes(range)) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return (
    d.toLocaleDateString([], { month: "short", day: "numeric" }) +
    " " +
    d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  );
}
