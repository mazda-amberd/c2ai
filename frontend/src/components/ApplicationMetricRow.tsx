import { getMetricBarColor, getMetricNumberColor } from "@/utils/metrics";
import { getMetricIcon, type MetricKey } from "@/utils/metricAssets";

type Props = {
  metric: MetricKey;
  label: string;
  value: number;
  tierIndex: number;
};

export default function ApplicationMetricRow({ metric, label, value, tierIndex }: Props) {
  const icon = getMetricIcon(metric, value, tierIndex);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <img src={icon} alt={label} className="w-4 h-4 object-contain" />
          <span className="text-sm text-slate-400">{label}</span>
        </div>
        <span className={`text-xs font-semibold ${getMetricNumberColor(value)}`}>
          {value}%
        </span>
      </div>
      <div className="w-full bg-slate-700/50 rounded-full h-2 overflow-hidden">
        <div
          className={`h-2 rounded-full transition-all duration-500 ${getMetricBarColor(value)}`}
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  );
}
