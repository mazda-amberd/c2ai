import { useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { appsColors } from "@styles/appsColors";
import ApplicationListItem from "./ApplicationListItem";
import DeployingCard from "./DeployingCard";
import type { Application } from "@/types/application";
import NewDeploymentModal from "./NewDeploymentModal";
import { TIER_DISPLAY_NAMES } from "@/constants/deployment";
import { useTierDeploymentActions } from "@hooks/useDeployments";
import { useDismissedDeployments } from "@hooks/useDismissedDeployments";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@ui/dialog";
import { Button } from "@ui/button";
import { Input } from "@ui/input";

type ApplicationListProps = {
  tierIndex: number;
  apps: Application[];
  loading: boolean;
};

export default function ApplicationList({
  tierIndex,
  apps,
  loading,
}: ApplicationListProps) {
  const appStyle = appsColors[tierIndex] || appsColors[0];
  const activeTier = TIER_DISPLAY_NAMES[tierIndex];

  const [terminateStep, setTerminateStep] = useState<1 | 2>(1);
  const { dismiss, isDismissed } = useDismissedDeployments();
  const [confirmText, setConfirmText] = useState("");

  const {
    pendingDeployments,
    isAppMigrating,
    isAppTerminating,
    isAppUpdating,
    migrationPipelineForApp,
    updatePipelineForApp,
    terminatePipelineForApp,
    updateTarget,
    setUpdateTarget,
    openUpdate,
    updateModalInitialValues,
    onUpdateDeployed,
    triggerMoveTier,
    bombTarget,
    setBombTarget,
    openTerminate,
    isBombing,
    confirmTerminate,
    cancelPipelineRun,
  } = useTierDeploymentActions(tierIndex, apps);
  const currentTier = tierIndex + 1;
  const moveTierOptions = TIER_DISPLAY_NAMES.map((label, index) => ({
    label,
    value: index + 1,
  }));
  const [moveTarget, setMoveTarget] = useState<Application | null>(null);
  const [moveTierValue, setMoveTierValue] = useState<number | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [isMoving, setIsMoving] = useState(false);

  const handleTerminateOpenChange = (open: boolean) => {
    if (!open) {
      setBombTarget(null);
      setTerminateStep(1);
      setConfirmText("");
    }
  };

  const handleOpenTerminate = (app: Application) => {
    setTerminateStep(1);
    setConfirmText("");
    openTerminate(app);
  };

  const handleConfirmTerminate = async () => {
    await confirmTerminate();
    setTerminateStep(1);
    setConfirmText("");
  };

  if (!activeTier) {
    return null;
  }

  if (loading) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <div className="text-muted-foreground">Loading applications...</div>
      </div>
    );
  }

  const visibleDeployments = pendingDeployments.filter((d) => !isDismissed(d.id));
  const isEmpty = apps.length === 0 && visibleDeployments.length === 0;

  const openMoveTier = (app: Application) => {
    setMoveTarget(app);
    setMoveTierValue(null);
    setMoveError(null);
  };

  const closeMoveTier = () => {
    setMoveTarget(null);
    setMoveTierValue(null);
    setMoveError(null);
    setIsMoving(false);
  };

  const confirmMoveTier = async () => {
    if (!moveTarget || moveTierValue == null) {
      setMoveError("Select a target tier.");
      return;
    }
    setIsMoving(true);
    setMoveError(null);
    try {
      await triggerMoveTier(moveTarget, moveTierValue);
      closeMoveTier();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Could not start tier migration.";
      setMoveError(message);
      setIsMoving(false);
    }
  };

  if (isEmpty) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <div className="text-muted-foreground">
          No instances found for {activeTier}
        </div>
      </div>
    );
  }

  return (
    <>
      <NewDeploymentModal
        open={updateTarget !== null}
        onOpenChange={(open) => { if (!open) setUpdateTarget(null); }}
        tierIndex={tierIndex}
        mode="update"
        initialValues={updateModalInitialValues}
        onDeployed={onUpdateDeployed}
        targetApp={updateTarget ? { name: updateTarget.name, nodename: updateTarget.nodename, version: updateTarget.version } : undefined}
      />

      <Dialog open={moveTarget !== null} onOpenChange={(open) => { if (!open) closeMoveTier(); }}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>Move application to another tier</DialogTitle>
            <DialogDescription>
              Choose the target tier for{" "}
              <span className="font-medium text-foreground">
                {moveTarget?.nodename}
              </span>
              . The current instance will stay locked while migration is in progress.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 pt-2">
            <p className="text-xs uppercase tracking-[0.12em] text-muted-foreground">
              Select target tier
            </p>
            <div className="grid grid-cols-4 gap-2">
              {moveTierOptions.map((option) => {
                const isCurrentTier = option.value === currentTier;
                const isSelected = option.value === moveTierValue;
                return (
                  <Button
                    key={option.value}
                    type="button"
                    variant={isSelected ? "default" : "secondary"}
                    className="h-11"
                    disabled={isCurrentTier || isMoving}
                    onClick={() => {
                      setMoveTierValue(option.value);
                      setMoveError(null);
                    }}
                  >
                    {option.label}
                  </Button>
                );
              })}
            </div>
            <p className="text-xs text-muted-foreground">
              Current tier: {TIER_DISPLAY_NAMES[tierIndex]}
            </p>
            {moveError ? (
              <p className="text-xs text-critical-text">{moveError}</p>
            ) : null}
          </div>
          <DialogFooter className="pt-4">
            <Button
              variant="secondary"
              onClick={closeMoveTier}
              disabled={isMoving}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void confirmMoveTier()}
              disabled={isMoving || moveTierValue == null}
            >
              {isMoving ? (
                <>
                  <Loader2 className="animate-spin" />
                  Migrating…
                </>
              ) : (
                "Start migration"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={bombTarget !== null} onOpenChange={handleTerminateOpenChange}>
        <DialogContent
          closeClassName="text-terminate-body opacity-80 hover:bg-terminate-callout/50 hover:text-terminate-title hover:opacity-100 data-[state=open]:bg-transparent data-[state=open]:text-terminate-body"
          className="sm:max-w-[26.25rem] gap-0 overflow-hidden rounded-xl border border-terminate-border bg-terminate-dialog p-0 shadow-xl"
        >
          <div className="flex flex-col gap-4 p-6">
            <DialogHeader>
              <DialogTitle className="text-xl font-semibold leading-snug text-terminate-title">
                Terminate application?
              </DialogTitle>
            </DialogHeader>

            <div>
              <p className="text-sm text-terminate-body">
                This will permanently stop and remove
              </p>
              <p className="mt-1 break-all font-semibold text-foreground">
                {bombTarget?.nodename}
              </p>
            </div>

            <div className="flex items-center gap-2 rounded-md border border-terminate-callout-border bg-terminate-callout px-3 py-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-terminate-callout-foreground" />
              <p className="text-sm text-terminate-callout-foreground">
                This action cannot be undone.
              </p>
            </div>

            {terminateStep === 2 && (
              <div className="flex flex-col gap-1.5">
                <label className="text-sm text-terminate-body">
                  Enter &ldquo;permanently delete&rdquo; to confirm
                </label>
                <Input
                  placeholder="e.g. permanently delete"
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  className="border-terminate-input-border bg-terminate-input text-foreground placeholder:text-terminate-body/22 focus-visible:border-terminate-callout-border focus-visible:ring-terminate-border/50"
                  autoFocus
                />
              </div>
            )}

            <DialogFooter className="gap-2 sm:gap-2">
              <Button
                variant="ghost"
                onClick={() => handleTerminateOpenChange(false)}
                disabled={isBombing}
                className="text-terminate-body hover:bg-terminate-callout/50 hover:text-terminate-title"
              >
                Cancel
              </Button>
              {terminateStep === 1 ? (
                <Button
                  type="button"
                  variant="destructive"
                  onClick={() => setTerminateStep(2)}
                  className="border-0 bg-terminate-accent text-white shadow-none hover:bg-terminate-accent-hover hover:text-white"
                >
                  Yes, Terminate
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="destructive"
                  onClick={() => void handleConfirmTerminate()}
                  disabled={
                    isBombing ||
                    confirmText.toLowerCase().trim() !== "permanently delete"
                  }
                  className="border-0 bg-terminate-accent text-white shadow-none hover:bg-terminate-accent-hover hover:text-white disabled:opacity-40"
                >
                  {isBombing ? (
                    <>
                      <Loader2 className="animate-spin" />
                      Terminating…
                    </>
                  ) : (
                    "Confirm Termination"
                  )}
                </Button>
              )}
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      <div
        className="flex flex-col w-full h-full border rounded-md py-6"
        style={{
          background: appStyle.pageBackground,
          borderColor: appStyle.border,
          boxShadow: appStyle.shadow,
        }}
      >
        <p
          className="font-semibold text-xl shrink-0 mb-2 pl-6"
          style={{ color: appStyle.text }}
        >
          Applications ({apps.length + visibleDeployments.length})
        </p>
        <div
          className="w-full flex-1 min-h-0 overflow-y-auto no-scrollbar"
          style={{
            maskImage:
              "linear-gradient(to bottom, transparent, black 24px, black calc(100% - 24px), transparent)",
            WebkitMaskImage:
              "linear-gradient(to bottom, transparent, black 24px, black calc(100% - 24px), transparent)",
          }}
        >
          <div className="grid grid-cols-3 gap-6 content-start px-6 pt-4 pb-6">
            {visibleDeployments.map((d) => (
              <DeployingCard
                key={`deploying-${d.id}`}
                deployment={d}
                appStyle={appStyle}
                onCancel={
                  d.run_id != null
                    ? () => void cancelPipelineRun(d.id)
                    : undefined
                }
                onDismiss={() => dismiss(d.id)}
              />
            ))}
            {apps.map((app) => {
              const mP = migrationPipelineForApp(app);
              const tP = terminatePipelineForApp(app);
              const uP = updatePipelineForApp(app);
              const cancellable =
                tP?.run_id != null
                  ? tP
                  : mP?.run_id != null
                    ? mP
                    : uP?.run_id != null
                      ? uP
                      : null;
              return (
                <ApplicationListItem
                  key={app.id}
                  app={app}
                  tierIndex={tierIndex}
                  onMoveTier={() => openMoveTier(app)}
                  onUpdate={() => openUpdate(app)}
                  onBomb={() => handleOpenTerminate(app)}
                  isMigrating={isAppMigrating(app)}
                  isTerminating={isAppTerminating(app)}
                  isUpdating={isAppUpdating(app)}
                  migrationPipeline={mP}
                  updatePipeline={uP}
                  terminatePipeline={tP}
                  onCancelPipeline={
                    cancellable?.run_id != null
                      ? () => void cancelPipelineRun(cancellable.id)
                      : undefined
                  }
                />
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
