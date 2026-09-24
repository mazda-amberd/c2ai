import type { Application } from "@/types/application";

const GRAFANA_K8S_BASE = "https://grafana-k8s.amberd.ai";

/** Kubernetes app deployments overview (replaces legacy Qumulus / application dashboards). */
const GRAFANA_K8S_DEPLOYMENTS_DASHBOARD_PATH =
  "/d/k8s-app-deployments-overview-fixed/745048c";

/** Athena default dashboard (per-customer Tier Metrics when `VITE_TIER_METRICS_TARGET=athena`). */
const ATHENA_DEFAULT_DASHBOARD_PATH =
  "/d/athenaDefaultDashboard/athena-default-dashboard";

const athenaDefaultDashboardParams =
  "orgId=1&from=now-6h&to=now&timezone=browser&refresh=30s";

const dashboardCommonEntries: Array<[string, string]> = [
  ["orgId", "1"],
  ["from", "now-6h"],
  ["to", "now"],
  ["timezone", "browser"],
  ["var-datasource", "prometheus"],
];

function deploymentsDashboardUrl(
  variableEntries: Array<[string, string]>,
): string {
  const params = new URLSearchParams([
    ...dashboardCommonEntries,
    ...variableEntries,
    ["refresh", "30s"],
  ]);

  return `${GRAFANA_K8S_BASE}${GRAFANA_K8S_DEPLOYMENTS_DASHBOARD_PATH}?${params.toString()}`;
}

const ATHENA_TIER_METRICS_PATH = "/d/athenaRayDefaultDashboard/athena-tier-metrics";

const athenaRayTierParams =
  "orgId=1&from=now-30m&to=now&timezone=browser&var-datasource=prometheus&var-SessionName=$__all&var-Instance=$__all&var-RayNodeType=$__all";

const TIER_CLUSTER_IDS: Record<number, string> = {
  1: "qwen-5254d",
  2: "qwen-pq9sc",
  3: "qwen-l8dnl",
};

export function buildAthenaRayTierMetricsUrl(urlTier: number): string {
  const clusterId = TIER_CLUSTER_IDS[urlTier];
  if (!clusterId) return "#";
  return `${GRAFANA_K8S_BASE}${ATHENA_TIER_METRICS_PATH}?${athenaRayTierParams}&var-Tier=${encodeURIComponent(clusterId)}`;
}

/** `urlTier` is 1–4 (matches route `/apps/:tierIndex`). */
export function buildAthenaDefaultDashboardTierUrl(urlTier: number): string {
  const tierParam = `tier${urlTier}`;
  return `${GRAFANA_K8S_BASE}${ATHENA_DEFAULT_DASHBOARD_PATH}?${athenaDefaultDashboardParams}&var-Tier=${encodeURIComponent(tierParam)}`;
}

export function buildTierMetricsUrl(tierIndex: number): string {
  const tierParam = `tier${tierIndex + 1}`;
  return deploymentsDashboardUrl([
    ["var-tier", tierParam],
    ["var-namespace", "$__all"],
    ["var-app", "$__all"],
    ["var-deployment", "$__all"],
  ]);
}

export function buildAppMetricsUrl(appName: string): string {
  return deploymentsDashboardUrl([
    ["var-tier", "$__all"],
    ["var-namespace", "$__all"],
    ["var-app", appName],
    ["var-deployment", appName],
  ]);
}

/** Grafana deployments dashboard scoped to namespace (K8s / workflow subdomain), tier, and app name. */
export function buildDeploymentMetricsUrl(tierIndex: number, app: Application): string {
  const tierParam = `tier${tierIndex + 1}`;
  return deploymentsDashboardUrl([
    ["var-tier", tierParam],
    ["var-namespace", app.nodename],
    ["var-app", app.name],
    ["var-deployment", app.name],
  ]);
}
