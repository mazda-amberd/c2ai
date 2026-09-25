/* Registered applications catalog — backed by /api/registered-applications.
 * The API's snake_case records are normalized here into the shape the
 * catalog page and Deploy Application modal render. */

import {
  deleteRegisteredApplication,
  getRegisteredApplication,
  listRegisteredApplications,
  listGithubTags,
  listImageTags,
  type ApiCatalogItem,
  type ApiRegisteredApplicationDetail,
} from "@api/services/registeredApplications";
import { fromApiParameter, type ParameterDef } from "./registrationApi";

export type RegisteredAppType = "github" | "container";
export type RegisteredAppStatus = "active" | "draft" | "deprecated";

export type RegisteredApp = {
  id: string;
  name: string;
  desc: string;
  type: RegisteredAppType;
  status: RegisteredAppStatus;
  /** Registered template version number (bumps on re-registration). */
  version: number;
  /** Total deployment instances across every tier. */
  instances: number;
  /** Instance count keyed by tier name, e.g. { "Tier 1": 2 }. */
  tiers: Record<string, number>;
  /** Server-side delete gate (no instances, no managed secrets). */
  canDelete: boolean;
  /** Server-side edit gate: nothing of it live on a tier, and not ADA. */
  canEdit: boolean;
  /** yyyy-mm-dd */
  created: string;
};

/** Detail-only data the deploy form needs, fetched per selected app. */
export type RegisteredAppDetail = {
  id: string;
  type: RegisteredAppType;
  /** GitHub Workflow apps only — the file the deploy trigger runs. */
  workflowFile?: string;
  /** Registered ref (GitHub) or image tag (container) — the default version. */
  defaultVersion: string;
  /** GitHub Workflow apps only — registered deployment parameters, with
   *  their defaults. The Deploy Application form is generated from these
   *  (Story 5.1). */
  parameters: ParameterDef[];
};

export function normalizeCatalogItem(item: ApiCatalogItem): RegisteredApp {
  const tiers: Record<string, number> = {};
  for (const { tier, instances } of item.tiers_deployed_to ?? []) {
    if (instances > 0) tiers[/^\d+$/.test(tier) ? `Tier ${tier}` : tier] = instances;
  }
  return {
    id: String(item.id),
    name: item.name,
    desc: item.description ?? "",
    type: item.application_type === "containerized" ? "container" : "github",
    status: item.status,
    version: item.current_version,
    instances: item.total_deployed_instances,
    tiers,
    canDelete: item.can_delete,
    canEdit: item.can_edit,
    created: (item.created_at ?? "").slice(0, 10),
  };
}

export function normalizeDetail(detail: ApiRegisteredApplicationDetail): RegisteredAppDetail {
  if (detail.application_type === "github_workflow") {
    return {
      id: String(detail.id),
      type: "github",
      workflowFile: detail.github.workflow_file_path,
      // The code branch deploys unless another version is picked.
      defaultVersion: detail.github.code_ref ?? detail.github.ref,
      parameters: detail.parameters.map<ParameterDef>(fromApiParameter),
    };
  }
  return {
    id: String(detail.id),
    type: "container",
    defaultVersion: detail.container.tag ?? "",
    parameters: [],
  };
}

/** Last catalog fetched — lets the registration wizard check name
 *  uniqueness synchronously while typing. */
let lastLoadedApps: RegisteredApp[] = [];

export async function fetchRegisteredApps(): Promise<RegisteredApp[]> {
  const list = await listRegisteredApplications({ limit: 200 });
  lastLoadedApps = list.map(normalizeCatalogItem);
  return lastLoadedApps;
}

/** Only active templates are offered in the Deploy Application picker. */
export async function fetchDeployableApps(): Promise<RegisteredApp[]> {
  const list = await listRegisteredApplications({ status: "active", limit: 200 });
  const apps = list.map(normalizeCatalogItem);
  if (lastLoadedApps.length === 0) lastLoadedApps = apps;
  return apps;
}

/** GET /{id} — the discriminated detail, with the GitHub parameter
 *  definitions the deploy form is generated from. */
export async function fetchRegisteredAppDetail(appId: string): Promise<RegisteredAppDetail> {
  return normalizeDetail(await getRegisteredApplication(appId));
}

/** DELETE /api/registered-applications/{id} — 204 on success, 409 while it
 *  still has running instances or managed secrets. */
export async function deleteRegisteredApp(app: RegisteredApp): Promise<void> {
  await deleteRegisteredApplication(app.id);
}

/** Version options for the Deploy picker — GitHub branches/tags or
 *  registry image tags, registered/default version first. */
export async function fetchAppVersions(detail: RegisteredAppDetail): Promise<string[]> {
  let names: string[];
  let preferred: string | null = detail.defaultVersion || null;

  if (detail.type === "github") {
    const res = await listGithubTags(detail.id);
    names = res.items?.length ? res.items : [...res.branches, ...res.tags];
  } else {
    const res = await listImageTags(detail.id);
    names = res.items.map((t) => t.tag);
    preferred = res.default_tag ?? res.items.find((t) => t.is_default)?.tag ?? preferred;
  }

  const unique = Array.from(new Set(names.filter(Boolean)));
  if (preferred) {
    const at = unique.indexOf(preferred);
    if (at !== -1) unique.splice(at, 1);
    unique.unshift(preferred);
  }
  return unique;
}

/** Unique within the C2AI instance (case-insensitive), per Story 3.1/3.2.
 *  Checks against the most recently loaded catalog; the backend is the
 *  final authority and returns 409/422 on a real collision. An edit passes
 *  its own id, so keeping (or recasing) its name is not a collision. */
export function isApplicationNameTaken(name: string, exceptId?: string): boolean {
  const normalized = name.trim().toLowerCase();
  return lastLoadedApps.some(
    (a) => a.id !== exceptId && a.name.trim().toLowerCase() === normalized,
  );
}

export const TYPE_LABEL: Record<RegisteredAppType, string> = {
  github: "GitHub Workflow",
  container: "Containerized",
};

export const STATUS_LABEL: Record<RegisteredAppStatus, string> = {
  active: "Active",
  draft: "Draft",
  deprecated: "Deprecated",
};
