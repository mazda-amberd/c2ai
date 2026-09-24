import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";

import type { AppMetric, TimeRangeKey } from "@/utils/metricsApi";
import { formatMetricTime } from "@/utils/metricsApi";

const GREEN = "#34d399";
const AMBER = "#fbbf24";
const RED = "#f87171";

export type MetricHealth = "ok" | "warning" | "alert";

/** True once every point covering the trailing `minutes` has been at/above
 *  `threshold` — i.e. the breach has actually lasted that long, not just the
 *  latest sample. Requires history to actually span the full window; a
 *  freshly-opened page can't confirm "sustained" yet. */
function isSustainedAbove(points: AppMetric["points"], threshold: number, minutes: number): boolean {
  if (points.length === 0) return false;
  const windowMs = minutes * 60_000;
  const now = points[points.length - 1].timestamp;
  const windowPoints = points.filter((p) => now - p.timestamp <= windowMs);
  const earliest = windowPoints[0];
  if (!earliest || now - earliest.timestamp < windowMs) return false;
  return windowPoints.every((p) => p.value >= threshold);
}

export function metricHealth(metric: AppMetric): MetricHealth {
  if (!metric.available || metric.points.length === 0) return "ok";
  const last = metric.points[metric.points.length - 1].value;

  if (metric.threshold !== undefined) {
    if (last >= metric.threshold) {
      if (!metric.sustainedMinutes) return "alert";
      // Breached, but only confirmed critical once sustained — otherwise
      // treat it as a warning until it's held for the required duration.
      if (isSustainedAbove(metric.points, metric.threshold, metric.sustainedMinutes)) {
        return "alert";
      }
      return metric.noWarning ? "ok" : "warning";
    }
    const warnFloor = metric.warningThreshold ?? (metric.noWarning ? undefined : metric.threshold * 0.8);
    if (warnFloor !== undefined && last >= warnFloor) return "warning";
  }
  if (metric.lowThreshold !== undefined && last < metric.lowThreshold) {
    return "alert";
  }
  return "ok";
}

const HEALTH_COLOR: Record<MetricHealth, string> = {
  ok: GREEN,
  warning: AMBER,
  alert: RED,
};

/* Template: .card{background:var(--panel)} .card.ok only changes the border
 * (no tint) .card.alert{background:var(--red-bg)} — ok stays plain, only
 * alert (and our own warning extension, not in the template) get a tint. */
const HEALTH_TINT: Record<MetricHealth, string | null> = {
  ok: null,
  warning: "rgba(251,191,36,0.08)",
  alert: "rgba(248,113,113,0.12)",
};

type Props = {
  metric: AppMetric;
  range: TimeRangeKey;
};

export default function MetricCard({ metric, range }: Props) {
  const health = metricHealth(metric);
  const color = HEALTH_COLOR[health];
  const last = metric.points[metric.points.length - 1];

  // Per metrics-v2.md: null means "no data", never coalesce with 0.
  const showValue = metric.available && last !== undefined;
  const formatted = showValue ? metric.format(last.value) : null;

  const first = metric.points[0]?.timestamp ?? 0;
  const lastTs = last?.timestamp ?? 0;
  const gradientId = `metric-fill-${metric.key}`;

  // Recharts needs 2+ points to draw a line — with only one poll in, it
  // renders a dot instead of a flat line. Duplicate the single point for
  // the chart only; the axis labels below still reflect the one real
  // timestamp we have.
  const chartData = metric.points.length === 1 ? [metric.points[0], metric.points[0]] : metric.points;
  const tint = HEALTH_TINT[health];

  return (
    <div
      className="rounded-xl border p-5"
      style={{
        borderColor: color,
        background: tint
          ? `linear-gradient(${tint}, ${tint}), var(--chart-panel)`
          : "var(--chart-panel)",
      }}
    >
      <div className="mb-2.5 text-xs uppercase tracking-wider text-chart-muted">
        {metric.label}
      </div>
      <div className="flex items-baseline gap-1" style={{ color: showValue ? color : undefined }}>
        <span className={`text-[34px] font-extrabold leading-none ${showValue ? "" : "text-chart-muted"}`}>
          {showValue ? formatted!.display : "—"}
        </span>
        {showValue && formatted!.unit && (
          <span className="text-base font-semibold text-chart-muted">{formatted!.unit}</span>
        )}
      </div>
      <div className="mt-1 text-[13px] text-chart-muted">{metric.sub}</div>

      <div className="mt-3.5 h-20 w-full">
        {metric.points.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <YAxis hide domain={["dataMin", "dataMax"]} />
              <Area
                type="linear"
                dataKey="value"
                stroke={color}
                strokeWidth={2.5}
                strokeLinecap="round"
                fill={`url(#${gradientId})`}
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-chart-muted">
            No data yet
          </div>
        )}
      </div>

      <div className="mt-0.5 flex justify-between text-[11px] text-chart-axis">
        <span>{first ? formatMetricTime(first, range) : ""}</span>
        <span>{lastTs ? formatMetricTime(lastTs, range) : ""}</span>
      </div>
    </div>
  );
}
