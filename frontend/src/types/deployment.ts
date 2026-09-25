// Legacy type — kept so existing UI components compile during the transition.
export type DeploymentStatus = "deploying" | "success" | "failed" | "cancelled";

/** @deprecated Use PipelineRunRecord instead */
export type DeploymentRecord = {
  id: number;
  subdomain: string;
  customer_name: string;
  env_instance: string;
  tier: number;
  branch: string;
  domain: string;
  status: DeploymentStatus;
  created_at: string | null;
  completed_at: string | null;
};

export type TriggerDeploymentPayload = {
  branch: string;
  subdomain: string;
  customer_name: string;
  domain: string;
  env_instance: string;
  tier: number;
};

export type TriggerMoveTierPayload = {
  subdomain: string;
  tier: number;
};

// ---------------------------------------------------------------------------
// New pipeline types
// ---------------------------------------------------------------------------

/** GitHub Actions run status (mirrors GH API status field). */
export type GhRunStatus = "queued" | "in_progress" | "completed" | "waiting" | null;

/** GitHub Actions run conclusion (only present when status == "completed"). */
export type GhRunConclusion =
  | "success"
  | "failure"
  | "cancelled"
  | "timed_out"
  | "neutral"
  | "action_required"
  | null;

/** Returned immediately after triggering any operation. */
export type PipelineRunRecord = {
  id: string; // UUID (correlation_id)
  subdomain: string;
  operation: "deploy" | "migration" | "update" | "terminate";
  event_type: string;
  triggered_by: string;
  /** Registered application being deployed; null for ADA (titled by customer). */
  application_name?: string | null;
  run_id: number | null;
  tier: number | null;
  branch: string | null;
  dispatched_at: string | null;
  ended_at: string | null;
};

/** Full live status returned by /api/pipeline/status and /api/pipeline/active. */
export type PipelineStatusRecord = PipelineRunRecord & {
  gh_status: GhRunStatus;
  gh_conclusion: GhRunConclusion;
  run_url: string | null;
  active_job: string | null;
  current_step: string | null;
  started_at: string | null;
  completed_at: string | null;
};
