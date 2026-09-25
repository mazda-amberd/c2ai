import { beforeEach, describe, expect, it, vi } from "vitest";

import * as metricsV2Api from "@api/services/metricsV2";
import type { MetricsResponse } from "@/types/metricsV2";

import { fetchClusterMetrics } from "./metricsApi";

const response = (points?: { timestamp: number; value: number }[]): MetricsResponse =>
  ({
    level: "cluster",
    window: { from: "", to: "", preset: "1h", step_seconds: 60 },
    generated_at: "",
    refresh_after_seconds: 60,
    series: [
      {
        scope: {
          kind: "cluster",
          id: "cluster",
          name: "Cluster",
          tier: null,
          subdomain: null,
          client_name: null,
          instance_name: null,
        },
        metrics: {
          nodes_ready: { value: 3, unit: "count", status: null, available: true, points },
        },
      },
    ],
    degraded: false,
    errors: [],
  }) as MetricsResponse;

describe("metrics points", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("charts the samples the API returns for the window", async () => {
    const served = [
      { timestamp: 1_000, value: 2 },
      { timestamp: 61_000, value: 3 },
    ];
    vi.spyOn(metricsV2Api, "getMetricsV2").mockResolvedValue(response(served));
    const { metrics } = await fetchClusterMetrics("1h");
    expect(metrics[0].points).toEqual(served);
  });

  it("falls back to the values it has polled when the API sends none", async () => {
    vi.spyOn(metricsV2Api, "getMetricsV2").mockResolvedValue(response());
    const first = await fetchClusterMetrics("5m");
    const second = await fetchClusterMetrics("5m");
    expect(first.metrics[0].points).toHaveLength(1);
    expect(second.metrics[0].points.map((p) => p.value)).toEqual([3, 3]);
  });
});
