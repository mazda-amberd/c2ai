import type { DeploymentLogEntry } from "@/types/logs";
import { cn } from "@/lib/utils";

const LEVEL_DOT: Record<DeploymentLogEntry["level"], string> = {
  debug: "bg-slate-500/40 ring-1 ring-slate-400/15",
  trace: "bg-slate-400/35 ring-1 ring-slate-300/12",
  info: "bg-slate-400/45 ring-1 ring-slate-300/15",
  warning: "bg-amber-500/40 ring-1 ring-amber-400/20",
  error: "bg-rose-500/45 ring-1 ring-rose-400/25",
  fatal: "bg-rose-600/40 ring-1 ring-rose-500/22",
  critical: "bg-orange-600/38 ring-1 ring-orange-500/20",
};

export function LevelDot({ level }: { level: DeploymentLogEntry["level"] }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        LEVEL_DOT[level],
      )}
      title={level}
    />
  );
}

export function MetaRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,5.5rem)_1fr] gap-x-2 gap-y-0.5">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="break-all font-mono text-[11px] text-foreground/90">
        {value}
      </dd>
    </div>
  );
}
