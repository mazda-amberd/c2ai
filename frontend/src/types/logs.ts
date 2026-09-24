export type DeploymentLogLevel =
  | "debug"
  | "trace"
  | "info"
  | "warning"
  | "error"
  | "fatal"
  | "critical";

/** One structured log line from GET /api/logs/deployment (Grafana Loki). */
export type DeploymentLogEntry = {
  /** Stable id for row selection and deduplication (content hash). */
  id: string;
  timestamp: string;
  level: DeploymentLogLevel;
  /** e.g. container name from Loki labels */
  logType: string;
  user: string;
  /** Instance / namespace (best-effort from labels). */
  db: string;
  /** Application / workload name (best-effort). */
  app: string;
  /** e.g. pod name from Loki labels */
  client: string;
  role: string;
  /** Milliseconds when present in the log line text */
  durationMs: number | null;
  connectionId: string;
  message: string;
  /** Full Loki stream / line labels when available. */
  labels?: Record<string, string>;
};

export type DeploymentLogsResponse = {
  entries: DeploymentLogEntry[];
  source: "loki";
  has_more?: boolean;
  next_cursor?: string | null;
};

/** Toolbar time mode for the deployment log viewer. */
export type LogTimePreset =
  | "live"
  | "15m"
  | "1h"
  | "4h"
  | "24h"
  | "7d"
  | "custom";

export type GetDeploymentLogsParams = {
  subdomain: string;
  /** Same as application name in the deployments list (LogQL deployment matcher). */
  deployment: string;
  /** 1-based tier from route `/apps/:tierIndex`; optional when unknown. */
  tier?: number;
  /** ISO8601 lower bound (optional; server defaults to last 15m if both omitted). */
  from?: string;
  /** ISO8601 upper bound (optional). */
  to?: string;
  /** Server-side search / level tokens (LogQL); optional. */
  search?: string;
  /** When set with optional ``cursor``, enables paged Loki queries (forward). */
  limit?: number;
  /** Continuation token from the previous response's ``next_cursor``. */
  cursor?: string;
  /** Backward paging (live tail); see backend ``tail`` query param. */
  tail?: boolean;
};

export type DeploymentLogsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  subdomain: string;
  rayAppName: string;
  tier?: number;
};
