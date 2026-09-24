import { apiFetch } from "..";

import type {
  PipelineRunRecord,
  PipelineStatusRecord,
  TriggerDeploymentPayload,
  TriggerMoveTierPayload,
} from "@/types/deployment";

// ---------------------------------------------------------------------------
// Trigger operations — return PipelineRunRecord immediately
// ---------------------------------------------------------------------------

export const triggerDeployment = async (
  payload: TriggerDeploymentPayload,
): Promise<PipelineRunRecord> => {
  return await apiFetch<PipelineRunRecord>("/api/deploy", {
    method: "POST",
    body: JSON.stringify(payload),
  });
};

export const triggerUpdateDeployment = async (
  payload: TriggerDeploymentPayload,
): Promise<PipelineRunRecord> => {
  return await apiFetch<PipelineRunRecord>("/api/deploy/update", {
    method: "POST",
    body: JSON.stringify(payload),
  });
};

export const triggerMoveTierDeployment = async (
  payload: TriggerMoveTierPayload,
): Promise<PipelineRunRecord> => {
  return await apiFetch<PipelineRunRecord>("/api/deploy/move-tier", {
    method: "POST",
    body: JSON.stringify(payload),
  });
};

export const terminateDeployment = async (
  subdomain: string,
): Promise<PipelineRunRecord> => {
  return await apiFetch<PipelineRunRecord>("/api/deploy/terminate", {
    method: "POST",
    body: JSON.stringify({ subdomain }),
  });
};

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------

/**
 * All active pipeline operations across all instances.
 * Poll this on the global pipelines dashboard (e.g. every 10–15 s).
 */
export const getActivePipelines = async (): Promise<PipelineStatusRecord[]> => {
  return await apiFetch<PipelineStatusRecord[]>("/api/pipeline/active");
};

/**
 * Live status for the most recent operation on a specific subdomain.
 * Returns null if no operations have ever been triggered for this subdomain.
 */
export const getPipelineStatus = async (
  subdomain: string,
): Promise<PipelineStatusRecord | null> => {
  return await apiFetch<PipelineStatusRecord | null>(
    `/api/pipeline/status?subdomain=${encodeURIComponent(subdomain)}`,
  );
};

/**
 * Historical pipeline runs for a subdomain (no live GH API calls).
 */
export const getPipelineHistory = async (
  subdomain: string,
  limit = 20,
): Promise<PipelineRunRecord[]> => {
  return await apiFetch<PipelineRunRecord[]>(
    `/api/pipeline/history?subdomain=${encodeURIComponent(subdomain)}&limit=${limit}`,
  );
};

/** Cancel the linked GitHub Actions run; marks the pipeline row ended in Athena. */
export const cancelPipeline = async (
  pipelineRunId: string,
): Promise<PipelineRunRecord> => {
  return await apiFetch<PipelineRunRecord>("/api/pipeline/cancel", {
    method: "POST",
    body: JSON.stringify({ pipeline_run_id: pipelineRunId }),
  });
};

// ---------------------------------------------------------------------------
// GitHub branches / tags
// ---------------------------------------------------------------------------

export const getGithubBranches = async (repo: string): Promise<string[]> => {
  return await apiFetch<string[]>(
    `/api/github/branches?repo=${encodeURIComponent(repo)}`,
  );
};

export const getGithubTags = async (repo: string): Promise<string[]> => {
  return await apiFetch<string[]>(
    `/api/github/tags?repo=${encodeURIComponent(repo)}`,
  );
};
