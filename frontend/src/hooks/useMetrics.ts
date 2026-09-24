import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { getMetrics } from "@api/services/applications";
import type { Application, TiersResponse } from "@/types/application";
import type { TierSummary } from "@/types/metrics";

export type { TierSummary };

// Base hook for fetching all metrics
export function useMetrics() {
  return useQuery<TiersResponse>({
    queryKey: ["metrics"],
    queryFn: getMetrics,
    // Default query client pauses refetchInterval when the tab is unfocused; Grafana
    // keeps updating in another tab, which made metrics look "stuck" in Athena.
    refetchIntervalInBackground: true,
  });
}

// Helper to get tier-specific data
export function useTierData(tierIndex: number) {
  const { data, isLoading, isFetching } = useMetrics();
  const tierName = `Tier ${tierIndex + 1}`;
  const apps: Application[] = data?.tiers?.[tierName] ?? [];
  const tierGpuTotal = data?.tier_gpu_totals?.[tierName] ?? null;

  return {
    apps,
    // Only show loading if no cached data exists
    isLoading: isLoading && apps.length === 0,
    // True during background refresh (data still available)
    isFetching,
    tierGpuTotal,
  };
}

// Helper for tier summary (used by Cube component)
export function useTierSummary() {
  const { data, isLoading } = useMetrics();

  const tierSummary = useMemo((): TierSummary[] => {
    const tiers = data?.tiers || {};

    return ["Tier 1", "Tier 2", "Tier 3", "Tier 4"].map((tierName) => {
      const servers: Application[] = tiers[tierName] ?? [];
      const status = servers.reduce(
        (acc, server) => {
          if (server.status === "Healthy") acc.healthy++;
          else if (server.status === "Warning") acc.warning++;
          else if (server.status === "Critical") acc.critical++;
          return acc;
        },
        { healthy: 0, warning: 0, critical: 0 }
      );

      return {
        name: tierName,
        apps: servers.length,
        status,
        gpu: data?.tier_gpu_totals?.[tierName] ?? null,
      };
    });
  }, [data]);

  return {
    tierSummary,
    isLoading,
  };
}
