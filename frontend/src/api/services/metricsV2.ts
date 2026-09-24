import { apiFetch } from "..";
import {
  buildMetricsQuery,
  type MetricsQuery,
  type MetricsResponse,
  type RangePreset,
} from "@/types/metricsV2";

export const getMetricsV2 = async (
  query: MetricsQuery,
): Promise<MetricsResponse> => {
  return await apiFetch<MetricsResponse>(buildMetricsQuery(query));
};

/**
 * Single-application metrics — scoped server-side, unlike the plain
 * `level=application` query which returns every workload on the cluster.
 */
export const getApplicationMetricsV2 = async (
  application: string,
  range?: RangePreset,
): Promise<MetricsResponse> => {
  const params = new URLSearchParams({ application });
  if (range) params.set("range", range);
  return await apiFetch<MetricsResponse>(
    `/api/v2/metrics/application?${params.toString()}`,
  );
};
