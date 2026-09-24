/**
 * Host label rule: 3-63 lowercase alphanumeric chars or hyphens, no leading/trailing hyphen.
 * Mirrors the backend's SUBDOMAIN_RE.
 */
export const WORKFLOW_HOST_LABEL_RE = /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/;

/** Returns true when `label` is a valid workflow host label. */
export function isValidWorkflowHostLabel(label: string): boolean {
  return WORKFLOW_HOST_LABEL_RE.test(label);
}

function slugSegment(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Strip the amberd- prefix from a subdomain string (legacy / simple cases).
 * Prefer `customerNameFromWorkflowSubdomain` or `splitWorkflowHostLabel` when parsing.
 */
export function customerNameFromAmberdSubdomain(subdomain: string): string {
  return subdomain.replace(/^amberd-/, "");
}

/**
 * Host label matching the devops workflow ``prepare`` step:
 * ``amberd-{customer_slug}-{env_slug}`` (same slug rules per segment).
 */
export function workflowPrepareSubdomain(
  customerName: string,
  envInstance: string,
): string {
  return `amberd-${slugSegment(customerName)}-${slugSegment(envInstance)}`;
}

/**
 * Best-effort split of ``amberd-{customer}-{env}`` into slug segments.
 * If there is no trailing ``-{env}`` pattern, returns ``{ customer: body, env: "" }``.
 */
export function splitWorkflowHostLabel(full: string): { customer: string; env: string } {
  const t = full.trim();
  const prefix = "amberd-";
  if (!t.toLowerCase().startsWith(prefix)) {
    return { customer: t, env: "" };
  }
  const body = t.slice(prefix.length);
  const m = body.match(/^(.*)-([a-z0-9][a-z0-9-]*)$/i);
  if (!m) {
    return { customer: body, env: "" };
  }
  return { customer: m[1], env: m[2].toLowerCase() };
}

/**
 * Best-effort reverse of `workflowPrepareSubdomain` for redeploy prefill when
 * ``envInstance`` is known (suffix strip). Falls back to `splitWorkflowHostLabel`
 * behaviour via `customerNameFromAmberdSubdomain` when the suffix does not match.
 */
export function customerNameFromWorkflowSubdomain(
  workflowSubdomain: string,
  envInstance: string,
): string {
  const lower = workflowSubdomain.toLowerCase();
  const prefix = "amberd-";
  if (!lower.startsWith(prefix)) {
    return workflowSubdomain;
  }
  const rest = workflowSubdomain.slice(prefix.length);
  const suffix = `-${envInstance.trim().toLowerCase()}`;
  if (lower.endsWith(suffix)) {
    return rest.slice(0, rest.length - suffix.length);
  }
  return splitWorkflowHostLabel(workflowSubdomain).customer;
}

/** Full URL shown in UI; live DNS follows the workflow (often GitHub vars). */
export function deploymentPreviewUrl(subdomain: string, domain: string): string {
  const d = domain.trim().replace(/^\.+/, "");
  return `https://${subdomain}.${d}/`;
}
