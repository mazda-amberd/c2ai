import { useState, useCallback, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { Application } from "@/types/application";
import {
  cancelPipeline,
  getActivePipelines,
  getGithubBranches,
  getGithubTags,
  terminateDeployment,
  triggerMoveTierDeployment,
} from "@api/services/deployments";
import {
  findRegisteredDeploymentForInstance,
  terminateRegisteredDeployment,
} from "@api/services/registeredApplications";
import { DEPLOY_BRANCH_REPO } from "@/constants/deployment";
import type { PipelineStatusRecord } from "@/types/deployment";
import { isTerminal } from "@/utils/deploymentStatus";
import { splitWorkflowHostLabel } from "@/utils/subdomain";

// --- React Query keys --------------------------------------------------------

/** All active pipeline runs — single cache entry shared across all tier views. */
export const activePipelinesQueryKey = () => ["pipeline-active"] as const;

/** @deprecated use activePipelinesQueryKey — kept so old invalidate calls still compile. */
export const deploymentsQueryKey = (tier: number) => ["deployments", tier] as const;

export const githubBranchesQueryKey = (repo: string) =>
  ["github-branches", repo] as const;

export const githubTagsQueryKey = (repo: string) =>
  ["github-tags", repo] as const;

// --- Queries -----------------------------------------------------------------

/**
 * All active pipeline runs across every tier, polled every 10 s.
 * GH status is cached server-side so this is safe at this frequency.
 */
export function useActivePipelines() {
  return useQuery<PipelineStatusRecord[]>({
    queryKey: activePipelinesQueryKey(),
    queryFn: getActivePipelines,
    refetchInterval: 10_000,
  });
}

/**
 * Active *new deploy* pipeline rows for a tier (updates use instance cards; terminate
 * uses tier=null and overlays).
 */
export function useDeployments(tier: number) {
  const { data: all = [], ...rest } = useActivePipelines();
  return {
    data: all.filter((r) => r.tier === tier && r.operation === "deploy"),
    ...rest,
  };
}

/** Git branch and tag list for the deploy source repo; runs when `enabled` (e.g. modal open). */
export function useDeployBranchOptions(enabled: boolean) {
  const {
    data: branches = [],
    isLoading: branchesLoading,
    error: branchesQueryError,
  } = useQuery({
    queryKey: githubBranchesQueryKey(DEPLOY_BRANCH_REPO),
    queryFn: () => getGithubBranches(DEPLOY_BRANCH_REPO),
    staleTime: Infinity,
    enabled,
  });

  const {
    data: tags = [],
    isLoading: tagsLoading,
    error: tagsQueryError,
  } = useQuery({
    queryKey: githubTagsQueryKey(DEPLOY_BRANCH_REPO),
    queryFn: () => getGithubTags(DEPLOY_BRANCH_REPO),
    staleTime: Infinity,
    enabled,
  });

  const branchesError = branchesQueryError
    ? branchesQueryError instanceof Error
      ? branchesQueryError.message
      : "Failed to load branches"
    : tagsQueryError
      ? tagsQueryError instanceof Error
        ? tagsQueryError.message
        : "Failed to load tags"
      : null;

  return { branches, tags, branchesLoading: branchesLoading || tagsLoading, branchesError };
}

// --- Cache helpers -----------------------------------------------------------

/** `tierIndex` is 0-based (same as route / apps list). */
export function useInvalidateDeploymentsForTierIndex(tierIndex: number) {
  const queryClient = useQueryClient();
  const tier = tierIndex + 1;
  return useCallback(() => {
    queryClient.invalidateQueries({ queryKey: deploymentsQueryKey(tier) });
  }, [queryClient, tier]);
}

// --- Tier-scoped UI actions (list + modals) ----------------------------------

/**
 * Deployment polling, redeploy modal target, and terminate flow for a single
 * tier (0-based tier index).
 */
export function useTierDeploymentActions(
  tierIndex: number,
  apps: Application[] = [],
) {
  const tier = tierIndex + 1;
  const queryClient = useQueryClient();
  const { data: allActiveRuns = [] } = useActivePipelines();

  /** In-flight *new* deploys only — updates use the instance card overlay + GH status there. */
  const pendingDeployments = allActiveRuns.filter(
    (r) => r.tier === tier && r.operation === "deploy",
  );

  // Terminate: optimistic app.id set until the pipeline row appears / completes.
  // Update: same until /api/pipeline/active includes the update run (then API drives UI).
  const [terminatingIds, setTerminatingIds] = useState<Set<number>>(new Set());
  const [migrationIds, setMigrationIds] = useState<Set<number>>(new Set());
  const [updatingIds, setUpdatingIds] = useState<Set<number>>(new Set());

  /** Drop optimistic update flags once the active-pipelines poll shows the run. */
  useEffect(() => {
    if (updatingIds.size === 0) return;
    setUpdatingIds((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of prev) {
        const app = apps.find((a) => a.id === id);
        if (!app) {
          next.delete(id);
          changed = true;
          continue;
        }
        const apiHasUpdate = allActiveRuns.some(
          (r) =>
            r.operation === "update" &&
            r.subdomain === app.nodename &&
            !isTerminal(r),
        );
        if (apiHasUpdate) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [apps, allActiveRuns, updatingIds.size]);

  /** Same as optimistic ``updatingIds``, but for terminate. */
  useEffect(() => {
    if (terminatingIds.size === 0) return;
    setTerminatingIds((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of prev) {
        const app = apps.find((a) => a.id === id);
        if (!app) {
          next.delete(id);
          changed = true;
          continue;
        }
        const apiHasTerminate = allActiveRuns.some(
          (r) =>
            r.operation === "terminate" &&
            r.subdomain === app.nodename &&
            !isTerminal(r),
        );
        if (apiHasTerminate) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [apps, allActiveRuns, terminatingIds.size]);

  /** Same as optimistic ``updatingIds``, but for migration. */
  useEffect(() => {
    if (migrationIds.size === 0) return;
    setMigrationIds((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of prev) {
        const app = apps.find((a) => a.id === id);
        if (!app) {
          next.delete(id);
          changed = true;
          continue;
        }
        const apiHasMigration = allActiveRuns.some(
          (r) =>
            r.operation === "migration" &&
            r.subdomain === app.nodename &&
            !isTerminal(r),
        );
        if (apiHasMigration) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [apps, allActiveRuns, migrationIds.size]);

  const invalidateDeployments = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: activePipelinesQueryKey() });
  }, [queryClient]);

  const [updateTarget, setUpdateTarget] = useState<Application | null>(null);

  const [bombTarget, setBombTarget] = useState<Application | null>(null);
  const [isBombing, setIsBombing] = useState(false);

  const openUpdate = useCallback((app: Application) => {
    setUpdateTarget(app);
  }, []);

  const openTerminate = useCallback((app: Application) => {
    setBombTarget(app);
  }, []);

  const confirmTerminate = useCallback(async () => {
    if (!bombTarget) return;
    setIsBombing(true);
    const appId = bombTarget.id;
    try {
      // A registered deployment owns a type-specific cleanup pipeline
      // (containerized-app-terminate for containers), so terminate it through
      // its own endpoint; only legacy ada instances go to ada-terminate.
      // nodename = kubernetes namespace = the subdomain the devops workflow expects
      const registered = await findRegisteredDeploymentForInstance(
        tier,
        bombTarget.nodename,
      );
      if (registered) {
        await terminateRegisteredDeployment(
          registered.id,
          registered.instance_name,
        );
      } else {
        await terminateDeployment(bombTarget.nodename);
      }
      setTerminatingIds((prev) => new Set([...prev, appId]));
      invalidateDeployments();
    } catch {
      // Errors are surfaced via the modal; swallow here
    } finally {
      setIsBombing(false);
      setBombTarget(null);
    }
  }, [bombTarget, invalidateDeployments, tier]);

  // app.nodename = kubernetes namespace = workflow host label (e.g. "amberd-alex-test-ada")
  // app.name     = kubernetes resource owner name inside that namespace (e.g. "ada")
  // The devops workflows identify instances by namespace/subdomain, not by resource name.
  const updateModalInitialValues = updateTarget
    ? (() => {
        const { customer, env } = splitWorkflowHostLabel(updateTarget.nodename);
        return {
          subdomain: updateTarget.nodename,
          customer_name: customer,
          env_instance: env,
        };
      })()
    : undefined;

  const onUpdateDeployed = useCallback(
    () => {
      if (updateTarget) {
        setUpdatingIds((prev) => new Set([...prev, updateTarget.id]));
      }
      invalidateDeployments();
      setUpdateTarget(null);
    },
    [invalidateDeployments, updateTarget],
  );

  const triggerMoveTier = useCallback(
    async (app: Application, targetTier: number) => {
      const run = await triggerMoveTierDeployment({
        subdomain: app.nodename,
        tier: targetTier,
      });
      setMigrationIds((prev) => new Set([...prev, app.id]));
      invalidateDeployments();
      return run;
    },
    [invalidateDeployments],
  );

  const isAppUpdating = useCallback(
    (app: Application) =>
      updatingIds.has(app.id) ||
      allActiveRuns.some(
        (r) =>
          r.operation === "update" &&
          r.subdomain === app.nodename &&
          !isTerminal(r),
      ),
    [allActiveRuns, updatingIds],
  );

  const updatePipelineForApp = useCallback(
    (app: Application): PipelineStatusRecord | null =>
      allActiveRuns.find(
        (r) =>
          r.operation === "update" &&
          r.subdomain === app.nodename &&
          !isTerminal(r),
      ) ?? null,
    [allActiveRuns],
  );

  const isAppMigrating = useCallback(
    (app: Application) =>
      migrationIds.has(app.id) ||
      allActiveRuns.some(
        (r) =>
          r.operation === "migration" &&
          r.subdomain === app.nodename &&
          !isTerminal(r),
      ),
    [allActiveRuns, migrationIds],
  );

  const migrationPipelineForApp = useCallback(
    (app: Application): PipelineStatusRecord | null =>
      allActiveRuns.find(
        (r) =>
          r.operation === "migration" &&
          r.subdomain === app.nodename &&
          !isTerminal(r),
      ) ?? null,
    [allActiveRuns],
  );

  const isAppTerminating = useCallback(
    (app: Application) =>
      terminatingIds.has(app.id) ||
      allActiveRuns.some(
        (r) =>
          r.operation === "terminate" &&
          r.subdomain === app.nodename &&
          !isTerminal(r),
      ),
    [allActiveRuns, terminatingIds],
  );

  const terminatePipelineForApp = useCallback(
    (app: Application): PipelineStatusRecord | null =>
      allActiveRuns.find(
        (r) =>
          r.operation === "terminate" &&
          r.subdomain === app.nodename &&
          !isTerminal(r),
      ) ?? null,
    [allActiveRuns],
  );

  const cancelPipelineRun = useCallback(
    async (pipelineRunId: string) => {
      try {
        const out = await cancelPipeline(pipelineRunId);
        invalidateDeployments();
        setUpdatingIds((prev) => {
          const next = new Set(prev);
          let changed = false;
          for (const id of prev) {
            const app = apps.find((a) => a.id === id);
            if (app?.nodename === out.subdomain) {
              next.delete(id);
              changed = true;
            }
          }
          return changed ? next : prev;
        });
        setTerminatingIds((prev) => {
          const next = new Set(prev);
          let changed = false;
          for (const id of prev) {
            const app = apps.find((a) => a.id === id);
            if (app?.nodename === out.subdomain) {
              next.delete(id);
              changed = true;
            }
          }
          return changed ? next : prev;
        });
        setMigrationIds((prev) => {
          const next = new Set(prev);
          let changed = false;
          for (const id of prev) {
            const app = apps.find((a) => a.id === id);
            if (app?.nodename === out.subdomain) {
              next.delete(id);
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : "Could not cancel the pipeline run.";
        window.alert(msg);
      }
    },
    [apps, invalidateDeployments],
  );

  return {
    pendingDeployments,
    terminatingIds,
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
  };
}
