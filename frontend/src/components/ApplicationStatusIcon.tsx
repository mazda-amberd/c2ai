import type { AppStatus } from "@/types/application";

const STATUS_CONFIG: Record<AppStatus, { badgeClass: string; label: string }> = {
  Healthy: {
    badgeClass: "bg-healthy text-healthy-text border-healthy-text/30",
    label: "Healthy",
  },
  Warning: {
    badgeClass: "bg-warning text-warning-text border-warning-text/30",
    label: "Warning",
  },
  Critical: {
    badgeClass: "bg-critical text-critical-text border-critical-text/30",
    label: "Critical",
  },
};

type ApplicationStatusIconProps = {
  status: AppStatus;
};

export default function ApplicationStatusIcon({ status }: ApplicationStatusIconProps) {
  const config = STATUS_CONFIG[status];

  return (
    <span
      className={`inline-flex items-center justify-center rounded border px-2.5 py-1 text-xs font-medium ${config.badgeClass}`}
    >
      {config.label}
    </span>
  );
}
