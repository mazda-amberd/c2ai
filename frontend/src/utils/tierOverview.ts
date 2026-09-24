import { buildAthenaDefaultDashboardTierUrl } from "./grafana";

/** Per-tier Amberd control-plane UI (not Grafana). `urlTier` matches route `/apps/:tierIndex` (1–4). */
export function buildTierOverviewUrl(urlTier: number): string {
  return `https://tier${urlTier}.amberd.ai/#/overview`;
}

/**
 * URL opened by the Tier Metrics button. Set `VITE_TIER_METRICS_TARGET=athena` at build time to use
 * the Athena Grafana default dashboard instead of the Amberd tier overview.
 */
export function buildTierMetricsEntryUrl(urlTier: number): string {
  const raw = (import.meta.env.VITE_TIER_METRICS_TARGET as string | undefined)?.trim().toLowerCase();
  if (raw === "athena" || raw === "athena-grafana" || raw === "grafana-athena") {
    return buildAthenaDefaultDashboardTierUrl(urlTier);
  }
  return buildTierOverviewUrl(urlTier);
}
