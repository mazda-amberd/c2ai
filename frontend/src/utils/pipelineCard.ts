import type { ElementType } from "react";
import { Rocket, RefreshCw, Trash2 } from "lucide-react";

import type { PipelineStatusRecord } from "@/types/deployment";

export type StatusVariant =
  | "queued"
  | "in_progress"
  | "success"
  | "failure"
  | "cancelled"
  | "unknown";

export function resolveVariant(record: PipelineStatusRecord): StatusVariant {
  const { gh_status, gh_conclusion } = record;
  if (gh_conclusion === "success") return "success";
  if (gh_conclusion === "failure" || gh_conclusion === "timed_out") return "failure";
  if (gh_conclusion === "cancelled") return "cancelled";
  if (gh_status === "in_progress") return "in_progress";
  if (gh_status === "queued" || gh_status === "waiting") return "queued";
  if (gh_status === null) return "queued"; // run_id not resolved yet
  return "unknown";
}

export const DEFAULT_OPERATION_ICON: ElementType = Rocket;

export const OPERATION_ICONS: Record<string, ElementType> = {
  deploy: Rocket,
  update: RefreshCw,
  terminate: Trash2,
};

export const OPERATION_LABELS: Record<string, string> = {
  deploy: "Deploying",
  update: "Updating",
  terminate: "Terminating",
};

export const STATUS_BADGE: Record<
  StatusVariant,
  { label: string; className: string }
> = {
  queued: {
    label: "Queued",
    className: "border-slate-500 bg-slate-500/10 text-slate-400",
  },
  in_progress: {
    label: "In progress",
    className: "border-blue-400 bg-blue-500/10 text-blue-400",
  },
  success: {
    label: "Done",
    className: "border-green-400 bg-green-500/10 text-green-400",
  },
  failure: {
    label: "Failed",
    className: "border-red-400 bg-red-500/10 text-red-400",
  },
  cancelled: {
    label: "Cancelled",
    className: "border-amber-400 bg-amber-500/10 text-amber-400",
  },
  unknown: {
    label: "Pending",
    className: "border-slate-500 bg-slate-500/10 text-slate-400",
  },
};
