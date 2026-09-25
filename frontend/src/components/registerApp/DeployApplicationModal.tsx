import { useEffect, useMemo, useState } from "react";
import { History, Loader2, Rocket } from "lucide-react";
import { useForm } from "react-hook-form";

import { Dialog, DialogContent } from "@ui/dialog";
import { useToast } from "@components/Toast";
import {
  deployContainerApplication,
  deployGithubApplication,
  listDeployments,
  type ApiDeployment,
} from "@api/services/registeredApplications";
import {
  fetchAppVersions,
  fetchDeployableApps,
  fetchRegisteredAppDetail,
  TYPE_LABEL,
  type RegisteredApp,
  type RegisteredAppDetail,
} from "@/utils/registeredAppsApi";
import {
  C2AI_FILLED,
  choices,
  prefilledFor,
  type ParameterDef,
  type ParamType,
} from "@/utils/registrationApi";
import {
  ACCENT,
  ContextNote,
  Field,
  FIELD_CLASS,
  PANEL_BACKGROUND,
  Select,
} from "./wizardStyles";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 0-based tier index, matching the rest of the app's convention. */
  tierIndex: number;
  onDeployed?: () => void;
};

type DeployFormValues = {
  appId: string;
  version: string;
  customerName: string;
  instanceName: string;
  /** Dynamically generated from the registered app's parameter defs
   *  (Story 5.1) — one entry per parameter, value assigned at deploy time. */
  parameters: ParameterDef[];
};

const slugifyTier = (tierName: string) =>
  tierName.toLowerCase().replace(/\s+/g, "-");
/** DNS-label safe instance name seed: `^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`. */
const slugify = (value: string) =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40) || "app";

/** Parameters this form never renders: Customer Name, Instance Name and
 *  Version fill three, and C2AI derives the rest (it rejects supplied values). */
const FORM_MANAGED_PARAMETERS = new Set(Object.keys(C2AI_FILLED));

const EMPTY_FORM: DeployFormValues = {
  appId: "",
  version: "",
  customerName: "",
  instanceName: "",
  parameters: [],
};

const PARAM_TYPE_LABEL: Record<ParamType, string> = {
  text: "Text",
  number: "Number",
  boolean: "Boolean",
  select: "Choice",
  "key-value": "Key-Value",
};

const labelOf = (p: ParameterDef) => p.label || p.name;
const isBlank = (p: ParameterDef) =>
  p.type === "key-value" ? !p.kvKey.trim() : p.value.trim() === "";

/** Why these values cannot be deployed, or null. */
function parameterProblem(params: ParameterDef[]): string | null {
  for (const p of params) {
    if (p.required && isBlank(p)) return `${labelOf(p)} is required.`;
    if (p.type === "number" && !isBlank(p) && !Number.isFinite(Number(p.value))) {
      return `${labelOf(p)} must be a number.`;
    }
  }
  return null;
}

/** Map the form's typed parameter values onto the API's
 *  `parameters: { key: value }` body. A blank optional parameter is left
 *  out, so the template's default (or nothing) applies. */
function toParameterValues(params: ParameterDef[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of params) {
    if (!p.name || isBlank(p)) continue;
    switch (p.type) {
      case "boolean":
        out[p.name] = p.value === "true";
        break;
      case "number":
        out[p.name] = Number(p.value);
        break;
      case "key-value":
        out[p.name] = { [p.kvKey]: p.kvValue };
        break;
      default:
        out[p.name] = p.value;
    }
  }
  return out;
}

/** A parameter holding a value from an earlier deployment. */
function withValue(p: ParameterDef, value: unknown): ParameterDef {
  if (value === null || value === undefined) return p;
  if (p.type === "key-value" && typeof value === "object") {
    const [pair] = Object.entries(value as Record<string, string>);
    return pair ? { ...p, kvKey: pair[0], kvValue: String(pair[1]) } : p;
  }
  return { ...p, value: String(value) };
}

/** The version an earlier deployment ran. */
function deployedVersion(deployment: ApiDeployment): string | null {
  const configuration = deployment.configuration as {
    parameters?: Record<string, unknown>;
    container?: { image_tag?: string };
  };
  const version = configuration.container?.image_tag ?? configuration.parameters?.branch;
  return typeof version === "string" ? version : null;
}

export default function DeployApplicationModal({
  open,
  onOpenChange,
  tierIndex,
  onDeployed,
}: Props) {
  const { showToast } = useToast();
  const tierName = `Tier ${tierIndex + 1}`;
  const tierNumber = tierIndex + 1;

  const [apps, setApps] = useState<RegisteredApp[]>([]);
  const [detail, setDetail] = useState<RegisteredAppDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [versions, setVersions] = useState<string[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // The template's most recent deployment, to fill the form in from.
  const [lastDeploy, setLastDeploy] = useState<ApiDeployment | null>(null);

  const { register, watch, setValue, getValues, reset } =
    useForm<DeployFormValues>({ defaultValues: EMPTY_FORM });

  const appId = watch("appId");
  const version = watch("version");
  const customerName = watch("customerName");
  const instanceName = watch("instanceName");
  const parameters = watch("parameters");

  useEffect(() => {
    if (!open) return;
    fetchDeployableApps()
      .then((list) => {
        setApps(list);
        if (list.length > 0 && !getValues("appId")) {
          setValue("appId", list[0].id);
        }
      })
      .catch((err: unknown) => {
        showToast(
          err instanceof Error
            ? err.message
            : "Could not load registered applications.",
        );
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const app = useMemo(() => apps.find((a) => a.id === appId), [apps, appId]);

  // Whenever the selected application changes, load its detail
  // (GET /registered-applications/{id}) — the catalog list has no
  // parameters. If the detail says application_type is github_workflow,
  // its parameter definitions generate the Parameters fields below
  // (Story 5.1); containers have none. Versions then come from the
  // github-tags / image-tags endpoints.
  useEffect(() => {
    if (!app) return;
    let cancelled = false;
    setDetail(null);
    setLastDeploy(null);
    setVersions([]);
    setValue("version", "");
    setValue("parameters", []);
    setValue("instanceName", `${slugify(app.name)}-${slugifyTier(tierName)}`);
    setDetailLoading(true);

    fetchRegisteredAppDetail(app.id)
      .then(async (d) => {
        if (cancelled) return;
        setDetail(d);
        // Each field starts at the template's value for this tier.
        setValue(
          "parameters",
          d.type === "github"
            ? d.parameters
                .filter((p) => !FORM_MANAGED_PARAMETERS.has(p.name))
                .map((p) => prefilledFor(p, tierNumber))
            : [],
        );
        listDeployments({ application_id: d.id, limit: 1 })
          .then(([latest]) => {
            if (!cancelled) setLastDeploy(latest ?? null);
          })
          .catch(() => {
            /* nothing to fill in from */
          });
        setVersions(d.defaultVersion ? [d.defaultVersion] : []);
        setValue("version", d.defaultVersion);

        setVersionsLoading(true);
        try {
          const list = await fetchAppVersions(d);
          if (cancelled) return;
          setVersions(list);
          setValue("version", list[0] ?? d.defaultVersion);
        } catch {
          /* keep the registered version as the only option */
        } finally {
          if (!cancelled) setVersionsLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        showToast(
          err instanceof Error
            ? err.message
            : "Could not load the application.",
        );
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [app?.id]);

  /** The last deployment's customer, version and parameter values. */
  const fillFromLastDeploy = () => {
    if (!lastDeploy) return;
    const configuration = lastDeploy.configuration as {
      customer_name?: string;
      parameters?: Record<string, unknown>;
    };
    const values = configuration.parameters ?? {};
    setValue(
      "parameters",
      getValues("parameters").map((p) => (p.name in values ? withValue(p, values[p.name]) : p)),
    );
    const customer = configuration.customer_name ?? values.customer_name;
    if (typeof customer === "string") setValue("customerName", customer);
    const lastVersion = deployedVersion(lastDeploy);
    if (lastVersion && versions.includes(lastVersion)) setValue("version", lastVersion);
    showToast(`Filled in from ${lastDeploy.instance_name}.`);
  };

  const resetAll = () => {
    setSubmitting(false);
    setVersions([]);
    setDetail(null);
    setLastDeploy(null);
    setDetailLoading(false);
    reset(EMPTY_FORM);
  };

  const handleClose = (next: boolean) => {
    if (!next) resetAll();
    onOpenChange(next);
  };

  const handleDeploy = async () => {
    if (!app) {
      showToast("Select an application to deploy.");
      return;
    }
    if (!version.trim()) {
      showToast("Select a version to deploy.");
      return;
    }
    if (!detail) {
      showToast("Application details are still loading.");
      return;
    }
    const customer = customerName.trim();
    if (!customer) {
      showToast("Customer Name is required.");
      return;
    }
    const name = instanceName.trim();
    if (!name) {
      showToast("Instance Name is required.");
      return;
    }
    // Same rule the API enforces (DNS label): lowercase letters, digits and
    // hyphens, no leading/trailing hyphen, at most 63 characters.
    if (name.length > 63 || !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(name)) {
      showToast(
        "Instance Name must use lowercase letters, digits and hyphens only (no leading or trailing hyphen), up to 63 characters.",
      );
      return;
    }

    const problem = detail.type === "github" ? parameterProblem(getValues("parameters")) : null;
    if (problem) {
      showToast(problem);
      return;
    }

    setSubmitting(true);
    try {
      let created: ApiDeployment;
      if (detail.type === "github") {
        // instance_name is always sent so the deployment is recorded under
        // the name the user chose (the API otherwise derives one from the
        // workflow parameters).
        created = await deployGithubApplication(app.id, {
          tier: tierNumber,
          version: version.trim(),
          instance_name: name,
          // Workflows that declare these receive them; for others Athena
          // records the customer without sending either.
          parameters: {
            ...toParameterValues(getValues("parameters")),
            customer_name: customer,
            env_instance: name,
          },
        });
      } else {
        created = await deployContainerApplication(app.id, tierNumber, {
          instance_name: name,
          customer_name: customer,
          version: version.trim(),
        });
      }
      // 202 Accepted — the pipeline continues in GitHub Actions. The tier
      // page's active-operations poll (/api/pipeline/active) shows progress
      // on the card, so the modal simply closes.
      showToast(`Deployment "${created.instance_name || name}" started in ${tierName}.`);
      onDeployed?.();
      handleClose(false);
    } catch (err) {
      // 409 DuplicateDeploymentInstance / 422 contract violation / 503 —
      // the API's `detail` is already the user-facing message.
      showToast(
        err instanceof Error ? err.message : "Deployment request failed.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  // Decided by the detail response's application_type, not the list row.
  const isGithub = detail?.type === "github";

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="top-[10vh] max-h-[85vh] w-full max-w-lg -translate-y-0 overflow-y-auto rounded-[10px] border-[#1c2836] p-4"
        closeClassName="right-4 top-4 text-[#8b97a5] hover:text-[#eef2f6]"
        style={{ background: PANEL_BACKGROUND }}
      >
        {/* Header */}
        <div className="mb-1 flex items-start gap-3">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
            style={{ background: "rgba(32,171,199,0.12)", color: ACCENT }}
          >
            <Rocket className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">
              Deployment Configuration
            </p>
            <h3 className="truncate text-lg font-bold text-[#eef2f6]">
              {app ? `Deploy: ${app.name}` : "Deploy Application"}
            </h3>
          </div>
        </div>
        <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">
          Choose the registered application and version to deploy.
        </p>

        <ContextNote>
          Deploying into <strong>{tierName}</strong> — the target tier and
          namespace are set automatically from this context.
        </ContextNote>

        <div className="space-y-4">
          <Field
            label="Application"
            required
            helper="Registered application template"
          >
            <Select {...register("appId")}>
              {apps.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {TYPE_LABEL[a.type]}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Version"
            required
            helper={
              versionsLoading ? "Loading versions…" : "Version / tag to deploy."
            }
          >
            <Select {...register("version")}>
              {versions.map((v, i) => (
                <option key={v} value={v}>
                  {v}
                  {i === 0 ? "  ·  current" : ""}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Customer Name" required helper="Who this instance is deployed for.">
            <input className={FIELD_CLASS} {...register("customerName")} />
          </Field>

          <Field
            label="Instance Name"
            required
            helper="Generated from the registered template — override if needed. Lowercase letters, digits and hyphens."
          >
            <input className={FIELD_CLASS} {...register("instanceName")} />
          </Field>

          {lastDeploy && !detailLoading && (
            <div className="flex items-center justify-between gap-3 rounded-[8px] border border-[#1c2836] px-3 py-2">
              <span className="min-w-0 truncate text-[11.5px] text-[#8b97a5]">
                Last deployed as <strong className="text-[#eef2f6]">{lastDeploy.instance_name}</strong>{" "}
                on Tier {lastDeploy.tier}
              </span>
              <button
                type="button"
                onClick={fillFromLastDeploy}
                className="flex shrink-0 items-center gap-1 text-[12px] font-semibold text-[#20abc7] hover:text-[#4dc6dd]"
              >
                <History className="h-3.5 w-3.5" /> Fill in from it
              </button>
            </div>
          )}

          {detailLoading && (
            <p className="flex items-center gap-1.5 text-[11.5px] text-[#57606c]">
              <Loader2 className="h-3 w-3 animate-spin" /> Loading application
              details…
            </p>
          )}

          {/* GitHub Workflow only — generated from the registered
                parameter definitions returned by the detail endpoint. */}
          {isGithub && (
            <div>
              <h4 className="mb-2 text-[15px] font-bold text-[#eef2f6]">
                Parameters
              </h4>
              <div className="space-y-3 rounded-[8px] border border-[#1c2836] p-3">
                {parameters.length === 0 && (
                  <p className="text-[12px] text-[#57606c]">
                    This workflow has no registered parameters.
                  </p>
                )}
                {parameters.map((param, i) => (
                  <Field
                    key={param.name || i}
                    label={labelOf(param)}
                    required={param.required}
                    helper={param.description || undefined}
                  >
                    {param.type === "text" && (
                      <input
                        aria-label={labelOf(param)}
                        className={FIELD_CLASS}
                        {...register(`parameters.${i}.value`)}
                      />
                    )}
                    {param.type === "number" && (
                      <input
                        aria-label={labelOf(param)}
                        type="number"
                        className={FIELD_CLASS}
                        {...register(`parameters.${i}.value`)}
                      />
                    )}
                    {param.type === "boolean" && (
                      <Select aria-label={labelOf(param)} {...register(`parameters.${i}.value`)}>
                        {!param.required && <option value="">—</option>}
                        <option value="true">True</option>
                        <option value="false">False</option>
                      </Select>
                    )}
                    {param.type === "select" && (
                      <Select aria-label={labelOf(param)} {...register(`parameters.${i}.value`)}>
                        <option value="">{param.required ? "Choose…" : "—"}</option>
                        {choices(param.options).map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </Select>
                    )}
                    {param.type === "key-value" && (
                      <div className="grid grid-cols-2 gap-2">
                        <input
                          className={FIELD_CLASS}
                          placeholder="Key"
                          {...register(`parameters.${i}.kvKey`)}
                        />
                        <input
                          className={FIELD_CLASS}
                          placeholder="Value"
                          {...register(`parameters.${i}.kvValue`)}
                        />
                      </div>
                    )}
                    <p className="text-[11px] text-[#57606c]">
                      {PARAM_TYPE_LABEL[param.type]}
                      {param.label ? ` · ${param.name}` : ""}
                    </p>
                  </Field>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="mt-5 flex items-center justify-end gap-2 border-t border-[#1c2836] pt-4">
          <button
            type="button"
            onClick={() => handleClose(false)}
            className="rounded-[6px] border border-[#2b3a4a] px-3.5 py-2 text-[12.5px] font-semibold text-[#8b97a5] transition-colors hover:text-[#eef2f6]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleDeploy}
            disabled={submitting || !detail || detailLoading}
            className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-[#04121a] transition-colors disabled:opacity-60"
            style={{ background: "#5eead4" }}
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Rocket className="h-3.5 w-3.5" />
            )}
            Deploy Application
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
