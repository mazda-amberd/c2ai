import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";

import { ApiFetchError } from "@/api";
import TroubleshootingMetricChart from "./TroubleshootingMetricChart";
import {
  createTroubleshootingJob,
  downloadTroubleshootingPdf,
  getTroubleshootingJob,
  type TroubleshootingEvent,
  type TroubleshootingJobStatus,
  type TroubleshootingReport,
  type TroubleshootingRequest,
} from "@/api/services/troubleshooting";

const POLL_INTERVAL_MS = 2_500;

const PHASE_LABEL: Record<Exclude<TroubleshootingJobStatus, "completed" | "failed">, string> = {
  gathering_data: "Gathering application logs, metrics and Kubernetes events…",
  analyzing_data: "Analyzing collected data…",
  generating_recommendations: "Generating recommendations…",
};

const SEVERITY_TEXT: Record<string, { label: string; className: string }> = {
  critical: { label: "Critical:", className: "text-[#f0655f]" },
  warning: { label: "Warning:", className: "text-[#f2b84b]" },
  normal: { label: "Normal:", className: "text-[#4ade80]" },
};

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatStarted(iso: string): string {
  const d = new Date(iso);
  const diffMin = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
  const ago =
    diffMin < 60
      ? `${diffMin}m ago`
      : `${Math.floor(diffMin / 60)}h ${diffMin % 60}m ago`;
  return `Started ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} (${ago})`;
}

/** Amber pill for warning-and-above events, green for the rest — mirrors the
 * template's Warning/Normal event styling. */
function eventPillClass(ev: TroubleshootingEvent): string {
  const bad = ["warning", "error", "fatal", "critical"].includes(ev.severity);
  return bad
    ? "bg-[rgba(242,184,75,0.12)] text-[#f2b84b]"
    : "bg-[rgba(74,222,128,0.12)] text-[#4ade80]";
}

type Props = {
  request: TroubleshootingRequest;
};

export default function ApplicationTroubleshooting({ request }: Props) {
  const [phase, setPhase] = useState<TroubleshootingJobStatus>("gathering_data");
  const [report, setReport] = useState<TroubleshootingReport | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const cancelledRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const poll = useCallback((id: string) => {
    getTroubleshootingJob(id)
      .then((job) => {
        if (cancelledRef.current) return;
        setPhase(job.status);
        if (job.status === "completed" && job.report) {
          setReport(job.report);
        } else if (job.status === "failed") {
          setError(job.error?.message ?? "The troubleshooting job failed.");
        } else {
          timerRef.current = setTimeout(() => poll(id), POLL_INTERVAL_MS);
        }
      })
      .catch((err) => {
        if (cancelledRef.current) return;
        setError(
          err instanceof ApiFetchError ? err.message : "Failed to load the troubleshooting report",
        );
      });
  }, []);

  const startJob = useCallback(() => {
    setError(null);
    setReport(null);
    setPhase("gathering_data");
    createTroubleshootingJob(request)
      .then((job) => {
        if (cancelledRef.current) return;
        setJobId(job.job_id);
        setPhase(job.status);
        timerRef.current = setTimeout(() => poll(job.job_id), POLL_INTERVAL_MS);
      })
      .catch((err) => {
        if (cancelledRef.current) return;
        setError(
          err instanceof ApiFetchError ? err.message : "Failed to start the troubleshooting job",
        );
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.subdomain, request.deployment, poll]);

  useEffect(() => {
    cancelledRef.current = false;
    startJob();
    return () => {
      cancelledRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [startJob]);

  const handleDownload = () => {
    if (!jobId || downloading) return;
    setDownloading(true);
    downloadTroubleshootingPdf(jobId)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = [request.deployment, request.client, "ai-troubleshooting-report"]
          .filter(Boolean)
          .join("-") + ".pdf";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      })
      .catch(() => {
        /* surfaced via console by apiFetch; keep the page state intact */
      })
      .finally(() => setDownloading(false));
  };

  /* ---------------- Failed ---------------- */
  if (error) {
    return (
      <div className="pt-3.5 px-7 pb-10">
        <div className="rounded-[14px] border border-[rgba(240,101,95,0.4)] bg-[#0d1420] px-7 py-[26px]">
          <div className="mb-2 text-sm font-extrabold tracking-[1.2px] text-[#f0655f]">
            AI OPS — ROOT CAUSE ANALYSIS
          </div>
          <p className="text-[15px] text-[#eef2f6]">{error}</p>
          <button
            type="button"
            onClick={startJob}
            className="mt-4 rounded-md bg-chart-accent-dim px-3.5 py-2 text-sm font-semibold text-white transition-colors hover:bg-chart-accent"
          >
            Retry analysis
          </button>
        </div>
      </div>
    );
  }

  /* ---------------- Loading ---------------- */
  if (!report) {
    const activePhase = phase === "completed" || phase === "failed" ? "gathering_data" : phase;
    return (
      <div className="pt-3.5 px-7 pb-10">
        <div className="rounded-[14px] border border-[#1c2836] bg-[#0d1420] px-7 py-[26px]">
          <div className="mb-5 text-sm font-extrabold tracking-[1.2px] text-chart-accent">
            AI OPS — ROOT CAUSE ANALYSIS
          </div>

          <div className="mb-[18px] flex flex-wrap items-center gap-2.5">
            {["Application Logs", "Live Metrics", "Kubernetes Events"].map((label) => (
              <div
                key={label}
                className={`flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[13px] ${
                  activePhase === "gathering_data"
                    ? "border-[rgba(45,212,191,0.35)] bg-[rgba(45,212,191,0.12)] text-chart-accent"
                    : "border-[#1c2836] text-[#8b97a5]"
                }`}
              >
                {activePhase !== "gathering_data" && <span className="text-[#4ade80]">✓</span>}
                {label}
              </div>
            ))}
            <div className="text-[#57606c]">→</div>
            <div
              className={`flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[13px] ${
                activePhase !== "gathering_data"
                  ? "border-[rgba(45,212,191,0.35)] bg-[rgba(45,212,191,0.12)] text-chart-accent"
                  : "border-[#1c2836] text-[#8b97a5]"
              }`}
            >
              AI Analysis
            </div>
          </div>

          <div className="flex items-center gap-3 py-6 text-[15px] text-[#8b97a5]">
            <Loader2 className="h-5 w-5 animate-spin text-chart-accent" />
            {PHASE_LABEL[activePhase]}
          </div>
        </div>
      </div>
    );
  }

  /* ---------------- Report ---------------- */
  const severity = SEVERITY_TEXT[report.severity] ?? SEVERITY_TEXT.normal;

  return (
    // .page-content{padding:14px 28px 40px;}
    <div className="pt-3.5 px-7 pb-10">
      {/* AI OPS — Root Cause Analysis card */}
      <div className="rounded-[14px] border border-[#1c2836] bg-[#0d1420] px-7 py-[26px]">
        <div className="mb-5 flex items-center gap-3">
          <div className="text-sm font-extrabold tracking-[1.2px] text-chart-accent">
            AI OPS — ROOT CAUSE ANALYSIS
          </div>
          <button
            type="button"
            onClick={handleDownload}
            disabled={downloading}
            className="ml-auto flex items-center gap-1.5 rounded-lg border border-[#1c2836] bg-transparent px-3.5 py-[7px] text-[12.5px] font-semibold text-[#8b97a5] transition-colors hover:border-chart-accent hover:text-chart-accent disabled:opacity-50"
          >
            {downloading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            Download
          </button>
        </div>

        {/* Pipeline pill row — data sources actually analyzed */}
        <div className="mb-[18px] flex flex-wrap items-center gap-2.5">
          {["Application Logs", "Live Metrics", "Kubernetes Events"].map((label) => (
            <div
              key={label}
              className="flex items-center gap-1.5 rounded-full border border-[#1c2836] px-3.5 py-1.5 text-[13px] text-[#8b97a5]"
            >
              <span className="text-[#4ade80]">✓</span> {label}
            </div>
          ))}
          <div className="text-[#57606c]">→</div>
          <div className="flex items-center gap-1.5 rounded-full border border-[rgba(45,212,191,0.35)] bg-[rgba(45,212,191,0.12)] px-3.5 py-1.5 text-[13px] text-chart-accent">
            AI Analysis
          </div>
        </div>

        <div className="max-w-[1180px] text-[15.5px] leading-[1.65] text-[#eef2f6]">
          <b className={severity.className}>{severity.label}</b> {report.summary}
        </div>
        {report.most_likely_root_cause && (
          <div className="mt-2.5 max-w-[1180px] text-[14.5px] leading-[1.65] text-[#8b97a5]">
            <span className="font-semibold text-[#eef2f6]">Most likely root cause:</span>{" "}
            {report.most_likely_root_cause}
          </div>
        )}
        {report.issue_started && (
          <div className="mt-3.5 text-[13px] text-[#57606c]">
            {formatStarted(report.issue_started)}
          </div>
        )}

        {/* Recommended actions */}
        {report.recommended_actions.length > 0 && (
          <div className="mt-[22px] border-t border-[#16202c] pt-5">
            <div className="mb-3 text-[12.5px] font-extrabold tracking-[0.8px] text-[#8b97a5]">
              RECOMMENDED ACTIONS
            </div>
            <ul className="flex flex-col gap-2.5">
              {report.recommended_actions.map((action, i) => (
                <li key={i} className="flex gap-2.5 text-[14.5px] leading-normal text-[#eef2f6]">
                  <span className="mt-px flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-md bg-[rgba(45,212,191,0.12)] text-xs font-extrabold text-chart-accent">
                    {i + 1}
                  </span>
                  <span>{action}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Flagged metrics */}
      {report.application_metrics.length > 0 && (
        <>
          <div className="mb-3.5 mt-[34px] text-[13px] font-extrabold tracking-[1.2px] text-chart-accent">
            APPLICATION METRICS FLAGGED BY AI ({report.application_metrics.length})
          </div>
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
            {report.application_metrics.map((m) => (
              <div
                key={m.name}
                className="relative rounded-[14px] border border-[#1c2836] bg-[#0d1420] px-5 pb-3.5 pt-[18px]"
              >
                <div className="absolute right-[18px] top-[18px] h-2 w-2 rounded-full bg-[#34d399]" />
                <div className="mb-3 pr-4 text-[11px] font-extrabold uppercase tracking-[0.8px] text-[#8b97a5]">
                  {m.label}
                </div>
                <div className="text-2xl font-extrabold text-[#34d399]">
                  {m.value !== null ? m.value.toLocaleString() : "—"}
                  <span className="text-sm font-semibold text-[#8b97a5]">{m.unit}</span>
                </div>
                <TroubleshootingMetricChart metric={m} />
              </div>
            ))}
          </div>
        </>
      )}

      {/* Cluster events */}
      {report.cluster_events.length > 0 && (
        <>
          <div className="mb-3.5 mt-[34px] text-[13px] font-extrabold tracking-[1.2px] text-chart-accent">
            CLUSTER EVENTS
          </div>
          <div className="overflow-hidden rounded-[14px] border border-[#1c2836] bg-[#0d1420]">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-[13.5px]">
                <thead>
                  <tr>
                    {["TIMESTAMP", "RESOURCE", "EVENT TYPE", "REASON", "MESSAGE"].map((h) => (
                      <th
                        key={h}
                        className="border-b border-[#16202c] bg-[#0a0f18] px-5 py-3.5 text-left text-[11px] font-extrabold tracking-[0.8px] text-[#8b97a5]"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.cluster_events.map((ev) => (
                    <tr key={ev.id} className="hover:bg-white/[0.015]">
                      <td className="whitespace-nowrap border-b border-[#16202c] px-5 py-[13px] text-[13px] text-[#8b97a5]">
                        {formatTimestamp(ev.timestamp)}
                      </td>
                      <td className="border-b border-[#16202c] px-5 py-[13px] font-mono text-[12.5px] text-[#c9d4de]">
                        {ev.resource}
                      </td>
                      <td className="border-b border-[#16202c] px-5 py-[13px]">
                        <span
                          className={`inline-block rounded-full px-2.5 py-[3px] text-[11.5px] font-bold ${eventPillClass(ev)}`}
                        >
                          {ev.event_type}
                        </span>
                      </td>
                      <td className="border-b border-[#16202c] px-5 py-[13px] text-[#eef2f6]">
                        {ev.reason ?? "—"}
                      </td>
                      <td className="border-b border-[#16202c] px-5 py-[13px] text-[#eef2f6]">
                        {ev.message}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
