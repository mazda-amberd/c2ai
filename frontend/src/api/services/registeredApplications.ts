import { apiFetch } from "..";

/* Registered applications (catalog + deployments), GitHub connections and
 * LLM models. Types mirror backend/src/service/schemas/registered_application.py
 * and github_connection.py. */

/* ---------------- Shared enums ---------------- */

export type ApiApplicationType = "github_workflow" | "containerized";
export type ApiApplicationStatus = "active" | "draft" | "deprecated";
export type ApiParameterType = "text" | "number" | "boolean" | "select" | "key_value";

/** GitHub templates declare `{key, type}`; containerized ones `{key, value}`. */
export type ApiGithubParameter = { key: string; type: ApiParameterType };
export type ApiContainerParameter = { key: string; value: string };

function toQuery(query: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/* ---------------- Catalog (list) ---------------- */

/** RegisteredApplicationCatalogItem — note: no parameters here, only on
 *  the detail endpoint. */
export type ApiCatalogItem = {
  id: string;
  name: string;
  description: string | null;
  application_type: ApiApplicationType;
  status: ApiApplicationStatus;
  current_version: number;
  total_deployed_instances: number;
  /** e.g. [{ tier: "Tier 1", instances: 2 }] */
  tiers_deployed_to: Array<{ tier: string; instances: number }>;
  can_delete: boolean;
  created_at: string;
  updated_at: string;
};

export type ListApplicationsQuery = {
  search?: string;
  application_type?: ApiApplicationType;
  status?: ApiApplicationStatus;
  tier?: number;
  sort_by?: "name" | "instances" | "created_at";
  sort_order?: "asc" | "desc";
  offset?: number;
  limit?: number;
};

export const listRegisteredApplications = async (
  query: ListApplicationsQuery = {},
): Promise<ApiCatalogItem[]> => {
  const res = await apiFetch<{ items: ApiCatalogItem[]; total: number }>(
    `/api/registered-applications${toQuery(query)}`,
  );
  return res.items ?? [];
};

/* ---------------- Detail ---------------- */

export type ApiGithubWorkflowConfiguration = {
  github_connection_id: string;
  trigger_method: "workflow_dispatch" | "repository_dispatch";
  repository: string;
  code_repository: string | null;
  workflow_file_path: string;
  ref: string;
};

export type ApiContainerConfiguration = {
  registry: string;
  image_registry: string;
  registry_username: string | null;
  tag: string | null;
  port: number | null;
  pull_policy: "Always" | "IfNotPresent" | "Never";
  expose_public_service: boolean;
  cpu_request: string | null;
  memory_request: string | null;
  scaling: string | null;
  storage: string | null;
};

type ApiDetailBase = {
  id: string;
  name: string;
  description: string | null;
  status: ApiApplicationStatus;
  version: number;
  created_by: string;
  created_at: string;
  llm: { endpoint: string; model_name: string } | null;
};

export type ApiGithubApplicationDetail = ApiDetailBase & {
  application_type: "github_workflow";
  github: ApiGithubWorkflowConfiguration;
  parameters: ApiGithubParameter[];
};

export type ApiContainerApplicationDetail = ApiDetailBase & {
  application_type: "containerized";
  container: ApiContainerConfiguration;
  parameters: ApiContainerParameter[];
};

/** Discriminated on `application_type`. */
export type ApiRegisteredApplicationDetail =
  | ApiGithubApplicationDetail
  | ApiContainerApplicationDetail;

export const getRegisteredApplication = async (
  applicationId: string,
): Promise<ApiRegisteredApplicationDetail> =>
  await apiFetch<ApiRegisteredApplicationDetail>(
    `/api/registered-applications/${encodeURIComponent(applicationId)}`,
  );

export const deleteRegisteredApplication = async (applicationId: string): Promise<void> => {
  await apiFetch<unknown>(
    `/api/registered-applications/${encodeURIComponent(applicationId)}`,
    { method: "DELETE" },
  );
};

/* ---------------- Registration ---------------- */

export type LlmConfigPayload = {
  endpoint: string;
  api_token: string;
  model_name: string;
};

export type RegisterGithubApplicationPayload = {
  application_type: "github_workflow";
  name: string;
  description: string;
  github: {
    github_connection: string;
    trigger_method: "workflow_dispatch" | "repository_dispatch";
    repository: string;
    /** Optional — omitted when the application has no separate source repo. */
    code_repository?: string;
    workflow_file_path: string;
    ref: string;
  };
  parameters: ApiGithubParameter[];
  llm: LlmConfigPayload;
};

export type RegisterContainerApplicationPayload = {
  application_type: "containerized";
  name: string;
  description: string;
  container: {
    registry: string;
    image_registry: string;
    registry_username: string;
    registry_password: string;
    tag: string;
    port: number;
    pull_policy: "Always" | "IfNotPresent" | "Never";
    expose_public_service: boolean;
    /** Optional (min_length=1 on the API) — omit rather than send "". */
    gpu_request?: string;
    cpu_request?: string;
    memory_request?: string;
    scaling?: string;
    storage?: string;
  };
  parameters: ApiContainerParameter[];
  llm: LlmConfigPayload;
};

export const registerGithubApplication = async (
  payload: RegisterGithubApplicationPayload,
): Promise<ApiGithubApplicationDetail> =>
  await apiFetch<ApiGithubApplicationDetail>("/api/registered-applications/github", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const registerContainerApplication = async (
  payload: RegisterContainerApplicationPayload,
): Promise<ApiContainerApplicationDetail> =>
  await apiFetch<ApiContainerApplicationDetail>("/api/registered-applications/container", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/* ---------------- Version pickers ---------------- */

/** GitHubRepositoryTagList — `items` is branches + tags combined. */
export type ApiGithubTagList = {
  application_id: string;
  repository: string;
  branches: string[];
  tags: string[];
  items: string[];
};

/** Branches and tags for a GitHub application, read from the code repository. */
export const listGithubTags = async (
  applicationId: string,
  limit = 200,
): Promise<ApiGithubTagList> =>
  await apiFetch<ApiGithubTagList>(
    `/api/registered-applications/${encodeURIComponent(applicationId)}/github-tags?limit=${limit}`,
  );

export type ApiImageTag = {
  tag: string;
  image_reference: string;
  digest: string | null;
  last_updated: string | null;
  is_default: boolean;
};

/** ContainerImageTagList — `default_tag` / `is_default` mark the registered tag. */
export type ApiImageTagList = {
  application_id: string;
  registry: string;
  repository: string;
  default_tag: string | null;
  items: ApiImageTag[];
  total: number;
  limit: number;
};

export const listImageTags = async (
  applicationId: string,
  limit = 100,
): Promise<ApiImageTagList> =>
  await apiFetch<ApiImageTagList>(
    `/api/registered-applications/${encodeURIComponent(applicationId)}/image-tags?limit=${limit}`,
  );

/* ---------------- Deployments ---------------- */

export type ApiDeploymentStatus =
  | "pending"
  | "deploying"
  | "running"
  | "updating"
  | "failed"
  | "terminating"
  | "terminated"
  | "cancelled";

export const ACTIVE_DEPLOYMENT_STATUSES: ApiDeploymentStatus[] = [
  "pending",
  "deploying",
  "updating",
  "terminating",
];

/** RegisteredApplicationDeploymentOut (+ detail fields, optional). */
export type ApiDeployment = {
  id: string;
  application_id: string;
  application_name: string;
  application_type: ApiApplicationType;
  application_version: number;
  instance_name: string;
  tier: number;
  status: ApiDeploymentStatus | string;
  configuration: Record<string, unknown>;
  triggered_by: string;
  dispatch_reference: Record<string, unknown> | null;
  current_step: string;
  failure_reason: string | null;
  completed_at: string | null;
  /** Stable namespace/host label used to match a metrics card to this record. */
  subdomain: string | null;
  events?: Array<{ status?: string; step?: string; message?: string; created_at?: string }>;
  workflow_progress?: unknown;
  workflow_progress_error?: string | null;
};

export type DeployGithubApplicationPayload = {
  tier: number;
  version: string;
  /** Registered parameter values keyed by parameter key. Never include
   *  slack_user / tier / namespace / instance_name — Athena fills those. */
  parameters: Record<string, unknown>;
  /** Optional override; normally omitted for GitHub applications. */
  instance_name?: string;
};

export const deployGithubApplication = async (
  applicationId: string,
  payload: DeployGithubApplicationPayload,
): Promise<ApiDeployment> =>
  await apiFetch<ApiDeployment>(
    `/api/registered-applications/${encodeURIComponent(applicationId)}/deployments`,
    { method: "POST", body: JSON.stringify(payload) },
  );

export type DeployContainerApplicationPayload = {
  instance_name: string;
  version: string;
};

export const deployContainerApplication = async (
  applicationId: string,
  tier: number,
  payload: DeployContainerApplicationPayload,
): Promise<ApiDeployment> =>
  await apiFetch<ApiDeployment>(
    `/api/registered-applications/${encodeURIComponent(applicationId)}/tiers/${tier}/deployments`,
    { method: "POST", body: JSON.stringify(payload) },
  );

/** Single deployment with `events[]` and live `workflow_progress`. Poll
 *  every ~3s while the status is pending | deploying | updating | terminating. */
export const getDeployment = async (deploymentId: string): Promise<ApiDeployment> =>
  await apiFetch<ApiDeployment>(
    `/api/registered-applications/deployments/${encodeURIComponent(deploymentId)}`,
  );

export type ListDeploymentsQuery = {
  tier?: number;
  application_id?: string;
  /** Exact deployment subdomain or instance name. */
  instance?: string;
  status?: ApiDeploymentStatus;
  offset?: number;
  limit?: number;
};

export const listDeployments = async (
  query: ListDeploymentsQuery = {},
): Promise<ApiDeployment[]> => {
  const res = await apiFetch<{ items: ApiDeployment[]; total: number }>(
    `/api/registered-applications/deployments${toQuery(query)}`,
  );
  return res.items ?? [];
};

export const upgradeDeployment = async (
  deploymentId: string,
  version: string,
): Promise<ApiDeployment> =>
  await apiFetch<ApiDeployment>(
    `/api/registered-applications/deployments/${encodeURIComponent(deploymentId)}/upgrade`,
    { method: "POST", body: JSON.stringify({ version }) },
  );

/** Destructive: the backend requires `confirmation` to equal the instance name. */
export const terminateRegisteredDeployment = async (
  deploymentId: string,
  confirmation: string,
): Promise<ApiDeployment> =>
  await apiFetch<ApiDeployment>(
    `/api/registered-applications/deployments/${encodeURIComponent(deploymentId)}/terminate`,
    { method: "POST", body: JSON.stringify({ confirmation }) },
  );

/**
 * The live registered deployment behind one metrics instance, or null when the
 * namespace belongs to a legacy ada instance. Update and terminate both route
 * on this: a registered deployment owns its own type-specific pipeline.
 */
export const findRegisteredDeploymentForInstance = async (
  tier: number,
  instance: string,
): Promise<ApiDeployment | null> => {
  const deployments = await listDeployments({ tier, instance, limit: 200 });
  const deployment =
    deployments.find(
      (item) => item.status !== "terminated" && item.status !== "cancelled",
    ) ?? null;
  const matchesInstance =
    deployment?.subdomain === instance || deployment?.instance_name === instance;
  return matchesInstance ? deployment : null;
};

/* ---------------- GitHub connections ---------------- */

/** Mirrors backend GitHubConnectionOut — tokens are never returned. */
export type ApiGithubConnection = {
  id: string;
  display_name: string;
  connection_url: string | null;
  legacy?: boolean;
  created_by?: string | null;
  created_at?: string | null;
};

export type GithubConnectionPayload = {
  connection_name: string;
  repository_url: string;
  /** Write-only; encrypted at rest and never returned. */
  access_token: string;
};

/** Mirrors backend GitHubConnectionValidationOut. */
export type ValidateConnectionResponse = {
  valid: boolean;
  message: string;
  repository?: string | null;
};

export const listGithubConnections = async (): Promise<ApiGithubConnection[]> => {
  const res = await apiFetch<{ items: ApiGithubConnection[] }>("/api/github-connections");
  return res.items ?? [];
};

export const validateGithubConnectionRequest = async (
  payload: GithubConnectionPayload,
): Promise<ValidateConnectionResponse> =>
  await apiFetch<ValidateConnectionResponse>("/api/github-connections/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const createGithubConnection = async (
  payload: GithubConnectionPayload,
): Promise<ApiGithubConnection> =>
  await apiFetch<ApiGithubConnection>("/api/github-connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });

/* ---------------- LLM models ---------------- */

/** Mirrors backend LLMModelOut. */
export type ApiLlmModel = {
  model_name: string;
  provider: string | null;
  pricing_available: boolean;
  /** Set only when pricing is unavailable — render verbatim. */
  message: string | null;
};

/** Models Athena holds a price for — datalist suggestions, not a whitelist. */
export const listPricedLlmModels = async (): Promise<ApiLlmModel[]> => {
  const res = await apiFetch<{ items: ApiLlmModel[]; total: number }>(
    "/api/registered-applications/llm-models",
  );
  return res.items ?? [];
};

/** Resolves one typed model name. Warning only — registration still
 *  succeeds when `pricing_available` is false. */
export const checkLlmModelPricing = async (
  modelName: string,
  signal?: AbortSignal,
): Promise<ApiLlmModel> =>
  await apiFetch<ApiLlmModel>(
    `/api/registered-applications/llm-models/pricing?model_name=${encodeURIComponent(modelName)}`,
    { signal },
  );
