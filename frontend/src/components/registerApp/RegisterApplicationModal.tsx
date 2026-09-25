import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Box,
  Check,
  Github,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
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
import { isApplicationNameTaken } from "@/utils/registeredAppsApi";
import {
  emptyParameterDef,
  fetchContainerRegistries,
  fetchGithubConnections,
  registerApplication,
  saveGithubConnection,
  validateGithubConnection,
  type AppRegistration,
  type ContainerRegistryOption,
  type GithubConnection,
  type KeyValue,
  type ParameterDef,
  type ParamType,
  type PullPolicy,
  type TriggerMethod,
} from "@/utils/registrationApi";

type Kind = "github" | "container";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRegistered?: () => void;
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

/* ---------------- Editable parameters table (Container) ---------------- */

function ParamsTable({
  title,
  addLabel,
  rows,
  onAdd,
  onRemove,
  onChangeKey,
  onChangeValue,
  info,
}: {
  title: string;
  addLabel: string;
  rows: KeyValue[];
  onAdd: () => void;
  onRemove: (index: number) => void;
  onChangeKey: (index: number, value: string) => void;
  onChangeValue: (index: number, value: string) => void;
  info: string;
}) {
  // Key/Value cells are always-editable inputs styled to look like plain
  // text (no visible border/box) until focused — clicking anywhere in a
  // cell edits it directly. The pencil icon is just a shortcut that focuses
  // the row's Key input.
  const keyRefs = useRef<Array<HTMLInputElement | null>>([]);

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[15px] font-bold text-[#eef2f6]">{title}</h4>
        <button
          type="button"
          onClick={onAdd}
          className="flex items-center gap-1 text-[12.5px] font-semibold text-[#20abc7] hover:text-[#4dc6dd]"
        >
          <Plus className="h-3.5 w-3.5" />
          {addLabel}
        </button>
      </div>
      <div className="overflow-hidden rounded-[8px] border border-[#1c2836]">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th className="border-b border-[#1c2836] px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-[#8b97a5]">
                Key
              </th>
              <th className="border-b border-[#1c2836] px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-[#8b97a5]">
                Value
              </th>
              <th className="w-16 border-b border-[#1c2836]" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-[#1c2836] last:border-b-0">
                <td className="p-1.5">
                  <input
                    ref={(el) => {
                      keyRefs.current[i] = el;
                    }}
                    value={row.key}
                    onChange={(e) => onChangeKey(i, e.target.value)}
                    className="h-8 w-full rounded border border-transparent bg-transparent px-2 text-[#eaf0f7] outline-none focus:border-[#20abc7]"
                  />
                </td>
                <td className="p-1.5">
                  <input
                    value={row.value}
                    onChange={(e) => onChangeValue(i, e.target.value)}
                    className="h-8 w-full rounded border border-transparent bg-transparent px-2 text-[#eaf0f7] outline-none focus:border-[#20abc7]"
                  />
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex items-center justify-end gap-2.5">
                    <button
                      type="button"
                      onClick={() => keyRefs.current[i]?.focus()}
                      className="text-[#8b97a5] hover:text-[#20abc7]"
                      aria-label="Edit"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onRemove(i)}
                      className="text-[#8b97a5] hover:text-[#f0655f]"
                      aria-label="Remove"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={3}
                  className="px-4 py-5 text-center text-[12px] text-[#57606c]"
                >
                  No environment variables yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-[rgba(59,130,246,0.35)] bg-[rgba(59,130,246,0.08)] p-3 text-[12.5px] text-[#93c5fd]">
        <span className="mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#3b82f6] text-[10px] font-bold text-white">
          i
        </span>
        <span>{info}</span>
      </div>
    </div>
  );
}

/* ---------------- Typed parameters (GitHub Workflow only) ----------------
 * Each parameter has a Type — Text, Number, Boolean, or Key-Value — chosen
 * from a dropdown. No Value column here: the value is only assigned per
 * deployment on the dynamically generated deploy form (Story 5.1). */

const PARAM_TYPE_LABEL: Record<ParamType, string> = {
  text: "Text",
  number: "Number",
  boolean: "Boolean",
  "key-value": "Key-Value",
};

function TypedParamsTable({
  rows,
  onAdd,
  onRemove,
  onChangeName,
  onChangeType,
  info,
}: {
  rows: ParameterDef[];
  onAdd: () => void;
  onRemove: (index: number) => void;
  onChangeName: (index: number, value: string) => void;
  onChangeType: (index: number, value: ParamType) => void;
  info: string;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[15px] font-bold text-[#eef2f6]">Parameters</h4>
        <button
          type="button"
          onClick={onAdd}
          className="flex items-center gap-1 text-[12.5px] font-semibold text-[#20abc7] hover:text-[#4dc6dd]"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Parameter
        </button>
      </div>
      <div className="overflow-hidden rounded-[8px] border border-[#1c2836]">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th className="border-b border-[#1c2836] px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-[#8b97a5]">
                Parameter Name
              </th>
              <th className="w-[180px] border-b border-[#1c2836] px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-[#8b97a5]">
                Type
              </th>
              <th className="w-10 border-b border-[#1c2836]" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-[#1c2836] last:border-b-0">
                <td className="p-1.5">
                  <input
                    value={row.name}
                    onChange={(e) => onChangeName(i, e.target.value)}
                    placeholder="param_name"
                    className="h-8 w-full rounded border border-transparent bg-transparent px-2 text-[#eaf0f7] outline-none focus:border-[#20abc7]"
                  />
                </td>
                <td className="p-1.5">
                  <Select
                    className="h-8 text-[12px]"
                    value={row.type}
                    onChange={(e) =>
                      onChangeType(i, e.target.value as ParamType)
                    }
                  >
                    {(Object.keys(PARAM_TYPE_LABEL) as ParamType[]).map((t) => (
                      <option key={t} value={t}>
                        {PARAM_TYPE_LABEL[t]}
                      </option>
                    ))}
                  </Select>
                </td>
                <td className="px-2 py-2 text-center">
                  <button
                    type="button"
                    onClick={() => onRemove(i)}
                    className="text-[#8b97a5] hover:text-[#f0655f]"
                    aria-label="Remove"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={3}
                  className="px-4 py-5 text-center text-[12px] text-[#57606c]"
                >
                  No parameters yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-[rgba(59,130,246,0.35)] bg-[rgba(59,130,246,0.08)] p-3 text-[12.5px] text-[#93c5fd]">
        <span className="mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#3b82f6] text-[10px] font-bold text-white">
          i
        </span>
        <span>{info}</span>
      </div>
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

export default function RegisterApplicationModal({
  open,
  onOpenChange,
  onRegistered,
}: Props) {
  const [kind, setKind] = useState<Kind>("github");
  const [stepIndex, setStepIndex] = useState(0);
  const [submitting, setSubmitting] = useState(false);

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

  const steps = kind === "container" ? CONTAINER_STEPS : GITHUB_STEPS;
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
  }, [open]);

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
  };

  const handleClose = (next: boolean) => {
    if (!next) resetAll();
    onOpenChange(next);
  };

  const nameError = useMemo(() => {
    if (!name.trim()) return null;
    return isApplicationNameTaken(name)
      ? "An application with this name already exists."
      : null;
  }, [name]);

  /* ---------------- Validation gating Next ---------------- */
  // The template surfaces these as showToast(...) calls, not inline
  // banners — matched here via the app's toast system.
  const { showToast } = useToast();

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
        if (!githubForm.codeRepository.trim()) return fail("Code Repository is required.");
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
        if (
          !containerForm.registryUsername.trim() ||
          !containerForm.registryPassword.trim()
        ) {
          return fail("Registry Username and Password / Token are required.");
        }
        if (!containerForm.tag.trim()) return fail("Default Image Tag is required.");
        if (!containerForm.port.trim()) return fail("Container Port is required.");
        if (!/^\d+$/.test(containerForm.port.trim()))
          return fail("Container Port must be a number.");
        return true;
      case "resources":
        return true;
      case "params":
        return true;
      case "llm": {
        const f = kind === "container" ? containerForm : githubForm;
        if (
          !f.llmEndpoint.trim() ||
          !f.llmApiToken.trim() ||
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

  /* ---------------- Submit ---------------- */

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const registration: AppRegistration =
        kind === "github"
          ? { kind: "github", ...githubFormApi.getValues() }
          : { kind: "container", ...containerFormApi.getValues() };
      await registerApplication(registration);
      showToast(`"${registration.name}" registered successfully.`);
      onRegistered?.();
      handleClose(false);
    } catch (err) {
      // 409 / 422 from the API (duplicate name, contract violation) — surface
      // the backend's detail message in the template's toast.
      showToast(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const selectedConnection = connections.find(
    (c) => c.id === githubForm.connectionId,
  );
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
              Register Application
            </h3>
          </div>
        </div>
        <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">
          {step.subtitle}
        </p>

        {/* Body */}
        <div className="space-y-2">
          {step.id === "basic" && (
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

          {step.id === "type" && (
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

          {step.id === "workflow" && (
            <>
              <Field label="GitHub Connection" required>
                <Select {...githubFormApi.register("connectionId")}>
                  <option value="">Select a connection…</option>
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
                required
                helper="owner/repo — where the application source lives"
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
            </>
          )}

          {step.id === "container" && (
            <>
              <Field label="Container Registry" required>
                <Select {...containerFormApi.register("containerRegistry")}>
                  <option value="">Select a registry…</option>
                  {registries.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Image Registry"
                required
                helper="company/application"
              >
                <input
                  className={FIELD_CLASS}
                  placeholder="company/application"
                  {...containerFormApi.register("imageRegistry")}
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Registry Username" required>
                  <input
                    className={FIELD_CLASS}
                    {...containerFormApi.register("registryUsername")}
                  />
                </Field>
                <Field label="Registry Password / Token" required>
                  <input
                    type="password"
                    className={FIELD_CLASS}
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
            </>
          )}

          {step.id === "resources" && (
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
              <Field label="Storage — Persistent Volume" optional>
                <Select {...containerFormApi.register("storage")}>
                  <option value="">No persistent volume</option>
                  <option value="10Gi">10Gi</option>
                  <option value="20Gi">20Gi</option>
                  <option value="50Gi">50Gi</option>
                  <option value="100Gi">100Gi</option>
                </Select>
              </Field>
            </>
          )}

          {step.id === "params" && kind === "github" && (
            <TypedParamsTable
              rows={githubForm.parameters}
              onAdd={() => githubParams.append(emptyParameterDef())}
              onRemove={(i) => githubParams.remove(i)}
              onChangeName={(i, value) =>
                githubFormApi.setValue(`parameters.${i}.name`, value)
              }
              onChangeType={(i, value) =>
                githubFormApi.setValue(`parameters.${i}.type`, value)
              }
              info="These key/value pairs will be sent to the workflow as inputs, and pre-filled on the deployment form.

"
            />
          )}

          {step.id === "params" && kind === "container" && (
            <ParamsTable
              title="Environment Variables"
              addLabel="Add Variable"
              rows={containerForm.parameters}
              onAdd={() => containerParams.append({ key: "", value: "" })}
              onRemove={(i) => containerParams.remove(i)}
              onChangeKey={(i, value) =>
                containerFormApi.setValue(`parameters.${i}.key`, value)
              }
              onChangeValue={(i, value) =>
                containerFormApi.setValue(`parameters.${i}.value`, value)
              }
              info="Environment variables set on the container for every deployment of this application. Resources, scaling, and storage are configured on the Resources & Scaling step."
            />
          )}

          {step.id === "llm" && (
            <>
              <Field label="LLM Endpoint" required>
                <input
                  className={FIELD_CLASS}
                  {...(kind === "container"
                    ? containerFormApi.register("llmEndpoint")
                    : githubFormApi.register("llmEndpoint"))}
                />
              </Field>
              <Field label="LLM API Token" required>
                <input
                  type="password"
                  className={FIELD_CLASS}
                  placeholder="sk-••••••••••••"
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
              disabled={submitting}
              className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-[#04121a] transition-colors disabled:opacity-60"
              style={{ background: "#5eead4" }}
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {step.final ? (
                <>
                  <Check className="h-3.5 w-3.5" /> Create Application
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
