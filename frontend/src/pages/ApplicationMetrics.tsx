import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import MetricCard from "@components/MetricCard";
import ApplicationTroubleshooting from "@components/ApplicationTroubleshooting";
import { ApiFetchError } from "@/api";
import type { Application, AppStatus } from "@/types/application";
import {
  fetchAppMetrics,
  TIME_RANGE_OPTIONS,
  type AppMetric,
  type TimeRangeKey,
} from "@/utils/metricsApi";

const AUTO_REFRESH_MS = 30_000;

/* Exact colors from the template's inline STATUS_PILL map — healthy uses a
 * solid dark border (#14532d), not an alpha one, while warning/critical use
 * .35-alpha borders. Kept as literal values (not the shared status tokens)
 * so this page matches the template byte-for-byte. */
const STATUS_PILL_CLASS: Record<AppStatus, string> = {
  Healthy: "bg-[rgba(52,211,153,.12)] text-[#34d399] border-[#14532d]",
  Warning: "bg-[rgba(251,191,36,.12)] text-[#fbbf24] border-[rgba(251,191,36,.35)]",
  Critical: "bg-[rgba(248,113,113,.12)] text-[#f87171] border-[rgba(248,113,113,.35)]",
};

/* Template's .pill-muted — used by the version and client tags. */
const MUTED_PILL_CLASS =
  "rounded-full border border-chart-panel-border bg-[#1a212c] px-2.5 py-1 text-xs font-semibold text-chart-muted";

export default function ApplicationMetrics() {
  const { tierIndex, appName } = useParams<{
    tierIndex: string;
    appName: string;
  }>();
  const location = useLocation();
  const app = (location.state as { app?: Application } | null)?.app;

  const urlTier = Number(tierIndex);

  const status: AppStatus = app?.status ?? "Healthy";

  const [tab, setTab] = useState<"Metrics" | "Troubleshooting">("Metrics");
  // Becomes true on the first Troubleshooting click — that's when the job
  // API call fires; the component then stays mounted across tab switches.
  const [troubleshootingOpened, setTroubleshootingOpened] = useState(false);
  const [range, setRange] = useState<TimeRangeKey>("6h");
  const [metrics, setMetrics] = useState<AppMetric[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Flipped on unmount / param change so an in-flight fetch can't apply its
  // result or schedule another poll for a page that's no longer showing.
  const cancelledRef = useRef(false);

  const loadMetrics = useCallback(() => {
    if (!appName || !app?.nodename) {
      setLoading(false);
      return;
    }
    if (timerRef.current) clearTimeout(timerRef.current);

    fetchAppMetrics({ name: appName, nodename: app.nodename }, range)
      .then((result) => {
        if (cancelledRef.current) return;
        setMetrics(result.metrics);
        setDegraded(result.degraded);
        setError(null);
        setLoading(false);
        timerRef.current = setTimeout(loadMetrics, AUTO_REFRESH_MS);
      })
      .catch((err) => {
        if (cancelledRef.current) return;
        const message =
          err instanceof ApiFetchError ? err.message : "Failed to load metrics";
        setError(message);
        setLoading(false);
        timerRef.current = setTimeout(loadMetrics, AUTO_REFRESH_MS);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appName, app?.nodename, range]);

  useEffect(() => {
    cancelledRef.current = false;
    setLoading(true);
    loadMetrics();
    return () => {
      cancelledRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [loadMetrics]);

  if (isNaN(urlTier) || urlTier < 1 || urlTier > 4 || !appName) {
    return <Navigate to="/" replace />;
  }

  const instanceLabel = app?.instance_name ?? `${appName.toLowerCase()}-primary`;

  return (
    <div>
      {/* .header{padding:20px 28px 0;} */}
      <div className="pt-5 px-7">
        <Link
          to={`/apps/${urlTier}`}
          className="inline-flex items-center gap-1.5 text-[13px] text-chart-muted transition-colors hover:text-chart-accent"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Tier {urlTier}
        </Link>

        {/* .titlerow{gap:12px;margin-top:10px;} h1{font-size:30px;color:var(--teal);} */}
        <div className="mt-2.5 flex items-center gap-3">
          <h1 className="text-[30px] font-bold text-chart-accent">{appName}</h1>
          {/* .pill{font-size:12px;font-weight:600;padding:4px 10px;border-radius:20px;} */}
          <span
            className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${STATUS_PILL_CLASS[status]}`}
          >
            {status}
          </span>
          {app?.version && <span className={MUTED_PILL_CLASS}>{app.version}</span>}
          {app?.client_name && <span className={MUTED_PILL_CLASS}>{app.client_name}</span>}
          {app?.instance_name && <span className={MUTED_PILL_CLASS}>{app.instance_name}</span>}
        </div>
      </div>

      {/* .tabs{gap:26px;padding:18px 28px 0;} .tab{font-size:14px;font-weight:600;} */}
      <div className="pt-[18px] px-7 flex gap-[26px] border-b border-chart-panel-border">
        {(["Metrics", "Troubleshooting"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => {
              setTab(t);
              if (t === "Troubleshooting") setTroubleshootingOpened(true);
            }}
            className={`pb-3 text-sm font-semibold transition-colors border-b-2 ${
              tab === t
                ? "border-chart-accent text-chart-accent"
                : "border-transparent text-chart-muted hover:text-foreground"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Mounted on first open and kept alive (hidden) afterwards, so
          switching tabs doesn't restart the analysis job. */}
      {troubleshootingOpened && app?.nodename && (
        <div className={tab === "Troubleshooting" ? "" : "hidden"}>
          <ApplicationTroubleshooting
            request={{
              subdomain: app.nodename,
              deployment: appName,
              tier: urlTier,
              status,
              version: app.version ?? undefined,
              instance: app.instance_name ?? undefined,
              client: app.client_name ?? undefined,
            }}
          />
        </div>
      )}

      {tab === "Troubleshooting" && !app?.nodename && (
        <div className="flex justify-center py-24 px-7 text-center text-muted-foreground">
          Open this page from the Applications list to run troubleshooting —
          direct links need the app's client/instance to identify the deployment.
        </div>
      )}

      {tab === "Metrics" && (
        <>
      {/* .toolbar{gap:20px;border-radius:10px;padding:12px 18px;margin:20px 28px 0;} */}
      <div className="mt-5 mx-7 flex flex-wrap items-center gap-5 rounded-[10px] border border-chart-panel-border bg-chart-toolbar px-[18px] py-3">
        <div className="flex items-center gap-2.5">
          <label
            htmlFor="metricsTimeRange"
            className="text-xs uppercase tracking-[.06em] text-chart-muted"
          >
            Time Range
          </label>
          {/* select{padding:8px 12px;border-radius:6px;font-size:14px;min-width:150px;} */}
          <select
            id="metricsTimeRange"
            value={range}
            onChange={(e) => setRange(e.target.value as TimeRangeKey)}
            className="min-w-[150px] rounded-md border border-chart-panel-border bg-chart-panel px-3 py-2 text-sm text-[#e6edf3]"
          >
            {TIME_RANGE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {/* .refresh-status{gap:6px;font-size:13px;padding-left:20px;} .dot{width/height:8px;} */}
        <div className="flex items-center gap-1.5 border-l border-chart-panel-border pl-5 text-[13px] text-chart-muted">
          <span className="h-2 w-2 rounded-full bg-chart-healthy" />
          Auto-refresh 30s (fixed)
        </div>

        {/* .btn{padding:8px 14px;border-radius:6px;font-weight:600;font-size:14px;} */}
        <button
          type="button"
          onClick={loadMetrics}
          className="ml-auto rounded-md bg-chart-accent-dim px-3.5 py-2 text-sm font-semibold text-white transition-colors hover:bg-chart-accent"
        >
          Refresh now
        </button>
      </div>

      {/* .subtext{padding:0 28px;margin-top:14px;font-size:13px;} */}
      <p className="mt-3.5 px-7 text-[13px] text-chart-muted">
        Showing metrics for {appName} — {instanceLabel}
      </p>

      {error && (
        <div className="mt-3.5 mx-7 rounded-lg border border-critical-text/30 bg-critical px-4 py-2.5 text-[13px] text-critical-text">
          Unable to load metrics: {error}
        </div>
      )}

      {!error && degraded && (
        <div className="mt-3.5 mx-7 rounded-lg border border-warning-text/30 bg-warning px-4 py-2.5 text-[13px] text-warning-text">
          Some metrics are temporarily unavailable — showing the rest.
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-24 text-muted-foreground">
          Loading metrics…
        </div>
      ) : !app?.nodename ? (
        <div className="flex justify-center py-24 text-center text-muted-foreground">
          Open this page from the Applications list to see live metrics —
          direct links need the app's client/instance to look up its metrics.
        </div>
      ) : (
        // .grid{gap:20px;padding:14px 28px 28px;}
        <div className="mt-3.5 grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-5 px-7 pb-7">
          {metrics.map((metric) => (
            <MetricCard key={metric.key} metric={metric} range={range} />
          ))}
        </div>
      )}
        </>
      )}
    </div>
  );
}
