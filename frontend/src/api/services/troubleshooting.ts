import { apiFetch, apiFetchBlob } from "..";

/* Contracts mirroring backend/src/service/schemas/troubleshooting.py */

export type TroubleshootingJobStatus =
  | "gathering_data"
  | "analyzing_data"
  | "generating_recommendations"
  | "completed"
  | "failed";

export type TroubleshootingSeverity = "critical" | "warning" | "normal";

export type TroubleshootingEvent = {
  id: string;
  timestamp: string;
  source: "application" | "kubernetes";
  severity:
    | "debug"
    | "trace"
    | "info"
    | "warning"
    | "error"
    | "fatal"
    | "critical";
  resource: string;
  event_type: string;
  reason: string | null;
  message: string;
  attributes: Record<string, string>;
};

export type TroubleshootingMetric = {
  name: string;
  label: string;
  value: number | null;
  unit: string;
  /** No longer rendered by the backend; always null. */
  plot_data_url: string | null;
  /** Samples over the report window, oldest first. */
  points?: { timestamp: string; value: number }[];
};

export type TroubleshootingWindow = {
  start: string;
  end: string;
  hours: number;
};

export type TroubleshootingReport = {
  generated_at: string;
  window: TroubleshootingWindow;
  severity: TroubleshootingSeverity;
  summary: string;
  most_likely_root_cause: string;
  issue_started: string | null;
  most_critical_event: TroubleshootingEvent | null;
  recommended_actions: string[];
  relevant_events: TroubleshootingEvent[];
  cluster_events: TroubleshootingEvent[];
  application_metrics: TroubleshootingMetric[];
  analyzed_log_lines: number;
  data_sources: Array<"application" | "kubernetes">;
};

export type TroubleshootingJob = {
  job_id: string;
  status: TroubleshootingJobStatus;
  created_at: string;
  updated_at: string;
  report: TroubleshootingReport | null;
  error: { code: string; message: string } | null;
};

export type TroubleshootingRequest = {
  subdomain: string;
  deployment: string;
  tier?: number | null;
  status?: string | null;
  version?: string | null;
  instance?: string | null;
  client?: string | null;
};

/** Start an asynchronous troubleshooting job; poll it with getTroubleshootingJob. */
export const createTroubleshootingJob = async (
  request: TroubleshootingRequest,
): Promise<TroubleshootingJob> => {
  return await apiFetch<TroubleshootingJob>("/jobs", {
    method: "POST",
    body: JSON.stringify(request),
  });
};

export const getTroubleshootingJob = async (
  jobId: string,
): Promise<TroubleshootingJob> => {
  return await apiFetch<TroubleshootingJob>(`/jobs/${encodeURIComponent(jobId)}`);
};

/** Backend-rendered PDF of a completed job's report. */
export const downloadTroubleshootingPdf = async (
  jobId: string,
): Promise<Blob> => {
  return await apiFetchBlob(`/jobs/${encodeURIComponent(jobId)}/report.pdf`, {
    headers: { Accept: "application/pdf" },
  });
};
