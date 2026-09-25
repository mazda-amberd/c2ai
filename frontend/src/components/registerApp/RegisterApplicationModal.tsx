import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Box, Check, Github, ListChecks, Loader2, Plus, X } from "lucide-react";
import { useFieldArray, useForm } from "react-hook-form";

import { Dialog, DialogContent } from "@ui/dialog";
import { useToast } from "@components/Toast";
import { useLlmModelPricing, useLlmModelSuggestions } from "@hooks/useLlmModelPricing";
import {
  ACCENT,
  Field,
  FIELD_CLASS,
  PANEL_BACKGROUND,
  Select,
} from "./wizardStyles";
import CheckList from "./CheckList";
import EnvironmentEditor from "./EnvironmentEditor";
import ParameterEditor from "./ParameterEditor";
import {
  checkContainerImage,
  getContainerSecretStorage,
  getRegisteredApplication,
  inspectGithubWorkflow,
  listContainerSecrets,
  type ApiContainerSecret,
  type ApiImageCheck,
  type ApiWorkflowInspection,
} from "@api/services/registeredApplications";
import { parseImageReference } from "@/utils/imageReference";
import { isApplicationNameTaken, type RegisteredApp } from "@/utils/registeredAppsApi";
import {
  ALWAYS_SENT,
  choices,
  emptyParameterDef,
  emptyVariable,
  fetchContainerRegistries,
  fetchGithubConnections,
  fromApiParameter,
  registerApplication,
  registrationFromDetail,
  repositoryFromUrl,
  saveApplicationCopy,
  saveApplicationEdit,
  saveGithubConnection,
  syncContainerSecrets,
  validateGithubConnection,
  type AppRegistration,
  type ContainerRegistryOption,
  type GithubConnection,
  type KeyValue,
  type ParameterDef,
  type PullPolicy,
  type TriggerMethod,
} from "@/utils/registrationApi";

type Kind = "github" | "container";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** After a registration, a saved edit, or a copy. */
  onRegistered?: () => void;
  /** The template to edit. */
  editing?: RegisteredApp | null;
  /** The template to copy into a new one. */
  duplicating?: RegisteredApp | null;
};

type StepDef = {
  id: string;
  kicker: string;
  subtitle: string;
  icon: "cube" | "github" | "container";
  final?: boolean;
};

const GITHUB_STEPS: StepDef[] = [
  {
    id: "basic",
    kicker: "Basic Information",
    subtitle: "Enter basic information about your application.",
    icon: "cube",
  },
  {
    id: "type",
    kicker: "Application Type",
    subtitle: "Choose how you want to deploy your application.",
    icon: "cube",
  },
  {
    id: "workflow",
    kicker: "Workflow Configuration",
    subtitle: "Configure how C2AI will trigger your workflow.",
    icon: "github",
  },
  {
    id: "params",
    kicker: "Configure Parameters",
    subtitle: "Define the inputs (parameters) this workflow expects.",
    icon: "github",
  },
  {
    id: "llm",
    kicker: "LLM Configuration",
    subtitle:
      "Provide the LLM endpoint this application uses. Required for every application type.",
    icon: "cube",
    final: true,
  },
];

const CONTAINER_STEPS: StepDef[] = [
  {
    id: "basic",
    kicker: "Basic Information",
    subtitle: "Enter basic information about your application.",
    icon: "cube",
  },
  {
    id: "type",
    kicker: "Application Type",
    subtitle: "Choose how you want to deploy your application.",
    icon: "cube",
  },
  {
    id: "container",
    kicker: "Container / Image Configuration",
    subtitle: "Provide the container image details.",
    icon: "container",
  },
  {
    id: "resources",
    kicker: "Resources & Scaling",
    subtitle:
      "Set the default compute resources, scaling, and storage for this application.",
    icon: "container",
  },
  {
    id: "params",
    kicker: "Environment Variables",
    subtitle: "Define the environment variables applied to every deployment.",
    icon: "container",
  },
  {
    id: "llm",
    kicker: "LLM Configuration",
    subtitle:
      "Provide the LLM endpoint this application uses. Required for every application type.",
    icon: "cube",
    final: true,
  },
];

/* Shared wizard visual language (colors/fields) lives in wizardStyles.tsx so
 * this and DeployApplicationModal stay pixel-identical. */

/* ---------------- Choice list (radio cards) ---------------- */

function ChoiceList<T extends string>({
  options,
  value,
  onChange,
}: {
  options: Array<{
    value: T;
    icon?: React.ReactNode;
    title: string;
    desc: string;
  }>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="radiogroup" className="space-y-2">
      {options.map((opt) => {
        const selected = opt.value === value;
        return (
          <label
            key={opt.value}
            onClick={() => onChange(opt.value)}
            className="flex cursor-pointer items-center gap-3 rounded-[8px] border p-3 transition-colors"
            style={{
              borderColor: selected ? ACCENT : "#1c2836",
              background: selected ? "rgba(32,171,199,0.08)" : "transparent",
            }}
          >
            <input
              type="radio"
              checked={selected}
              onChange={() => onChange(opt.value)}
              className="sr-only"
            />
            {opt.icon && (
              <span className="shrink-0 text-[#eef2f6]">{opt.icon}</span>
            )}
            <span className="flex-1">
              <strong className="block text-sm text-[#eef2f6]">
                {opt.title}
              </strong>
              <span className="text-xs text-[#8b97a5]">{opt.desc}</span>
            </span>
            <span
              className="h-4 w-4 shrink-0 rounded-full border-2"
              style={{
                borderColor: selected ? ACCENT : "#3a4a5c",
                background: selected ? ACCENT : "transparent",
              }}
            />
          </label>
        );
      })}
    </div>
  );
}

/* ---------------- Toggle switch ---------------- */

function Switch({
  checked,
  onChange,
  title,
  helper,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  title: string;
  helper: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-[8px] border border-[#1c2836] p-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className="relative h-5 w-9 shrink-0 rounded-full transition-colors"
        style={{ background: checked ? ACCENT : "#2b3a4a" }}
      >
        <span
          className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform"
          style={{ transform: checked ? "translateX(16px)" : "translateX(0)" }}
        />
      </button>
      <div className="min-w-0 flex-1">
        <div
          className="truncate text-sm font-semibold text-[#eef2f6]"
          title={title}
        >
          {title}
        </div>
        <div className="text-[11.5px] text-[#8b97a5]">{helper}</div>
      </div>
    </div>
  );
}

/* ---------------- react-hook-form value shapes ---------------- */

type GithubFormValues = {
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

type ContainerFormValues = {
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

/* Text fields start empty except the ones with a house default: the
 * branch, the LLM gateway every Amberd application uses, and a small
 * container footprint. The enum controls (trigger method radio, pull-policy
 * select) carry a selection, since they can't be blank. */

const DEFAULT_LLM = {
  llmEndpoint: "http://amberd-llm-gateway:8010",
  llmApiToken: "EMPTY",
  llmModelName: "qwen3-coder-next",
};

const emptyGithubValues = (): GithubFormValues => ({
  name: "",
  description: "",
  connectionId: "",
  codeRepository: "",
  workflowRepository: "",
  triggerMethod: "workflow_dispatch",
  workflowFile: "",
  branch: "main",
  parameters: [],
  ...DEFAULT_LLM,
});

const emptyContainerValues = (): ContainerFormValues => ({
  name: "",
  description: "",
  containerRegistry: "",
  imageRegistry: "",
  registryUsername: "",
  registryPassword: "",
  tag: "",
  port: "",
  pullPolicy: "IfNotPresent",
  exposePublicly: false,
  gpuRequest: "",
  cpuRequest: "500m",
  memoryRequest: "512Mi",
  replicas: "1",
  storage: "",
  parameters: [],
  ...DEFAULT_LLM,
});

const STORAGE_SIZES = ["10Gi", "20Gi", "50Gi", "100Gi"];
/** A Kubernetes quantity: 25Gi, 512Mi, 1.5Ti… */
const QUANTITY = /^\d+(\.\d+)?(Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?$/;
/** What the secret provider accepts as a variable name. */
const SECRET_VARIABLE = /^[A-Za-z_][A-Za-z0-9_]*$/;
/** Checks without which nothing was read from the workflow. */
const READ_CHECKS = new Set(["Connection", "Repository", "Branch / Ref", "Workflow file"]);

/** "chat copy", or "chat copy 2"… whichever name is free. */
function copyName(name: string): string {
  for (let n = 1; ; n += 1) {
    const candidate = n === 1 ? `${name} copy` : `${name} copy ${n}`;
    if (!isApplicationNameTaken(candidate)) return candidate;
  }
}

/** Why these GitHub parameters cannot be saved, or null. */
function parameterProblem(rows: ParameterDef[]): string | null {
  if (rows.some((p) => !p.name.trim() && (p.label || p.value || p.description))) {
    return "Every parameter needs a name.";
  }
  const names = rows.map((p) => p.name.trim()).filter(Boolean);
  const twice = names.find((n, i) => names.indexOf(n) !== i);
  if (twice) return `There are two parameters named '${twice}'.`;
  for (const p of rows.filter((row) => row.name.trim())) {
    const name = p.name.trim();
    const options = choices(p.options);
    if (p.type === "select" && options.length === 0) {
      return `Give the choice parameter '${name}' its choices.`;
    }
    const values = [p.value, ...Object.values(p.tierValues)].filter((v) => v.trim() !== "");
    if (p.type === "number" && values.some((v) => !Number.isFinite(Number(v)))) {
      return `The values of '${name}' must be numbers.`;
    }
    if (p.type === "select" && values.some((v) => !options.includes(v))) {
      return `The values of '${name}' must be among its choices.`;
    }
  }
  return null;
}

/** Why these environment variables cannot be saved, or null. */
function variableProblem(rows: KeyValue[], secretsAvailable: boolean | null): string | null {
  const used = rows.filter((r) => r.key.trim() || r.value);
  if (used.some((r) => !r.key.trim())) return "Every environment variable needs a key.";
  const keys = used.map((r) => r.key.trim());
  const twice = keys.find((k, i) => keys.indexOf(k) !== i);
  if (twice) return `There are two variables named '${twice}'.`;
  for (const r of used.filter((row) => row.secret)) {
    const key = r.key.trim();
    if (!SECRET_VARIABLE.test(key)) {
      return `The secret '${key}' needs a name made of letters, digits and underscores.`;
    }
    if (!r.value && !r.secretId) return `Enter the value of the secret '${key}', or remove it.`;
    if (!secretsAvailable && (!r.secretId || r.value)) {
      return "Secret storage isn't configured on this server, so secret values can't be stored.";
    }
  }
  return null;
}

export default function RegisterApplicationModal({
  open,
  onOpenChange,
  onRegistered,
  editing = null,
  duplicating = null,
}: Props) {
  // The template the wizard starts from: edited in place, or copied.
  const source = editing ?? duplicating;
  const copying = !editing && !!duplicating;
  const [kind, setKind] = useState<Kind>("github");
  const [stepIndex, setStepIndex] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  // The template toast system surfaces validation, as the template does.
  const { showToast } = useToast();

  /* Editing or copying: the saved template fills the wizard. "loading"
   * until it has, an error if it could not be read. */
  const [load, setLoad] = useState<{ status: "loading" | "ready" | "error"; message: string }>({
    status: "ready",
    message: "",
  });
  // Whether the template already has an LLM token, which a blank field keeps.
  const [storedLlmToken, setStoredLlmToken] = useState(false);
  /* A saved template may name a connection that is not a saved one (ADA's
   * "athena-environment"): its deploys use the server's own GitHub token.
   * It stays an option, so an edit need not change it. */
  const [unsavedConnection, setUnsavedConnection] = useState<string | null>(null);
  // The source's registry username: its stored password carries over for it.
  const [storedUsername, setStoredUsername] = useState("");
  // Secret variables stored for the template being edited (not for a copy).
  const [storedSecrets, setStoredSecrets] = useState<ApiContainerSecret[]>([]);
  // Whether this server can keep secret values (null while asking).
  const [secretsAvailable, setSecretsAvailable] = useState<boolean | null>(null);
  const sourceId = source?.id;

  // The last workflow and image checks, each for the settings it was made with.
  const [inspection, setInspection] = useState<(ApiWorkflowInspection & { key: string }) | null>(
    null,
  );
  const [inspecting, setInspecting] = useState(false);
  const [imageCheck, setImageCheck] = useState<(ApiImageCheck & { key: string }) | null>(null);
  const [checkingImage, setCheckingImage] = useState(false);
  const [imageReference, setImageReference] = useState("");
  const [referenceError, setReferenceError] = useState("");
  // The Workflow Repository last filled in from a connection's URL.
  const derivedRepository = useRef("");

  const githubFormApi = useForm<GithubFormValues>({
    mode: "onChange",
    defaultValues: emptyGithubValues(),
  });
  const containerFormApi = useForm<ContainerFormValues>({
    mode: "onChange",
    defaultValues: emptyContainerValues(),
  });

  const githubParams = useFieldArray({
    control: githubFormApi.control,
    name: "parameters",
  });
  const containerParams = useFieldArray({
    control: containerFormApi.control,
    name: "parameters",
  });

  const githubForm = githubFormApi.watch();
  const containerForm = containerFormApi.watch();

  // GitHub connection sub-state ("+ Add new connection" inline form)
  const [connections, setConnections] = useState<GithubConnection[]>([]);
  const [showConnForm, setShowConnForm] = useState(false);
  const [savingConnection, setSavingConnection] = useState(false);
  const [newConn, setNewConn] = useState({ name: "", repoUrl: "", token: "" });
  const [validation, setValidation] = useState<{
    status: "idle" | "validating" | "success" | "failed";
    message: string;
  }>({ status: "idle", message: "" });

  const [registries, setRegistries] = useState<ContainerRegistryOption[]>([]);

  // An edit or a copy keeps the application's type, so it has no type step.
  const steps = (kind === "container" ? CONTAINER_STEPS : GITHUB_STEPS).filter(
    (s) => !source || s.id !== "type",
  );
  const stepIndexClamped = Math.min(stepIndex, steps.length - 1);
  const step = steps[stepIndexClamped];
  const name = kind === "container" ? containerForm.name : githubForm.name;

  // LLM step: pricing check on every model-name change + datalist suggestions.
  const llmModelName =
    kind === "container" ? containerForm.llmModelName : githubForm.llmModelName;
  const llmPricing = useLlmModelPricing(step.id === "llm" ? llmModelName : "");
  const llmSuggestions = useLlmModelSuggestions(open && step.id === "llm");

  // Option lists only — the selects stay on their "Select…" placeholder
  // until the user picks something.
  useEffect(() => {
    if (!open) return;
    fetchGithubConnections()
      .then(setConnections)
      .catch(() => setConnections([]));
    fetchContainerRegistries().then(setRegistries);
    getContainerSecretStorage()
      .then((storage) => setSecretsAvailable(storage.configured))
      .catch(() => setSecretsAvailable(false));
  }, [open]);

  useEffect(() => {
    if (!open || !sourceId) return;
    let cancelled = false;
    setLoad({ status: "loading", message: "" });
    // The connections too, so the saved one is an option when the form
    // fills; and a container's secret variables, which live apart from it.
    Promise.all([
      getRegisteredApplication(sourceId),
      fetchGithubConnections().catch(() => [] as GithubConnection[]),
    ])
      .then(async ([detail, saved]) => {
        const secrets =
          detail.application_type === "containerized"
            ? await listContainerSecrets(sourceId).catch(() => [] as ApiContainerSecret[])
            : [];
        return { detail, saved, secrets };
      })
      .then(({ detail, saved, secrets }) => {
        if (cancelled) return;
        setConnections(saved);
        const { kind: savedKind, ...values } = registrationFromDetail(detail);
        // A template saved without LLM settings starts from the house ones,
        // as a new registration does.
        if (detail.llm === null) Object.assign(values, DEFAULT_LLM);
        if (copying) values.name = copyName(detail.name);
        if (savedKind === "github") {
          const github = values as GithubFormValues;
          githubFormApi.reset(github);
          const known = saved.some((c) => c.id === github.connectionId);
          setUnsavedConnection(github.connectionId && !known ? github.connectionId : null);
        } else {
          const container = values as ContainerFormValues;
          // A copy has none of the secrets yet: their values must be entered again.
          container.parameters = [
            ...container.parameters,
            ...secrets.map((stored) => ({
              ...emptyVariable(),
              key: stored.environment_variable,
              secret: true,
              secretId: copying ? undefined : stored.id,
            })),
          ];
          containerFormApi.reset(container);
          setStoredUsername(container.registryUsername);
          setStoredSecrets(copying ? [] : secrets);
        }
        setKind(savedKind);
        setStepIndex(0);
        setStoredLlmToken(detail.llm !== null);
        setLoad({ status: "ready", message: "" });
      })
      .catch((err) => {
        if (cancelled) return;
        setLoad({
          status: "error",
          message: err instanceof Error ? err.message : "Could not load the application.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [open, sourceId, copying, githubFormApi, containerFormApi]);

  // Picking a connection fills in the Workflow Repository from its URL,
  // unless one was typed.
  useEffect(() => {
    const connection = connections.find((c) => c.id === githubForm.connectionId);
    const repository = connection ? repositoryFromUrl(connection.repoUrl) : null;
    if (!repository) return;
    const current = githubFormApi.getValues("workflowRepository").trim();
    if (!current || current === derivedRepository.current) {
      githubFormApi.setValue("workflowRepository", repository);
    }
    derivedRepository.current = repository;
  }, [githubForm.connectionId, connections, githubFormApi]);

  /** The Basic step's fields are entered before the type is chosen, into
   *  whichever form is active at the time. Switching type must carry them
   *  over, otherwise the other form submits an empty name (422). */
  const handleKindChange = (next: Kind) => {
    if (next === kind) return;
    if (next === "container") {
      containerFormApi.setValue("name", githubFormApi.getValues("name"));
      containerFormApi.setValue("description", githubFormApi.getValues("description"));
    } else {
      githubFormApi.setValue("name", containerFormApi.getValues("name"));
      githubFormApi.setValue("description", containerFormApi.getValues("description"));
    }
    setKind(next);
  };

  // Branch / Ref is required; put the default back if it was cleared.
  useEffect(() => {
    if (step.id === "workflow" && !githubFormApi.getValues("branch")) {
      githubFormApi.setValue("branch", "main");
    }
  }, [step.id, githubFormApi]);

  const resetAll = () => {
    setKind("github");
    setStepIndex(0);
    githubFormApi.reset(emptyGithubValues());
    containerFormApi.reset(emptyContainerValues());
    setShowConnForm(false);
    setNewConn({ name: "", repoUrl: "", token: "" });
    setValidation({ status: "idle", message: "" });
    setLoad({ status: "ready", message: "" });
    setStoredLlmToken(false);
    setUnsavedConnection(null);
    setStoredUsername("");
    setStoredSecrets([]);
    setInspection(null);
    setImageCheck(null);
    setImageReference("");
    setReferenceError("");
    derivedRepository.current = "";
  };

  const handleClose = (next: boolean) => {
    if (!next) resetAll();
    onOpenChange(next);
  };

  const nameError = useMemo(() => {
    if (!name.trim()) return null;
    // An edit may keep its own name; a copy needs a new one.
    return isApplicationNameTaken(name, editing?.id)
      ? "An application with this name already exists."
      : null;
  }, [name, editing?.id]);

  /* ---------------- Validation gating Next ---------------- */
  // The template surfaces these as showToast(...) calls, not inline
  // banners — matched here via the app's toast system.

  const validateStep = (): boolean => {
    switch (step.id) {
      case "basic":
        if (!name.trim()) return fail("Application Name is required.");
        if (nameError) return fail(nameError);
        return true;
      case "type":
        return true;
      case "workflow":
        // The inline form closes itself once a connection is saved, so an
        // open form means the new connection isn't usable yet.
        if (showConnForm) {
          return fail(
            "Validate and save the new GitHub connection before continuing, or close the form and pick an existing one.",
          );
        }
        if (!githubForm.connectionId) return fail("GitHub Connection is required.");
        if (!githubForm.workflowRepository.trim())
          return fail("Workflow Repository is required.");
        if (!githubForm.branch.trim()) return fail("Branch / Ref is required.");
        if (!githubForm.workflowFile.trim())
          return fail("Workflow File is required.");
        return true;
      case "container":
        if (!containerForm.containerRegistry)
          return fail("Container Registry is required.");
        if (!containerForm.imageRegistry.trim())
          return fail("Image Registry is required.");
        if (!containerForm.tag.trim()) return fail("Default Image Tag is required.");
        if (!containerForm.port.trim()) return fail("Container Port is required.");
        if (!/^\d+$/.test(containerForm.port.trim()))
          return fail("Container Port must be a number.");
        {
          // No login: a public image. An edit or a copy keeps the stored
          // password for the same username.
          const username = containerForm.registryUsername.trim();
          const password = containerForm.registryPassword;
          if (password && !username) {
            return fail("Enter the Registry Username that goes with the password.");
          }
          const kept = !!source && !!username && username === storedUsername;
          if (username && !password && !kept) {
            return fail(
              "Enter the Registry Password / Token, or leave the username blank for a public image.",
            );
          }
        }
        return true;
      case "resources":
        if (containerForm.storage.trim() && !QUANTITY.test(containerForm.storage.trim())) {
          return fail("Storage must be a size such as 25Gi.");
        }
        return true;
      case "params": {
        const problem =
          kind === "github"
            ? parameterProblem(githubForm.parameters)
            : variableProblem(containerForm.parameters, secretsAvailable);
        return problem ? fail(problem) : true;
      }
      case "llm": {
        const f = kind === "container" ? containerForm : githubForm;
        const tokenKept = !!source && storedLlmToken;
        if (
          !f.llmEndpoint.trim() ||
          (!tokenKept && !f.llmApiToken.trim()) ||
          !f.llmModelName.trim()
        ) {
          return fail(
            "LLM Endpoint, API Token, and Model Name are all required.",
          );
        }
        return true;
      }
      default:
        return true;
    }
  };

  function fail(message: string): false {
    showToast(message);
    return false;
  }

  const goNext = async () => {
    if (!validateStep()) return;
    if (step.final) {
      await handleSubmit();
      return;
    }
    setStepIndex((i) => Math.min(i + 1, steps.length - 1));
  };
  const goBack = () => {
    setStepIndex((i) => Math.max(i - 1, 0));
  };

  /* ---------------- GitHub connection actions ---------------- */

  const handleValidateConnection = async () => {
    // Check locally first so an empty form gets a plain sentence instead
    // of the API's field-by-field validation response.
    const missing = [
      !newConn.name.trim() && "Connection Name",
      !newConn.repoUrl.trim() && "Repository URL",
      !newConn.token.trim() && "Personal Access Token",
    ].filter((f): f is string => !!f);
    if (missing.length > 0) {
      setValidation({
        status: "failed",
        message: `${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} required to validate the connection.`,
      });
      return;
    }
    setValidation({ status: "validating", message: "" });
    const result = await validateGithubConnection(
      newConn.name,
      newConn.repoUrl,
      newConn.token,
    );
    // POST /api/github-connections/validate — a dry run. A success only
    // unlocks "Save Connection"; nothing is persisted yet.
    setValidation({
      status: result.success ? "success" : "failed",
      message: result.message,
    });
  };

  /** POST /api/github-connections — only reachable after a successful
   *  validation; persists the connection and selects it in the dropdown. */
  const handleSaveConnection = async () => {
    if (validation.status !== "success" || savingConnection) return;
    setSavingConnection(true);
    let saved: GithubConnection;
    try {
      saved = await saveGithubConnection(newConn.name, newConn.repoUrl, newConn.token);
    } catch (err) {
      setValidation({
        status: "failed",
        message: err instanceof Error ? err.message : "Could not save the connection.",
      });
      return;
    } finally {
      setSavingConnection(false);
    }
    setConnections((prev) => [...prev, saved]);
    githubFormApi.setValue("connectionId", saved.id);
    setShowConnForm(false);
    setNewConn({ name: "", repoUrl: "", token: "" });
    setValidation({ status: "idle", message: "" });
    showToast(`Connection "${saved.name}" saved.`);
  };

  /* ---------------- Checks ---------------- */

  const workflowKey = [
    githubForm.connectionId,
    githubForm.workflowRepository.trim(),
    githubForm.workflowFile.trim(),
    githubForm.branch.trim(),
  ].join("|");
  const shownInspection = inspection?.key === workflowKey ? inspection : null;

  /** Read the workflow as set now (or reuse the last reading of it). */
  const inspectWorkflow = async (reuse: boolean): Promise<ApiWorkflowInspection | null> => {
    if (reuse && shownInspection) return shownInspection;
    const f = githubFormApi.getValues();
    if (!f.connectionId || !f.workflowRepository.trim() || !f.workflowFile.trim() || !f.branch.trim()) {
      showToast(
        "Choose the GitHub Connection and enter the Workflow Repository, Branch / Ref and Workflow File first.",
      );
      return null;
    }
    setInspecting(true);
    try {
      const result = await inspectGithubWorkflow({
        github_connection: f.connectionId,
        repository: f.workflowRepository.trim(),
        workflow_file_path: f.workflowFile.trim(),
        ref: f.branch.trim(),
      });
      setInspection({ ...result, key: workflowKey });
      // The workflow decides how it is started.
      if (result.triggers.length > 0 && !result.triggers.includes(f.triggerMethod)) {
        githubFormApi.setValue("triggerMethod", result.triggers[0]);
        showToast(`Trigger Method set to ${result.triggers[0]}, which the workflow listens for.`);
      }
      return result;
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not read the workflow.");
      return null;
    } finally {
      setInspecting(false);
    }
  };

  /** Add the workflow's inputs as parameters, but not those already here or always sent. */
  const importParameters = async () => {
    const result = await inspectWorkflow(true);
    if (!result) return;
    const unread = result.checks.find((c) => c.ok === false && READ_CHECKS.has(c.name));
    if (unread) {
      showToast(`Could not read the workflow: ${unread.detail}`);
      return;
    }
    const file = githubFormApi.getValues("workflowFile").split("/").pop();
    const here = new Set(githubFormApi.getValues("parameters").map((p) => p.name.trim()));
    const fresh = result.inputs.filter((i) => !ALWAYS_SENT.has(i.key) && !here.has(i.key));
    if (fresh.length > 0) githubParams.append(fresh.map(fromApiParameter));
    const left = result.inputs.length - fresh.length;
    showToast(
      fresh.length === 0
        ? `Nothing new to import from ${file}.`
        : `Imported ${fresh.length} parameter${fresh.length === 1 ? "" : "s"} from ${file}` +
            (left ? ` (${left} already here or sent by C2AI).` : "."),
    );
  };

  const imageKey = [
    containerForm.containerRegistry,
    containerForm.imageRegistry.trim(),
    containerForm.tag.trim(),
    containerForm.registryUsername.trim(),
    containerForm.registryPassword,
  ].join("|");
  const shownImageCheck = imageCheck?.key === imageKey ? imageCheck : null;

  const runImageCheck = async () => {
    const f = containerFormApi.getValues();
    if (!f.containerRegistry || !f.imageRegistry.trim() || !f.tag.trim()) {
      showToast("Enter the Container Registry, Image Registry and Default Image Tag first.");
      return;
    }
    const username = f.registryUsername.trim();
    setCheckingImage(true);
    try {
      const result = await checkContainerImage({
        registry: f.containerRegistry,
        image_registry: f.imageRegistry.trim(),
        tag: f.tag.trim(),
        ...(username ? { registry_username: username } : {}),
        ...(username && f.registryPassword ? { registry_password: f.registryPassword } : {}),
        // An edit or a copy may use the stored password.
        ...(sourceId ? { application_id: sourceId } : {}),
      });
      setImageCheck({ ...result, key: imageKey });
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not look the image up.");
    } finally {
      setCheckingImage(false);
    }
  };

  /** Split a pasted image reference into the registry fields. */
  const fillFromReference = () => {
    const parts = parseImageReference(imageReference);
    if (!parts) {
      setReferenceError("That is not an image reference, such as ghcr.io/owner/app:1.2.3.");
      return;
    }
    containerFormApi.setValue("containerRegistry", parts.registry);
    containerFormApi.setValue("imageRegistry", parts.imageRegistry);
    if (parts.tag) containerFormApi.setValue("tag", parts.tag);
    setReferenceError("");
    setImageReference("");
  };

  /* ---------------- Submit ---------------- */

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const registration: AppRegistration =
        kind === "github"
          ? { kind: "github", ...githubFormApi.getValues() }
          : { kind: "container", ...containerFormApi.getValues() };
      let id: string;
      let message: string;
      if (editing) {
        const { version } = await saveApplicationEdit(editing.id, registration);
        id = editing.id;
        message = `"${registration.name.trim()}" saved as version ${version}.`;
      } else if (duplicating) {
        ({ id } = await saveApplicationCopy(duplicating.id, registration));
        message = `"${registration.name.trim()}" created from "${duplicating.name}".`;
      } else {
        ({ id } = await registerApplication(registration));
        message = `"${registration.name}" registered successfully.`;
      }
      // Secret values are kept by the secret provider, once the template exists.
      if (
        registration.kind === "container" &&
        (registration.parameters.some((p) => p.secret) || storedSecrets.length > 0)
      ) {
        const failures = await syncContainerSecrets(id, registration.parameters, storedSecrets);
        if (failures.length > 0) message += ` These secrets were not stored: ${failures.join("; ")}.`;
      }
      showToast(message);
      onRegistered?.();
      handleClose(false);
    } catch (err) {
      // 409 / 422 from the API (duplicate name, contract violation, an
      // instance deployed meanwhile) — surface the backend's detail message
      // in the template's toast.
      const fallback = source ? "Could not save the application." : "Registration failed.";
      showToast(err instanceof Error ? err.message : fallback);
    } finally {
      setSubmitting(false);
    }
  };

  /** Shown in a secret field of an edit or a copy: blank keeps the stored one. */
  const keepSecret = copying
    ? `Copied from ${source?.name} — type a new one to replace it`
    : "Unchanged — type a new one to replace it";

  const selectedConnection = connections.find(
    (c) => c.id === githubForm.connectionId,
  );
  const imageHint =
    registries.find((r) => r.value === containerForm.containerRegistry)?.imageHint ??
    "company/application";

  const StepIcon = step.icon === "github" ? Github : Box;

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="top-[6vh] max-h-[88vh] w-full max-w-2xl -translate-y-0 overflow-y-auto rounded-[10px] border-[#1c2836] p-4"
        closeClassName="right-4 top-12 text-[#8b97a5] hover:text-[#eef2f6]"
        // A multi-step form shouldn't vanish on a stray click outside it —
        // only Cancel or the X close the wizard (and discard its state).
        onInteractOutside={(e) => e.preventDefault()}
        style={{ background: PANEL_BACKGROUND }}
      >
        {/* Step progress — one rounded bar segment per step; completed and
            current steps fill solid, upcoming ones stay dim. Sits below the
            close button's row so it doesn't overlap it. */}
        <div className="mb-6 mt-1 flex gap-2">
          {steps.map((s, i) => (
            <span
              key={s.id}
              className="h-[3px] flex-1 rounded-full transition-colors"
              style={{
                background:
                  i <= stepIndexClamped ? ACCENT : "rgba(255,255,255,0.12)",
              }}
            />
          ))}
        </div>

        {/* Header */}
        <div className="mb-1 flex items-start gap-3">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
            style={{ background: "rgba(32,171,199,0.12)", color: ACCENT }}
          >
            <StepIcon className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">
              Step {stepIndexClamped + 1} of {steps.length} · {step.kicker}
            </p>
            <h3 className="text-lg font-bold text-[#eef2f6]">
              {editing
                ? `Edit ${editing.name}`
                : duplicating
                  ? `Duplicate ${duplicating.name}`
                  : "Register Application"}
            </h3>
          </div>
        </div>
        <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">
          {step.subtitle}
        </p>

        {/* Body */}
        <div className="space-y-2">
          {load.status === "loading" && (
            <p className="flex items-center gap-2 py-6 text-[12.5px] text-[#8b97a5]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading the application…
            </p>
          )}
          {load.status === "error" && (
            <p role="alert" className="py-6 text-[12.5px] text-[#f0655f]">
              Could not load the application: {load.message}
            </p>
          )}
          {load.status === "ready" && editing && step.id === "basic" && (
            <p className="rounded-[8px] border border-[#1c2836] px-3 py-2 text-[12px] text-[#8b97a5]">
              Saving makes this version {editing.version + 1}. Anything deployed from now on
              uses it; past deployments keep the version they ran.
            </p>
          )}
          {load.status === "ready" && duplicating && step.id === "basic" && (
            <p className="rounded-[8px] border border-[#1c2836] px-3 py-2 text-[12px] text-[#8b97a5]">
              The copy is a new application, starting at version 1; {duplicating.name} stays as
              it is. Its passwords and tokens are copied unless you enter new ones, but secret
              environment variables need their values entered again.
            </p>
          )}
          {load.status === "ready" && step.id === "basic" && (
            <>
              <Field label="Application Name" required>
                <input
                  className={FIELD_CLASS}
                  placeholder="e.g. my-application"
                  {...(kind === "container"
                    ? containerFormApi.register("name")
                    : githubFormApi.register("name"))}
                />
                {nameError && (
                  <p className="mt-1 text-xs text-[#f0655f]">{nameError}</p>
                )}
              </Field>
              <Field label="Description" optional>
                <textarea
                  rows={3}
                  placeholder="What does this application do?"
                  className="flex w-full rounded-[6px] border border-[#16202c] bg-[rgba(3,14,25,0.87)] px-3 py-2 text-[12px] text-[#eaf0f7] placeholder:text-[#57606c] outline-none transition-colors focus:border-[#20abc7]"
                  {...(kind === "container"
                    ? containerFormApi.register("description")
                    : githubFormApi.register("description"))}
                />
              </Field>
            </>
          )}

          {load.status === "ready" && step.id === "type" && (
            <ChoiceList
              value={kind}
              onChange={handleKindChange}
              options={[
                {
                  value: "github",
                  icon: <Github className="h-5 w-5" />,
                  title: "GitHub Workflow",
                  desc: "Deploy by triggering a GitHub Actions workflow.",
                },
                {
                  value: "container",
                  icon: <Box className="h-5 w-5" />,
                  title: "Containerized Application",
                  desc: "Deploy a container image as a managed service.",
                },
              ]}
            />
          )}

          {load.status === "ready" && step.id === "workflow" && (
            <>
              <Field label="GitHub Connection" required>
                <Select {...githubFormApi.register("connectionId")}>
                  <option value="">Select a connection…</option>
                  {unsavedConnection && (
                    <option value={unsavedConnection}>
                      C2AI server's GitHub token ({unsavedConnection})
                    </option>
                  )}
                  {connections.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>

              <button
                type="button"
                onClick={() => setShowConnForm((v) => !v)}
                className="flex items-center gap-1 text-[12px] font-medium text-[#20abc7] hover:text-[#4dc6dd]"
              >
                <Plus className="h-3 w-3" /> Add new connection
              </button>

              {showConnForm && (
                <div className="space-y-3 rounded-[8px] border border-[rgba(32,171,199,0.4)] p-4">
                  <Field label="Connection Name" required>
                    <input
                      className={FIELD_CLASS}
                      placeholder="e.g. my-application-prod"
                      value={newConn.name}
                      onChange={(e) => {
                        setNewConn((c) => ({ ...c, name: e.target.value }));
                        setValidation({ status: "idle", message: "" });
                      }}
                    />
                  </Field>
                  <Field label="Repository URL" required>
                    <input
                      className={FIELD_CLASS}
                      placeholder="https://github.com/org/repo"
                      value={newConn.repoUrl}
                      onChange={(e) => {
                        setNewConn((c) => ({ ...c, repoUrl: e.target.value }));
                        setValidation({ status: "idle", message: "" });
                      }}
                    />
                  </Field>
                  <Field label="Personal Access Token" required>
                    <input
                      type="password"
                      className={FIELD_CLASS}
                      placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                      value={newConn.token}
                      onChange={(e) => {
                        setNewConn((c) => ({ ...c, token: e.target.value }));
                        setValidation({ status: "idle", message: "" });
                      }}
                    />
                  </Field>

                  {validation.status === "success" && (
                    <p className="flex items-center gap-1.5 text-[12.5px] text-[#4ade80]">
                      <Check className="h-3.5 w-3.5" /> {validation.message}
                    </p>
                  )}
                  {validation.status === "failed" && (
                    <p className="flex items-center gap-1.5 text-[12.5px] text-[#f0655f]">
                      <X className="h-3.5 w-3.5" /> {validation.message}
                    </p>
                  )}

                  <div className="flex gap-2 pt-1">
                    <button
                      type="button"
                      onClick={handleValidateConnection}
                      disabled={validation.status === "validating"}
                      className="flex cursor-pointer items-center gap-1.5 rounded-[6px] border px-3.5 py-2 text-[12.5px] font-semibold transition-colors hover:bg-[rgba(32,171,199,0.12)] hover:text-[#eef2f6] disabled:cursor-wait disabled:opacity-60"
                      style={{ borderColor: ACCENT, color: ACCENT }}
                    >
                      {validation.status === "validating" && (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      )}
                      Validate Connection
                    </button>
                    {/* Enabled only once the dry-run validation succeeded;
                        editing any field re-disables it (validation resets). */}
                    <button
                      type="button"
                      onClick={handleSaveConnection}
                      disabled={validation.status !== "success" || savingConnection}
                      title={
                        validation.status === "success"
                          ? "Save this connection"
                          : "Validate the connection first"
                      }
                      className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                      style={{ background: ACCENT }}
                    >
                      {savingConnection && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      Save Connection
                    </button>
                  </div>
                </div>
              )}
              {selectedConnection && !showConnForm && (
                <p className="text-[11.5px] text-[#57606c]">
                  {selectedConnection.repoUrl}
                </p>
              )}

              <Field
                label="Code Repository"
                optional
                helper="owner/repo — where the application source lives, if not in the workflow repository. Its branches and tags are the versions offered when deploying."
              >
                <input
                  className={FIELD_CLASS}
                  placeholder="owner/repo"
                  {...githubFormApi.register("codeRepository")}
                />
              </Field>

              <Field
                label="Workflow Repository"
                required
                helper="owner/repo — where the deploy workflow is defined"
              >
                <input
                  className={FIELD_CLASS}
                  placeholder="owner/repo"
                  {...githubFormApi.register("workflowRepository")}
                />
              </Field>

              <Field
                label="Branch / Ref"
                required
                helper="The branch or tag to run the workflow from."
              >
                <input className={FIELD_CLASS} {...githubFormApi.register("branch")} />
              </Field>

              <Field label="Workflow File" required>
                <input
                  className={FIELD_CLASS}
                  placeholder="e.g. .github/workflows/deploy.yml"
                  {...githubFormApi.register("workflowFile")}
                />
              </Field>

              <Field label="Trigger Method" required>
                <ChoiceList
                  value={githubForm.triggerMethod}
                  onChange={(v) => githubFormApi.setValue("triggerMethod", v)}
                  options={[
                    {
                      value: "workflow_dispatch",
                      title: "workflow_dispatch",
                      desc: "Trigger using workflow inputs.",
                    },
                    {
                      value: "repository_dispatch",
                      title: "repository_dispatch",
                      desc: "Trigger using event payload.",
                    },
                  ]}
                />
              </Field>

              <button
                type="button"
                onClick={() => void inspectWorkflow(false)}
                disabled={inspecting}
                className="flex items-center gap-1.5 rounded-[6px] border px-3.5 py-2 text-[12.5px] font-semibold transition-colors hover:bg-[rgba(32,171,199,0.12)] disabled:cursor-wait disabled:opacity-60"
                style={{ borderColor: ACCENT, color: ACCENT }}
              >
                {inspecting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ListChecks className="h-3.5 w-3.5" />}
                Check workflow
              </button>
              {shownInspection && <CheckList checks={shownInspection.checks} />}
            </>
          )}

          {load.status === "ready" && step.id === "container" && (
            <>
              <Field
                label="Paste an image reference"
                optional
                helper="Fills in the registry, image and tag below, e.g. ghcr.io/owner/app:1.2.3."
              >
                <div className="flex gap-2">
                  <input
                    aria-label="Image reference"
                    className={FIELD_CLASS}
                    placeholder="docker.io/company/application:1.0.0"
                    value={imageReference}
                    onChange={(e) => {
                      setImageReference(e.target.value);
                      setReferenceError("");
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        fillFromReference();
                      }
                    }}
                  />
                  <button
                    type="button"
                    onClick={fillFromReference}
                    disabled={!imageReference.trim()}
                    className="shrink-0 rounded-[6px] border px-3 text-[12.5px] font-semibold disabled:opacity-40"
                    style={{ borderColor: ACCENT, color: ACCENT }}
                  >
                    Fill in
                  </button>
                </div>
                {referenceError && <p className="text-xs text-[#f0655f]">{referenceError}</p>}
              </Field>
              <Field label="Container Registry" required>
                <Select {...containerFormApi.register("containerRegistry")}>
                  <option value="">Select a registry…</option>
                  {containerForm.containerRegistry &&
                    !registries.some((r) => r.value === containerForm.containerRegistry) && (
                      <option value={containerForm.containerRegistry}>
                        {containerForm.containerRegistry}
                      </option>
                    )}
                  {registries.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Image Registry" required helper={imageHint}>
                <input
                  className={FIELD_CLASS}
                  placeholder={imageHint}
                  {...containerFormApi.register("imageRegistry")}
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field
                  label="Registry Username"
                  optional
                  helper="Leave both blank for a public image."
                >
                  <input
                    className={FIELD_CLASS}
                    {...containerFormApi.register("registryUsername")}
                  />
                </Field>
                <Field label="Registry Password / Token" optional>
                  <input
                    type="password"
                    autoComplete="new-password"
                    className={FIELD_CLASS}
                    placeholder={
                      source && containerForm.registryUsername.trim() === storedUsername && storedUsername
                        ? keepSecret
                        : undefined
                    }
                    {...containerFormApi.register("registryPassword")}
                  />
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Default Image Tag" required>
                  <input
                    className={FIELD_CLASS}
                    placeholder="e.g. 1.0.0"
                    {...containerFormApi.register("tag")}
                  />
                </Field>
                <Field label="Container Port" required>
                  <input
                    className={FIELD_CLASS}
                    inputMode="numeric"
                    placeholder="e.g. 8080"
                    {...containerFormApi.register("port")}
                  />
                </Field>
              </div>
              <Field label="Image Pull Policy">
                <Select {...containerFormApi.register("pullPolicy")}>
                  <option value="IfNotPresent">IfNotPresent</option>
                  <option value="Always">Always</option>
                  <option value="Never">Never</option>
                </Select>
              </Field>
              <Switch
                checked={containerForm.exposePublicly}
                onChange={(v) => containerFormApi.setValue("exposePublicly", v)}
                title="Expose as a public service (Ingress)"
                helper="An ingress can be configured during deployment."
              />

              <button
                type="button"
                onClick={() => void runImageCheck()}
                disabled={checkingImage}
                className="flex items-center gap-1.5 rounded-[6px] border px-3.5 py-2 text-[12.5px] font-semibold transition-colors hover:bg-[rgba(32,171,199,0.12)] disabled:cursor-wait disabled:opacity-60"
                style={{ borderColor: ACCENT, color: ACCENT }}
              >
                {checkingImage ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ListChecks className="h-3.5 w-3.5" />}
                Check image
              </button>
              {shownImageCheck && (
                <CheckList
                  checks={[{ name: "Image", ok: shownImageCheck.ok, detail: shownImageCheck.detail }]}
                />
              )}
            </>
          )}

          {load.status === "ready" && step.id === "resources" && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label="CPU Request" optional>
                  <input className={FIELD_CLASS} {...containerFormApi.register("cpuRequest")} />
                </Field>
                <Field label="Memory Request" optional>
                  <input className={FIELD_CLASS} {...containerFormApi.register("memoryRequest")} />
                </Field>
              </div>
              <Field label="Scaling — Replica Count" optional>
                <input
                  className={FIELD_CLASS}
                  inputMode="numeric"
                  {...containerFormApi.register("replicas")}
                />
              </Field>
              <Field
                label="Storage — Persistent Volume"
                optional
                helper="A size such as 25Gi; blank for no persistent volume."
              >
                <input
                  className={FIELD_CLASS}
                  list="storage-sizes"
                  placeholder="No persistent volume"
                  autoComplete="off"
                  {...containerFormApi.register("storage")}
                />
                <datalist id="storage-sizes">
                  {STORAGE_SIZES.map((size) => (
                    <option key={size} value={size} />
                  ))}
                </datalist>
              </Field>
            </>
          )}

          {load.status === "ready" && step.id === "params" && kind === "github" && (
            <ParameterEditor
              rows={githubForm.parameters}
              ids={githubParams.fields.map((f) => f.id)}
              onAdd={() => githubParams.append(emptyParameterDef())}
              onRemove={(i) => githubParams.remove(i)}
              onChange={(i, patch) =>
                githubFormApi.setValue(`parameters.${i}`, {
                  ...githubFormApi.getValues(`parameters.${i}`),
                  ...patch,
                })
              }
              onImport={() => void importParameters()}
              importing={inspecting}
            />
          )}

          {load.status === "ready" && step.id === "params" && kind === "container" && (
            <EnvironmentEditor
              rows={containerForm.parameters}
              ids={containerParams.fields.map((f) => f.id)}
              onAdd={() => containerParams.append(emptyVariable())}
              onRemove={(i) => containerParams.remove(i)}
              onChange={(i, patch) =>
                containerFormApi.setValue(`parameters.${i}`, {
                  ...containerFormApi.getValues(`parameters.${i}`),
                  ...patch,
                })
              }
              secretsAvailable={secretsAvailable}
              copying={copying}
            />
          )}

          {load.status === "ready" && step.id === "llm" && (
            <>
              <Field label="LLM Endpoint" required>
                <input
                  className={FIELD_CLASS}
                  {...(kind === "container"
                    ? containerFormApi.register("llmEndpoint")
                    : githubFormApi.register("llmEndpoint"))}
                />
              </Field>
              <Field label="LLM API Token" required={!(source && storedLlmToken)}>
                <input
                  type="password"
                  autoComplete="new-password"
                  className={FIELD_CLASS}
                  placeholder={source && storedLlmToken ? keepSecret : "sk-••••••••••••"}
                  {...(kind === "container"
                    ? containerFormApi.register("llmApiToken")
                    : githubFormApi.register("llmApiToken"))}
                />
              </Field>
              <Field
                label="LLM Model Name"
                required
                helper="Any model the gateway routes. Suggestions are the models C2AI can price."
              >
                <input
                  className={FIELD_CLASS}
                  list="llm-model-suggestions"
                  autoComplete="off"
                  {...(kind === "container"
                    ? containerFormApi.register("llmModelName")
                    : githubFormApi.register("llmModelName"))}
                />
                <datalist id="llm-model-suggestions">
                  {llmSuggestions.map((m) => (
                    <option key={m.model_name} value={m.model_name} />
                  ))}
                </datalist>
                {/* Live pricing check — a warning, never a block: the
                    registration still succeeds with an unpriced model, its
                    usage just won't show up in cost tracking. */}
                {llmPricing.status === "checking" && (
                  <p className="flex items-center gap-1.5 text-[11.5px] text-[#57606c]">
                    <Loader2 className="h-3 w-3 animate-spin" /> Checking model pricing…
                  </p>
                )}
                {llmPricing.status === "priced" && (
                  <p className="flex items-center gap-1.5 text-[11.5px] text-[#4ade80]">
                    <Check className="h-3 w-3" /> Pricing available
                    {llmPricing.provider ? ` · ${llmPricing.provider}` : ""}
                  </p>
                )}
                {llmPricing.status === "unpriced" && (
                  <div className="flex items-start gap-2 rounded-[8px] border border-[rgba(251,191,36,0.4)] bg-[rgba(251,191,36,0.08)] p-2.5 text-[12px] text-[#fbbf24]">
                    <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
                    <span>
                      {llmPricing.message} Costs for this application won't be tracked.
                    </span>
                  </div>
                )}
                {llmPricing.status === "error" && (
                  <p className="text-[11.5px] text-[#f0655f]">{llmPricing.message}</p>
                )}
              </Field>
            </>
          )}

        </div>

        {/* Footer */}
        <div className="mt-5 flex items-center justify-between border-t border-[#1c2836] pt-4">
          <button
            type="button"
            onClick={goBack}
            disabled={stepIndexClamped === 0}
            className="flex items-center gap-1.5 rounded-[6px] border px-3.5 py-2 text-[12.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-30"
            style={{ borderColor: "rgba(32,171,199,0.4)", color: ACCENT }}
          >
            ← Back
          </button>
          <div className="flex items-center gap-2">
            {!step.final && (
              <button
                type="button"
                onClick={() => handleClose(false)}
                className="rounded-[6px] border border-[#2b3a4a] px-3.5 py-2 text-[12.5px] font-semibold text-[#8b97a5] transition-colors hover:text-[#eef2f6]"
              >
                Cancel
              </button>
            )}
            <button
              type="button"
              onClick={goNext}
              disabled={submitting || load.status !== "ready"}
              className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-[#04121a] transition-colors disabled:opacity-60"
              style={{ background: "#5eead4" }}
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {step.final ? (
                <>
                  <Check className="h-3.5 w-3.5" />{" "}
                  {editing ? "Save Changes" : duplicating ? "Create Copy" : "Create Application"}
                </>
              ) : (
                <>Next →</>
              )}
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
