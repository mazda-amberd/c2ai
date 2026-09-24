import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import MetricCard from "@components/MetricCard";
import { ApiFetchError } from "@/api";
import {
  fetchClusterMetrics,
  fetchTierMetrics,
  TIME_RANGE_OPTIONS,
  type AppMetric,
  type TimeRangeKey,
} from "@/utils/metricsApi";

const AUTO_REFRESH_MS = 30_000;

type View = "tier" | "cluster";

export default function ClusterMetrics() {
  const { tierIndex, view } = useParams<{ tierIndex: string; view?: string }>();
  const navigate = useNavigate();

  const urlTier = Number(tierIndex);
  const activeView: View = view === "cluster" ? "cluster" : "tier";
  const isCluster = activeView === "cluster";

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
    // Guard against the pre-redirect render with an invalid tier param
    // (the <Navigate> below fires after effects have already run once).
    if (isNaN(urlTier) || urlTier < 1 || urlTier > 4) return;
    if (timerRef.current) clearTimeout(timerRef.current);

    const request = isCluster
      ? fetchClusterMetrics(range)
      : fetchTierMetrics(urlTier, range);

    request
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
  }, [isCluster, urlTier, range]);

  useEffect(() => {
    cancelledRef.current = false;
    setLoading(true);
    loadMetrics();
    return () => {
      cancelledRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [loadMetrics]);

  if (isNaN(urlTier) || urlTier < 1 || urlTier > 4) {
    return <Navigate to="/" replace />;
  }

  const scopeLabel = isCluster ? "Cluster level" : `Tier ${urlTier} · Tier level`;

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
          <h1 className="text-[30px] font-bold text-chart-accent">
            {isCluster ? "Cluster Metrics" : `Tier ${urlTier} Metrics`}
          </h1>
        </div>
      </div>

      {/* .tabs{gap:26px;padding:18px 28px 0;} .tab{font-size:14px;font-weight:600;}
          Only this tier and Cluster — switching to a different tier means
          going back to that tier's page and clicking Tier Metrics there. */}
      <div className="pt-[18px] px-7 flex gap-[26px] border-b border-chart-panel-border">
        <button
          type="button"
          onClick={() => navigate(`/metrics/${urlTier}/tier`)}
          className={`pb-3 text-sm font-semibold transition-colors border-b-2 ${
            !isCluster
              ? "border-chart-accent text-chart-accent"
              : "border-transparent text-chart-muted hover:text-foreground"
          }`}
        >
          Tier
        </button>
        <button
          type="button"
          onClick={() => navigate(`/metrics/${urlTier}/cluster`)}
          className={`pb-3 text-sm font-semibold transition-colors border-b-2 ${
            isCluster
              ? "border-chart-accent text-chart-accent"
              : "border-transparent text-chart-muted hover:text-foreground"
          }`}
        >
          Cluster Metrics
        </button>
      </div>

      {/* .toolbar{gap:20px;border-radius:10px;padding:12px 18px;margin:20px 28px 0;} */}
      <div className="mt-5 mx-7 flex flex-wrap items-center gap-5 rounded-[10px] border border-chart-panel-border bg-chart-toolbar px-[18px] py-3">
        <div className="flex items-center gap-2.5">
          <label
            htmlFor="clusterTimeRange"
            className="text-xs uppercase tracking-[.06em] text-chart-muted"
          >
            Time Range
          </label>
          {/* select{padding:8px 12px;border-radius:6px;font-size:14px;min-width:150px;} */}
          <select
            id="clusterTimeRange"
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
        Showing {isCluster ? "cluster" : "GPU"} metrics · {scopeLabel}
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
      ) : (
        // .grid{gap:20px;padding:14px 28px 28px;}
        <div className="mt-3.5 grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-5 px-7 pb-7">
          {metrics.map((metric) => (
            <MetricCard key={metric.key} metric={metric} range={range} />
          ))}
        </div>
      )}
    </div>
  );
}
