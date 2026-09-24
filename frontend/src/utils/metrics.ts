import type { AppStatus } from "@/types/application";

export function getMetricBarColor(value: number): string {
  if (value >= 76) return "bg-critical-text";
  if (value <= 50) return "bg-healthy-text";
  return "bg-warning-text";
}

export function getMetricNumberColor(value: number): string {
  if (value >= 76) return "text-critical-text";
  if (value <= 50) return "text-healthy-text";
  return "text-warning-text";
}

export function getStatusColor(status: AppStatus | string): string {
  switch (status) {
    case "Healthy":
      return "bg-healthy text-healthy-text border-healthy-text";
    case "Warning":
      return "bg-warning text-warning-text border-warning-text";
    case "Critical":
      return "bg-critical text-critical-text border-critical-text";
    default:
      return "";
  }
}
