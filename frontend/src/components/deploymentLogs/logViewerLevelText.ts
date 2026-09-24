import type { DeploymentLogEntry } from "@/types/logs";

const LEVEL_TEXT: Record<DeploymentLogEntry["level"], string> = {
  debug: "text-slate-400/80",
  trace: "text-slate-400/75",
  info: "text-slate-300/85",
  warning: "text-amber-200/70",
  error: "text-rose-300/75",
  fatal: "text-rose-200/72",
  critical: "text-orange-200/70",
};

export function levelTextClass(level: DeploymentLogEntry["level"]): string {
  return LEVEL_TEXT[level];
}
