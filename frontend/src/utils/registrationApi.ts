/* Application registration (Epic 3) — backed by the registered-applications
 * and github-connections endpoints. The wizard's form values are mapped to
 * the API's request contracts here. */

import {
  createGithubConnection,
  listGithubConnections,
  registerContainerApplication,
  registerGithubApplication,
  updateRegisteredApplication,
  validateGithubConnectionRequest,
  type ApiParameterType,
  type ApiRegisteredApplicationDetail,
  type RegisterContainerApplicationPayload,
  type RegisterGithubApplicationPayload,
} from "@api/services/registeredApplications";

export type GithubConnection = {
  id: string;
  name: string;
  repoUrl: string;
};

export type TriggerMethod = "repository_dispatch" | "workflow_dispatch";
export type PullPolicy = "Always" | "IfNotPresent" | "Never";

export type KeyValue = { key: string; value: string };

/** GitHub Workflow parameter types — the deploy-time form is generated
 *  from these, one field per parameter, matching Story 5.1's "dynamically
 *  generated from the registered deployment parameters." */
export type ParamType = "text" | "number" | "boolean" | "key-value";

export type ParameterDef = {
  name: string;
  type: ParamType;
  /** Used when type is "text" or "number" (kept as a string; parsed at use). */
  value: string;
  /** Used when type is "boolean". */
  boolValue: boolean;
  /** Used when type is "key-value" — the nested key. */
  kvKey: string;
  /** Used when type is "key-value" — the nested value. */
  kvValue: string;
};

export function emptyParameterDef(): ParameterDef {
  return { name: "", type: "text", value: "", boolValue: false, kvKey: "", kvValue: "" };
}

export type GithubWorkflowRegistration = {
  kind: "github";
  name: string;
  description: string;
  connectionId: string;
  codeRepository: string;
  workflowRepository: string;
  triggerMethod: TriggerMethod;
  workflowFile: string;
  branch: string;
  parameters: ParameterDef[];
  llmEndpoint: string;
  llmApiToken: string;
  llmModelName: string;
};

export type ContainerizedRegistration = {
  kind: "container";
  name: string;
  description: string;
  containerRegistry: string;
  imageRegistry: string;
  registryUsername: string;
  registryPassword: string;
  tag: string;
  port: string;
  pullPolicy: PullPolicy;
  exposePublicly: boolean;
  gpuRequest: string;
  cpuRequest: string;
  memoryRequest: string;
  replicas: string;
  storage: string;
  parameters: KeyValue[];
  llmEndpoint: string;
  llmApiToken: string;
  llmModelName: string;
};

export type AppRegistration = GithubWorkflowRegistration | ContainerizedRegistration;

/* ---------------- Saved GitHub connections ---------------- */

export async function fetchGithubConnections(): Promise<GithubConnection[]> {
  const list = await listGithubConnections();
  return list.map((c) => ({
    id: String(c.id),
    name: c.display_name,
    repoUrl: c.connection_url ?? "",
  }));
}

export type ValidateConnectionResult = { success: boolean; message: string };

/** Dry-run check before saving — reports reachability and scope problems. */
export async function validateGithubConnection(
  connectionName: string,
  repoUrl: string,
  token: string,
): Promise<ValidateConnectionResult> {
  try {
    const res = await validateGithubConnectionRequest({
      connection_name: connectionName.trim(),
      repository_url: repoUrl.trim(),
      access_token: token,
    });
    return { success: res.valid, message: res.message };
  } catch (err) {
    return {
      success: false,
      message: err instanceof Error ? err.message : "Could not validate this connection.",
    };
  }
}

export async function saveGithubConnection(
  name: string,
  repoUrl: string,
  token: string,
): Promise<GithubConnection> {
  const saved = await createGithubConnection({
    connection_name: name.trim(),
    repository_url: repoUrl.trim(),
    access_token: token,
  });
  return {
    id: String(saved.id),
    name: saved.display_name,
    repoUrl: saved.connection_url ?? repoUrl,
  };
}

/* ---------------- Container registries ---------------- */

export type ContainerRegistryOption = { value: string; label: string };

/** Registries offered in the wizard. `value` is what's sent as
 *  `container.registry` — keys the backend's registry map understands
 *  (`docker.io` → Docker Hub, `ecr` → Amazon ECR). No listing endpoint. */
export async function fetchContainerRegistries(): Promise<ContainerRegistryOption[]> {
  return [
    { value: "Docker Hub", label: "Docker Hub" },
    { value: "Amazon ECR", label: "Amazon ECR" },
  ];
}

/* ---------------- Registration submission ---------------- */

/** Backend ParameterType: text | number | boolean | select | key_value. */
function toApiParamType(type: ParamType): ApiParameterType {
  if (type === "key-value") return "key_value";
  return type;
}

export function fromApiParamType(type: ApiParameterType): ParamType {
  switch (type) {
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "key_value":
      return "key-value";
    default:
      // "select" has no dedicated control yet — a free-text field is the
      // safe rendering until option lists are surfaced.
      return "text";
  }
}

/** A blank secret is left out: required to register (the wizard insists),
 *  and on an edit it means "keep the stored one". */
const secret = (key: string, value: string) => (value ? { [key]: value } : {});

function githubPayload(registration: GithubWorkflowRegistration): RegisterGithubApplicationPayload {
  return {
    application_type: "github_workflow",
    name: registration.name.trim(),
    description: registration.description.trim(),
    github: {
      github_connection: registration.connectionId,
      trigger_method: registration.triggerMethod,
      repository: registration.workflowRepository.trim(),
      // Required by the wizard.
      code_repository: registration.codeRepository.trim(),
      workflow_file_path: registration.workflowFile.trim(),
      ref: registration.branch.trim() || "main",
    },
    parameters: registration.parameters
      .filter((p) => p.name.trim())
      .map((p) => ({ key: p.name.trim(), type: toApiParamType(p.type) })),
    llm: {
      endpoint: registration.llmEndpoint.trim(),
      ...secret("api_token", registration.llmApiToken),
      model_name: registration.llmModelName.trim(),
    },
  };
}

function containerPayload(
  registration: ContainerizedRegistration,
): RegisterContainerApplicationPayload {
  // ContainerConfigurationCreate: optional resource fields are `str | None`
  // with min_length=1, so a blank field must be omitted, not sent as "".
  const optional = (key: string, value: string) =>
    value.trim() ? { [key]: value.trim() } : {};

  return {
    application_type: "containerized",
    name: registration.name.trim(),
    description: registration.description.trim(),
    container: {
      registry: registration.containerRegistry,
      image_registry: registration.imageRegistry.trim(),
      registry_username: registration.registryUsername.trim(),
      ...secret("registry_password", registration.registryPassword),
      tag: registration.tag.trim(),
      port: Number(registration.port),
      pull_policy: registration.pullPolicy,
      expose_public_service: registration.exposePublicly,
      ...optional("gpu_request", registration.gpuRequest),
      ...optional("cpu_request", registration.cpuRequest),
      ...optional("memory_request", registration.memoryRequest),
      ...optional("scaling", registration.replicas),
      ...optional("storage", registration.storage),
    },
    parameters: registration.parameters
      .filter((p) => p.key.trim())
      .map((p) => ({ key: p.key.trim(), value: p.value })),
    llm: {
      endpoint: registration.llmEndpoint.trim(),
      ...secret("api_token", registration.llmApiToken),
      model_name: registration.llmModelName.trim(),
    },
  };
}

export async function registerApplication(
  registration: AppRegistration,
): Promise<{ id: string }> {
  const created =
    registration.kind === "github"
      ? await registerGithubApplication(githubPayload(registration))
      : await registerContainerApplication(containerPayload(registration));
  return { id: created.id };
}

/** PUT /{id} — saved as the template's next version; the result's number. */
export async function saveApplicationEdit(
  applicationId: string,
  registration: AppRegistration,
): Promise<{ version: number }> {
  const saved = await updateRegisteredApplication(
    applicationId,
    registration.kind === "github" ? githubPayload(registration) : containerPayload(registration),
  );
  return { version: saved.version };
}

/** A saved template as the wizard's values, for editing. The secrets are
 *  never sent back, so they start blank ("keep the stored one"). */
export function registrationFromDetail(detail: ApiRegisteredApplicationDetail): AppRegistration {
  const llm = {
    llmEndpoint: detail.llm?.endpoint ?? "",
    llmApiToken: "",
    llmModelName: detail.llm?.model_name ?? "",
  };
  if (detail.application_type === "github_workflow") {
    return {
      kind: "github",
      name: detail.name,
      description: detail.description ?? "",
      connectionId: detail.github.github_connection,
      codeRepository: detail.github.code_repository ?? "",
      workflowRepository: detail.github.repository,
      triggerMethod: detail.github.trigger_method,
      workflowFile: detail.github.workflow_file_path,
      branch: detail.github.ref,
      parameters: detail.parameters.map((p) => ({
        ...emptyParameterDef(),
        name: p.key,
        type: fromApiParamType(p.type),
      })),
      ...llm,
    };
  }
  const container = detail.container;
  return {
    kind: "container",
    name: detail.name,
    description: detail.description ?? "",
    containerRegistry: container.registry,
    imageRegistry: container.image_registry,
    registryUsername: container.registry_username ?? "",
    registryPassword: "",
    tag: container.tag ?? "",
    port: container.port == null ? "" : String(container.port),
    pullPolicy: container.pull_policy,
    exposePublicly: container.expose_public_service,
    gpuRequest: container.gpu_request ?? "",
    cpuRequest: container.cpu_request ?? "",
    memoryRequest: container.memory_request ?? "",
    replicas: container.scaling ?? "",
    storage: container.storage ?? "",
    parameters: detail.parameters.map((p) => ({ key: p.key, value: p.value })),
    ...llm,
  };
}
