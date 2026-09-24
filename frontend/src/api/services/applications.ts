import { apiFetch } from "..";

import type { Application, TiersResponse } from "@/types/application";

export const getMetrics = async (): Promise<TiersResponse> => {
  return await apiFetch<TiersResponse>("/api/metrics");
};

export const getMetricsByTier = async (
  tierIndex: number
): Promise<Application[]> => {
  const tierName = `Tier ${tierIndex + 1}`;
  const response = await getMetrics();
  return response.tiers[tierName] ?? [];
};
