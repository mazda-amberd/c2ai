/** Wrap any JSON value in a Response — mirrors the real fetch response shape. */
export function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Minimal PipelineRunRecord fixture (no GH status fields). Override as needed. */
export const basePipelineRun = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  subdomain: "amberd-acme-ada",
  operation: "deploy",
  event_type: "ada-deploy.yaml",
  triggered_by: "admin",
  run_id: null,
  tier: 2,
  branch: "main",
  dispatched_at: "2026-04-01T12:00:00+00:00",
  ended_at: null,
} as const;

/** Minimal PipelineStatusRecord fixture (includes GH status fields). Override as needed. */
export const basePipelineStatus = {
  ...basePipelineRun,
  gh_status: "in_progress",
  gh_conclusion: null,
  run_url: "https://github.com/Inferaim/devops/actions/runs/12345",
  active_job: "deploy",
  current_step: "Helm deploy",
  started_at: "2026-04-01T12:00:10+00:00",
  completed_at: null,
} as const;
