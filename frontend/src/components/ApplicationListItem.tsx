import { useEffect, useState } from "react";
import {
  ArrowRightLeft,
  Bomb,
  ExternalLink,
  Loader2,
  MoreVertical,
  RefreshCw,
  ScrollText,
} from "lucide-react";

import TierMiniCube from "./TierMiniCube";
import ApplicationStatusIcon from "./ApplicationStatusIcon";
import ApplicationMetricRow from "./ApplicationMetricRow";
import type { PipelineStatusRecord } from "@/types/deployment";
import type { Application } from "@/types/application";
import { appsColors } from "@styles/appsColors";
import DeploymentLogsDialog from "./DeploymentLogsDialog";
import { useNavigate } from "react-router-dom";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@ui/dropdown-menu";
import { Button } from "@ui/button";
import { cn } from "@/lib/utils";
import CostAmount from "@components/CostAmount";
import { DEPLOY_DOMAIN } from "@/constants/deployment";
import { deploymentPreviewUrl } from "@/utils/subdomain";
import {
  fetchAppCost,
  isCostAvailable,
  useFinanceFilter,
  type CostValue,
} from "@/utils/financeApi";

type Props = {
  app: Application;
  tierIndex: number;
  onMoveTier: () => void;
  onUpdate: () => void;
  onBomb: () => void;
  isMigrating?: boolean;
  isTerminating?: boolean;
  isUpdating?: boolean;
  migrationPipeline?: PipelineStatusRecord | null;
  updatePipeline?: PipelineStatusRecord | null;
  terminatePipeline?: PipelineStatusRecord | null;
  onCancelPipeline?: () => void | Promise<void>;
};

export default function ApplicationListItem({
  app,
  tierIndex,
  onMoveTier,
  onUpdate,
  onBomb,
  isMigrating = false,
  isTerminating = false,
  isUpdating = false,
  migrationPipeline = null,
  updatePipeline = null,
  terminatePipeline = null,
  onCancelPipeline,
}: Props) {
  const appsStyle = appsColors[tierIndex] || appsColors[0];
  const isOperating = isMigrating || isTerminating || isUpdating;

  const [isCancelling, setIsCancelling] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);

  // Per-app cost for the shared finance date filter (Epic 10).
  const [financeFilter] = useFinanceFilter();
  const [appCost, setAppCost] = useState<CostValue | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchAppCost(`${app.nodename}/${app.name}`, financeFilter, tierIndex + 1).then((cost) => {
      if (!cancelled) setAppCost(cost);
    });
    return () => {
      cancelled = true;
    };
  }, [app.nodename, app.name, financeFilter, tierIndex]);

  const pipelineProgress: PipelineStatusRecord | null = isTerminating
    ? terminatePipeline
    : isMigrating
      ? migrationPipeline
      : isUpdating
        ? updatePipeline
        : null;
  const pipelineJobClass = isTerminating
    ? "text-[var(--pipeline-danger-fg)]"
    : "text-[var(--pipeline-active-fg)]";

  const showCancelRun =
    Boolean(onCancelPipeline) &&
    isOperating &&
    pipelineProgress?.run_id != null;

  const handleCancelRun = () => {
    if (!onCancelPipeline || isCancelling) return;
    setIsCancelling(true);
    void Promise.resolve(onCancelPipeline()).finally(() => {
      setIsCancelling(false);
    });
  };

  // Instances deployed by the ada workflows are served at their host label;
  // others at their application name.
  const appUrl = deploymentPreviewUrl(
    app.nodename.startsWith("amberd-") ? app.nodename : app.name,
    DEPLOY_DOMAIN,
  );

  const navigate = useNavigate();

  const urlTier = tierIndex + 1;

  const handleCardClick = () => {
    if (isOperating) return;
    navigate(`/apps/${urlTier}/${encodeURIComponent(app.name)}/metrics`, {
      state: { app },
    });
  };

  return (
    <>
      <div
        onClick={handleCardClick}
        className={[
          "p-6 rounded-2xl w-full min-h-[28rem] border-2 flex flex-col transition-all relative overflow-hidden",
          isOperating
            ? "animate-pulse opacity-60 cursor-default"
            : "cursor-pointer hover:-translate-y-1 hover:shadow-lg",
        ].join(" ")}
        style={{
          background: appsStyle.cardBackground,
          borderColor: isTerminating
            ? "var(--pipeline-danger-ring-strong)"
            : isMigrating || isUpdating
              ? "var(--pipeline-active-ring-strong)"
              : appsStyle.border,
          boxShadow: appsStyle.shadow,
        }}
      >
        {isOperating && (
          <div
            className={cn(
              "absolute inset-x-0 top-0 flex items-center justify-center gap-2 py-1.5 text-xs font-medium",
              isTerminating ? "pipeline-banner-danger" : "pipeline-banner-active",
            )}
          >
            <Loader2 className="h-3 w-3 animate-spin" />
            {isTerminating ? "Terminating…" : isMigrating ? "Migrating…" : "Updating…"}
          </div>
        )}

        <div className={`flex-1 ${isOperating ? "mt-6" : ""}`}>
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between gap-2">
              <p
                className="font-bold text-[32px] line-clamp-1 break-words min-w-0 flex-1"
                style={{ color: appsStyle.text }}
                title={app.name}
              >
                {app.name}
              </p>
              <div className="flex items-center gap-1.5 shrink-0">
                {!isOperating && <ApplicationStatusIcon status={app.status} />}

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    disabled={isOperating}
                    onClick={(e) => e.stopPropagation()}
                    className="flex h-6 w-6 items-center justify-center rounded text-foreground bg-white/[0.07] hover:bg-transparent transition-colors -mr-1 disabled:opacity-30 disabled:cursor-not-allowed"
                    aria-label="Deployment actions"
                  >
                    <MoreVertical className="h-4 w-4" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                  <DropdownMenuItem
                    onSelect={() => window.open(appUrl, "_blank", "noopener,noreferrer")}
                  >
                    <ExternalLink className="h-4 w-4" />
                    Go to URL
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={onMoveTier} disabled={isOperating}>
                    <ArrowRightLeft className="h-4 w-4" />
                    Move to Tier
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={onUpdate} disabled={isOperating}>
                    <RefreshCw className="h-4 w-4" />
                    Update
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    disabled={isOperating}
                    onSelect={() => setLogsOpen(true)}
                  >
                    <ScrollText className="h-4 w-4" />
                    View logs
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onSelect={onBomb}
                    className="text-destructive focus:text-destructive"
                  >
                    <Bomb className="h-4 w-4" />
                    Terminate
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              </div>
            </div>
            {/* Template's .app-meta-row: version tag left, cost right
                (sitting under the health badge). */}
            {(app.version || appCost !== null) && (
              <div className="flex items-center justify-between gap-2">
                {app.version ? (
                  <span
                    className="text-xs px-1.5 py-0.5 rounded border"
                    style={{ color: appsStyle.text, borderColor: appsStyle.text, backgroundColor: "rgba(255,255,255,0.1)" }}
                  >
                    {app.version}
                  </span>
                ) : (
                  <span />
                )}
                {appCost !== null && (
                  <span
                    className={`rounded border px-2 py-0.5 ${
                      isCostAvailable(appCost)
                        ? "border-[#fbbf24]/40 bg-[rgba(251,191,36,0.08)]"
                        : "border-[#57606c]/50 bg-white/[0.04]"
                    }`}
                  >
                    <CostAmount
                      cost={appCost}
                      unavailableLabel="Cost not available"
                      className="text-sm font-bold text-[#fbbf24]"
                      unavailableClassName="text-xs font-semibold text-[#8b97a5]"
                    />
                  </span>
                )}
              </div>
            )}
            {app.client_name && (
              <p className="font-bold text-xl text-white">
                {app.client_name}
              </p>
            )}
            {app.instance_name && (
              <p className="text-sm text-white/50">
                {app.instance_name}
              </p>
            )}
          </div>
        </div>

        <div className="flex flex-col items-center justify-center my-4 gap-2">
          <TierMiniCube tierIndex={tierIndex} />
          {isOperating &&
            pipelineProgress &&
            (pipelineProgress.active_job || pipelineProgress.run_url) && (
              <div className="text-center px-2 space-y-0.5">
                {pipelineProgress.active_job && (
                  <p className={`text-xs font-medium ${pipelineJobClass}`}>
                    {pipelineProgress.active_job}
                  </p>
                )}
                {pipelineProgress.current_step && (
                  <p className="text-[11px] text-muted-foreground">{pipelineProgress.current_step}</p>
                )}
                {pipelineProgress.run_url && (
                  <a
                    href={pipelineProgress.run_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <ExternalLink className="h-3 w-3" />
                    View on GitHub
                  </a>
                )}
              </div>
            )}
          {showCancelRun && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-7 text-[11px]"
              disabled={isCancelling}
              onClick={(e) => {
                e.stopPropagation();
                handleCancelRun();
              }}
            >
              {isCancelling ? "Cancelling…" : "Cancel run"}
            </Button>
          )}
        </div>

        {!isOperating && (
          <div className="space-y-4 mt-auto">
            <ApplicationMetricRow metric="cpu" label="CPU" value={app.cpu} tierIndex={tierIndex} />
            <ApplicationMetricRow metric="memory" label="Memory" value={app.memory} tierIndex={tierIndex} />
            <ApplicationMetricRow metric="gpu" label="GPU" value={app.gpu} tierIndex={tierIndex} />
          </div>
        )}
      </div>
      <DeploymentLogsDialog
        open={logsOpen}
        onOpenChange={setLogsOpen}
        subdomain={app.nodename}
        rayAppName={app.name}
        tier={urlTier}
      />
    </>
  );
}
