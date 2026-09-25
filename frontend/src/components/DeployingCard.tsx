import { useState } from "react";
import {
  CircleHelp,
  ExternalLink,
  Loader2,
  X,
  XCircle,
  CheckCircle2,
} from "lucide-react";

import type { PipelineStatusRecord } from "@/types/deployment";
import type { AppStyle } from "@styles/appsColors";
import { cn } from "@/lib/utils";
import { DEPLOY_DOMAIN } from "@/constants/deployment";
import { deploymentPreviewUrl, splitWorkflowHostLabel } from "@/utils/subdomain";
import { isTerminal } from "@/utils/deploymentStatus";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@ui/tooltip";
import {
  resolveVariant,
  OPERATION_ICONS,
  OPERATION_LABELS,
  DEFAULT_OPERATION_ICON,
  type StatusVariant,
} from "@/utils/pipelineCard";
import { Button } from "@ui/button";

type CardVariantKey = "success" | "failure" | "cancelled" | "active";

/** Banner visuals live in `index.css` (`--pipeline-*` + `.pipeline-banner-*`). */
const BANNER_CLASS: Record<CardVariantKey, string> = {
  success: "pipeline-banner-success",
  failure: "pipeline-banner-danger",
  cancelled: "pipeline-banner-warning",
  active: "pipeline-banner-active",
};

const CARD_BORDER_VAR: Record<CardVariantKey, string | undefined> = {
  success: "var(--pipeline-success-ring)",
  failure: "var(--pipeline-danger-ring)",
  cancelled: "var(--pipeline-warning-ring)",
  active: undefined,
};

function cardVariantKey(variant: StatusVariant): CardVariantKey {
  if (variant === "success" || variant === "failure" || variant === "cancelled") {
    return variant;
  }
  return "active";
}

type DeployingCardProps = {
  deployment: PipelineStatusRecord;
  appStyle: AppStyle;
  /** When set and ``deployment.run_id`` is present, shows Cancel for in-flight runs. */
  onCancel?: () => void | Promise<void>;
  /** When set, a failed deployment's card can be removed from the page. */
  onDismiss?: () => void;
};

export default function DeployingCard({
  deployment,
  appStyle,
  onCancel,
  onDismiss,
}: DeployingCardProps) {
  const variant = resolveVariant(deployment);
  const opLabel = OPERATION_LABELS[deployment.operation] ?? "Running";
  const OpIcon = OPERATION_ICONS[deployment.operation] ?? DEFAULT_OPERATION_ICON;

  const bannerMessage =
    variant === "success"
      ? `${opLabel.replace(/ing$/, "ed")} successfully`
      : variant === "failure"
        ? `${opLabel} failed — check GitHub Actions`
        : variant === "cancelled"
          ? "Operation cancelled"
          : `${opLabel}…`;

  const styleKey = cardVariantKey(variant);
  const cardBorderVar = CARD_BORDER_VAR[styleKey];

  const showBannerSpinner =
    variant === "in_progress" || variant === "queued" || variant === "unknown";

  const previewUrl = deploymentPreviewUrl(deployment.subdomain, DEPLOY_DOMAIN);
  const displayName =
    deployment.application_name?.trim() || splitWorkflowHostLabel(deployment.subdomain).customer;

  const runEnded = isTerminal(deployment);
  const isInProgress = variant === "in_progress" || variant === "queued";

  const [isCancelling, setIsCancelling] = useState(false);
  const showCancel =
    Boolean(onCancel) && isInProgress && deployment.run_id != null;

  const handleCancel = () => {
    if (!onCancel || isCancelling) return;
    setIsCancelling(true);
    void Promise.resolve(onCancel()).finally(() => {
      setIsCancelling(false);
    });
  };

  return (
    <div
      className={[
        "p-6 rounded-2xl w-full min-h-[28rem] border-2 flex flex-col relative overflow-hidden",
        isInProgress ? "animate-pulse" : "",
        runEnded ? "opacity-60" : "opacity-80",
      ]
        .filter(Boolean)
        .join(" ")}
      style={{
        background: appStyle.cardBackground,
        borderColor: runEnded && cardBorderVar != null ? cardBorderVar : appStyle.border,
        boxShadow: appStyle.shadow,
      }}
    >
      {/* Action name — same pattern as update/terminate instance cards */}
      <div
        className={cn(
          "absolute inset-x-0 top-0 flex items-center justify-center gap-2 py-1.5 text-xs font-medium",
          BANNER_CLASS[styleKey],
        )}
      >
        {showBannerSpinner && (
          <Loader2 className="h-3 w-3 animate-spin shrink-0" />
        )}
        {variant === "success" && (
          <CheckCircle2 className="h-3 w-3 shrink-0 opacity-90" />
        )}
        {(variant === "failure" || variant === "cancelled") && (
          <XCircle className="h-3 w-3 shrink-0 opacity-90" />
        )}
        <span>{bannerMessage}</span>
      </div>
      {onDismiss && variant === "failure" && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss failed deployment"
          title="Remove from page"
          className="absolute right-2 top-1 z-10 flex h-5 w-5 items-center justify-center rounded text-foreground/70 transition-colors hover:bg-white/10 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}

      {/* ── Header ── */}
      <div className="flex-1 space-y-3 mt-6">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {!isInProgress && (
              <OpIcon className="h-4 w-4 shrink-0 text-slate-400" />
            )}
            <h3
              className="font-bold text-xl line-clamp-2 break-words"
              style={{ color: appStyle.text }}
            >
              {displayName}
            </h3>
          </div>
        </div>

        <p className="text-base text-slate-400">{deployment.subdomain}</p>

        {/* Preview URL */}
        <div className="flex items-center gap-1">
          <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
            Preview URL
          </p>
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="inline-flex text-slate-500 hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 rounded-sm"
                  aria-label="How this preview is computed"
                >
                  <CircleHelp className="h-3 w-3" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-[240px] leading-snug">
                Same host pattern as the devops workflow. Live DNS may differ.
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
        <p className="text-xs text-slate-500 break-all font-mono">{previewUrl}</p>

        {/* Branch + triggered by */}
        <p className="text-sm text-slate-500">
          {deployment.branch && <span>{deployment.branch}</span>}
          {deployment.branch && (
            <span className="mx-1 text-slate-600">·</span>
          )}
          <span className="text-slate-600">{deployment.triggered_by}</span>
        </p>
      </div>

      {/* ── Pipeline status area ── */}
      <div className="flex flex-col items-center justify-center flex-1 gap-3 py-4">
        {/* Spinner / icon */}
        {isInProgress && (
          <Loader2 className="h-10 w-10 animate-spin text-blue-400 opacity-60" />
        )}
        {variant === "success" && (
          <CheckCircle2 className="h-10 w-10 text-green-400 opacity-80" />
        )}
        {(variant === "failure") && (
          <XCircle className="h-10 w-10 text-red-400 opacity-80" />
        )}
        {variant === "cancelled" && (
          <XCircle className="h-10 w-10 text-amber-400 opacity-80" />
        )}

        {/* Active job / step */}
        {deployment.active_job && isInProgress && (
          <div className="text-center space-y-0.5">
            <p className="text-xs font-medium text-blue-300">
              {deployment.active_job}
            </p>
            {deployment.current_step && (
              <p className="text-[11px] text-slate-500">
                {deployment.current_step}
              </p>
            )}
          </div>
        )}

        {/* GH run link */}
        {deployment.run_url && (
          <a
            href={deployment.run_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-[11px] text-slate-500 hover:text-slate-300 transition-colors"
          >
            <ExternalLink className="h-3 w-3" />
            View on GitHub
          </a>
        )}
      </div>

      {showCancel && (
        <div className="mt-auto flex justify-center pt-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="text-xs border-slate-600 text-slate-300 hover:bg-slate-800"
            disabled={isCancelling}
            onClick={(e) => {
              e.stopPropagation();
              handleCancel();
            }}
          >
            {isCancelling ? "Cancelling…" : "Cancel run"}
          </Button>
        </div>
      )}
    </div>
  );
}
