/* Application registration (Epic 3) — backed by the registered-applications
 * and github-connections endpoints. The wizard's form values are mapped to
 * the API's request contracts here. */

import {
  createContainerSecret,
  createGithubConnection,
  deleteContainerSecret,
  duplicateRegisteredApplication,
  listGithubConnections,
  registerContainerApplication,
  registerGithubApplication,
  updateContainerSecret,
  updateRegisteredApplication,
  validateGithubConnectionRequest,
  type ApiGithubParameter,
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

export const TIER_NUMBERS = ["1", "2", "3", "4"] as const;

/** A container environment variable. A secret one is stored by the secret
 *  provider, not in the template: `value` is then the value to write (blank
 *  keeps a stored one), and `secretId` names the stored secret it came from. */
export type KeyValue = {
  key: string;
  value: string;
  /** Tier number → the value on that tier, as typed ("" = the default). */
  tierValues: Record<string, string>;
  secret: boolean;
  secretId?: string;
};

export function emptyVariable(): KeyValue {
  return { key: "", value: "", tierValues: {}, secret: false };
}

/** GitHub Workflow parameter types — the deploy-time form is generated
 *  from these, one field per parameter, matching Story 5.1's "dynamically
 *  generated from the registered deployment parameters." "select" is shown
 *  as Choice. */
export type ParamType = "text" | "number" | "boolean" | "select" | "key-value";

/** One parameter: its definition when registering, its value when deploying. */
export type ParameterDef = {
  name: string;
  type: ParamType;
  /** Shown on the deploy form instead of the name ("" = the name). */
  label: string;
  /** Help text on the deploy form. */
  description: string;
  required: boolean;
  /** Choice parameters: the choices. */
  options: string[];
  /** Text, number, choice and boolean ("true"/"false"), kept as typed. At
   *  registration it is the default; on the deploy form, the value. */
  value: string;
  /** Used when type is "key-value" — the nested key. */
  kvKey: string;
  /** Used when type is "key-value" — the nested value. */
  kvValue: string;
  /** Tier number → the value on that tier, as typed ("" = the default). */
  tierValues: Record<string, string>;
};

export function emptyParameterDef(): ParameterDef {
  return {
    name: "",
    type: "text",
    label: "",
    description: "",
    required: true,
    options: [],
    value: "",
    kvKey: "",
    kvValue: "",
    tierValues: {},
  };
}

/** The choices as the user means them: trimmed, none blank. */
export const choices = (options: string[]) => options.map((o) => o.trim()).filter(Boolean);

/** Parameters C2AI fills in itself, and from what. A workflow that declares
 *  one receives it; nobody is asked for it when deploying. */
export const C2AI_FILLED: Record<string, string> = {
  customer_name: "the Customer Name entered when deploying",
  env_instance: "the Instance Name entered when deploying",
  instance_name: "the instance name",
  slack_user: "the person deploying",
  tier: "the tier deployed to",
  target_tier: "the tier deployed to",
  namespace: "the tier's namespace (tier1…tier4)",
  branch: "the Version picked when deploying",
  provider: "the tier (tier1…tier4)",
  deployment_id: "C2AI's ID for the deployment",
};

/** Sent to every workflow whether or not it declares them: never a parameter. */
export const ALWAYS_SENT = new Set(["provider", "deployment_id"]);

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

/** "owner/repo" of a GitHub repository URL, or null. */
export function repositoryFromUrl(url: string): string | null {
  const match = /github\.[^/]+\/([^/\s]+)\/([^/\s#?]+?)(?:\.git)?\/?(?:[#?].*)?$/i.exec(url.trim());
  return match ? `${match[1]}/${match[2]}` : null;
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

export type ContainerRegistryOption = {
  value: string;
  label: string;
  /** What Image Registry holds for this registry. */
  imageHint: string;
};

/** Registries offered in the wizard. `value` is what's sent as
 *  `container.registry` — names the backend's registry client understands. */
export const CONTAINER_REGISTRIES: ContainerRegistryOption[] = [
  { value: "Docker Hub", label: "Docker Hub", imageHint: "company/application" },
  {
    value: "Amazon ECR",
    label: "Amazon ECR",
    imageHint: "<account>.dkr.ecr.<region>.amazonaws.com/application",
  },
  { value: "GitHub Container Registry", label: "GitHub Container Registry", imageHint: "owner/application" },
  { value: "Private Registry", label: "Other registry", imageHint: "registry.example.com/team/application" },
];

export async function fetchContainerRegistries(): Promise<ContainerRegistryOption[]> {
  return CONTAINER_REGISTRIES;
}

/* ---------------- Parameters ---------------- */

/** Backend ParameterType: text | number | boolean | select | key_value. */
function toApiParamType(type: ParamType): ApiParameterType {
  if (type === "key-value") return "key_value";
  return type;
}

export function fromApiParamType(type: ApiParameterType): ParamType {
  return type === "key_value" ? "key-value" : type;
}

/** A scalar parameter value as the API holds it; null when blank. */
function typedValue(type: ParamType, text: string): unknown {
  if (text.trim() === "") return null;
  if (type === "number") return Number(text);
  if (type === "boolean") return text === "true";
  return text;
}

/** An API value as the form's text. */
function valueText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function toApiParameter(p: ParameterDef): ApiGithubParameter {
  const tierDefaults =
    p.type === "key-value"
      ? {}
      : Object.fromEntries(
          Object.entries(p.tierValues)
            .filter(([, text]) => text.trim() !== "")
            .map(([tier, text]) => [tier, typedValue(p.type, text)]),
        );
  return {
    key: p.name.trim(),
    type: toApiParamType(p.type),
    label: p.label.trim() || null,
    description: p.description.trim() || null,
    required: p.required,
    default:
      p.type === "key-value"
        ? p.kvKey.trim()
          ? { [p.kvKey.trim()]: p.kvValue }
          : null
        : typedValue(p.type, p.value),
    options: p.type === "select" ? choices(p.options) : [],
    tier_defaults: tierDefaults,
  };
}

export function fromApiParameter(p: ApiGithubParameter): ParameterDef {
  const pair =
    p.default && typeof p.default === "object"
      ? Object.entries(p.default as Record<string, string>)[0]
      : undefined;
  return {
    ...emptyParameterDef(),
    name: p.key,
    type: fromApiParamType(p.type),
    label: p.label ?? "",
    description: p.description ?? "",
    required: p.required ?? true,
    options: p.options ?? [],
    value: pair ? "" : valueText(p.default),
    kvKey: pair?.[0] ?? "",
    kvValue: pair?.[1] ?? "",
    tierValues: Object.fromEntries(
      Object.entries(p.tier_defaults ?? {}).map(([tier, value]) => [tier, valueText(value)]),
    ),
  };
}

/** What a parameter holds on the deploy form for one tier: its tier value, or its default. */
export function prefilledFor(p: ParameterDef, tier: number): ParameterDef {
  const onTier = p.tierValues[String(tier)];
  return { ...p, value: onTier !== undefined && onTier !== "" ? onTier : p.value };
}

/* ---------------- Registration submission ---------------- */

/** A blank secret is left out: required to register (the wizard insists),
 *  and on an edit or a copy it means "keep the stored one". */
const secret = (key: string, value: string) => (value ? { [key]: value } : {});
const optional = (key: string, value: string) => (value.trim() ? { [key]: value.trim() } : {});

function githubPayload(registration: GithubWorkflowRegistration): RegisterGithubApplicationPayload {
  return {
    application_type: "github_workflow",
    name: registration.name.trim(),
    description: registration.description.trim(),
    github: {
      github_connection: registration.connectionId,
      trigger_method: registration.triggerMethod,
      repository: registration.workflowRepository.trim(),
      // Blank: the code lives in the workflow repository.
      ...optional("code_repository", registration.codeRepository),
      workflow_file_path: registration.workflowFile.trim(),
      ref: registration.branch.trim() || "main",
    },
    parameters: registration.parameters.filter((p) => p.name.trim()).map(toApiParameter),
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
  // ContainerConfigurationCreate: optional fields are `str | None` with
  // min_length=1, so a blank field must be omitted, not sent as "". A blank
  // username means a public image, with no password either.
  const username = registration.registryUsername.trim();
  return {
    application_type: "containerized",
    name: registration.name.trim(),
    description: registration.description.trim(),
    container: {
      registry: registration.containerRegistry,
      image_registry: registration.imageRegistry.trim(),
      ...optional("registry_username", username),
      ...(username ? secret("registry_password", registration.registryPassword) : {}),
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
    // Secret variables are stored separately (syncContainerSecrets).
    parameters: registration.parameters
      .filter((p) => p.key.trim() && !p.secret)
      .map((p) => ({
        key: p.key.trim(),
        value: p.value,
        tier_values: Object.fromEntries(
          Object.entries(p.tierValues).filter(([, value]) => value !== ""),
        ),
      })),
    llm: {
      endpoint: registration.llmEndpoint.trim(),
      ...secret("api_token", registration.llmApiToken),
      model_name: registration.llmModelName.trim(),
    },
  };
}

const payloadOf = (registration: AppRegistration) =>
  registration.kind === "github" ? githubPayload(registration) : containerPayload(registration);

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
  const saved = await updateRegisteredApplication(applicationId, payloadOf(registration));
  return { version: saved.version };
}

/** POST /{id}/duplicate — a new template; secrets left blank are copied. */
export async function saveApplicationCopy(
  sourceId: string,
  registration: AppRegistration,
): Promise<{ id: string }> {
  const created = await duplicateRegisteredApplication(sourceId, payloadOf(registration));
  return { id: created.id };
}

/* ---------------- Secret environment variables ---------------- */

/** A Kubernetes-safe secret name for an environment variable. */
export const secretName = (variable: string) =>
  variable
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63) || "secret";

/** Store the secret variables of `rows` for the template, and delete those of
 *  `stored` no longer there. Returns what failed, as sentences. */
export async function syncContainerSecrets(
  applicationId: string,
  rows: KeyValue[],
  stored: Array<{ id: string; environment_variable: string }>,
): Promise<string[]> {
  const failures: string[] = [];
  const attempt = async (variable: string, call: () => Promise<unknown>) => {
    try {
      await call();
    } catch (err) {
      failures.push(`${variable}: ${err instanceof Error ? err.message : "not stored"}`);
    }
  };
  const kept = new Set<string>();
  for (const row of rows.filter((r) => r.secret && r.key.trim())) {
    const variable = row.key.trim();
    const existing = stored.find((s) => s.id === row.secretId);
    if (!existing) {
      await attempt(variable, () =>
        createContainerSecret(applicationId, {
          name: secretName(variable),
          environment_variable: variable,
          secret_value: row.value,
        }),
      );
      continue;
    }
    kept.add(existing.id);
    const renamed = existing.environment_variable !== variable;
    if (!renamed && !row.value) continue;
    await attempt(variable, () =>
      updateContainerSecret(applicationId, existing.id, {
        ...(renamed ? { name: secretName(variable), environment_variable: variable } : {}),
        ...secret("secret_value", row.value),
      }),
    );
  }
  for (const gone of stored.filter((s) => !kept.has(s.id))) {
    await attempt(gone.environment_variable, () => deleteContainerSecret(applicationId, gone.id));
  }
  return failures;
}

/* ---------------- Filling the wizard from a saved template ---------------- */

/** A saved template as the wizard's values, for editing or copying. The
 *  secrets are never sent back, so they start blank ("keep the stored one"). */
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
      parameters: detail.parameters.map(fromApiParameter),
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
    parameters: detail.parameters.map((p) => ({
      ...emptyVariable(),
      key: p.key,
      value: p.value,
      tierValues: { ...(p.tier_values ?? {}) },
    })),
    ...llm,
  };
}
