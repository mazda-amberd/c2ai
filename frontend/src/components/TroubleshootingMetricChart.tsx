import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";

import type { TroubleshootingMetric } from "@/api/services/troubleshooting";
import {
  CHART_TOOLTIP_CURSOR,
  CHART_TOOLTIP_LABEL_STYLE,
  CHART_TOOLTIP_STYLE,
} from "@styles/chartTooltip";

const LINE_COLOR = "#34d399";

function formatClock(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** One flagged metric's samples over the report window. */
export default function TroubleshootingMetricChart({ metric }: { metric: TroubleshootingMetric }) {
  const data = (metric.points ?? []).map((point) => ({
    timestamp: new Date(point.timestamp).getTime(),
    value: point.value,
  }));
  if (data.length === 0) {
    return (
      <div className="mt-2 flex h-[46px] items-center justify-center text-[11px] text-[#57606c]">
        No metric data available
      </div>
    );
  }

  // Recharts needs two points to draw a line.
  const chartData = data.length === 1 ? [data[0], data[0]] : data;
  const values = data.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const format = (value: number) => `${value.toLocaleString()}${metric.unit ? ` ${metric.unit}` : ""}`;

  return (
    <>
      <div className="mt-3.5 h-20 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
            {/* A flat series would otherwise sit on the chart's bottom edge. */}
            <YAxis hide domain={min === max ? [min - 1, max + 1] : ["dataMin", "dataMax"]} />
            <Tooltip
              cursor={CHART_TOOLTIP_CURSOR}
              isAnimationActive={false}
              contentStyle={CHART_TOOLTIP_STYLE}
              labelStyle={CHART_TOOLTIP_LABEL_STYLE}
              itemStyle={{ color: LINE_COLOR, padding: 0, fontWeight: 600 }}
              separator=""
              labelFormatter={(_label, payload) => {
                const timestamp = payload?.[0]?.payload?.timestamp;
                return timestamp ? formatClock(timestamp) : "";
              }}
              formatter={(value) => [format(Number(value)), ""]}
            />
            <Area
              type="linear"
              dataKey="value"
              stroke={LINE_COLOR}
              strokeWidth={2.5}
              strokeLinecap="round"
              fill="none"
              isAnimationActive={false}
              dot={false}
              activeDot={{ r: 4, fill: LINE_COLOR, stroke: "#0d1420", strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-0.5 flex justify-between text-[11px] text-chart-axis">
        <span>{formatClock(data[0].timestamp)}</span>
        <span>{formatClock(data[data.length - 1].timestamp)}</span>
      </div>
    </>
  );
}
