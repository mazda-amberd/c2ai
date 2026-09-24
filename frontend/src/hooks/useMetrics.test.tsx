import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as applicationsApi from "@api/services/applications";
import type { Application } from "@/types/application";

import { createQueryWrapper, createTestQueryClient } from "./queryTestUtils";
import { useTierData, useTierSummary } from "./useMetrics";

const sampleApp = (id: number): Application => ({
  id,
  name: `app-${id}`,
  nodename: `ns-${id}`,
  client_name: null,
  instance_name: null,
  version: null,
  cpu: 0,
  memory: 0,
  gpu: 0,
  status: "Healthy",
});

describe("useMetrics hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  describe("useTierData", () => {
    it("returns apps for the tier name derived from index", async () => {
      const tier1Apps = [sampleApp(1), sampleApp(2)];
      vi.spyOn(applicationsApi, "getMetrics").mockResolvedValue({
        tiers: {
          "Tier 1": tier1Apps,
          "Tier 2": [],
        },
      });

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierData(0), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.apps).toEqual(tier1Apps));
      expect(result.current.isLoading).toBe(false);
    });

    it("treats missing tier buckets as empty", async () => {
      vi.spyOn(applicationsApi, "getMetrics").mockResolvedValue({ tiers: {} });

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierData(3), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.apps).toEqual([]));
    });

    it("returns tierGpuTotal from tier_gpu_totals when present", async () => {
      vi.spyOn(applicationsApi, "getMetrics").mockResolvedValue({
        tiers: { "Tier 1": [sampleApp(1)] },
        tier_gpu_totals: { "Tier 1": 8.9, "Tier 2": 0.0 },
      });

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierData(0), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.tierGpuTotal).toBeCloseTo(8.9));
    });

    it("returns null tierGpuTotal when tier_gpu_totals is absent", async () => {
      vi.spyOn(applicationsApi, "getMetrics").mockResolvedValue({
        tiers: { "Tier 1": [] },
      });

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierData(0), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.tierGpuTotal).toBeNull());
    });

    it("returns null tierGpuTotal for tier with null entry (e.g. Tier 4)", async () => {
      vi.spyOn(applicationsApi, "getMetrics").mockResolvedValue({
        tiers: { "Tier 4": null },
        tier_gpu_totals: { "Tier 4": null },
      });

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierData(3), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.tierGpuTotal).toBeNull());
    });
  });

  describe("useTierSummary", () => {
    it("returns four tier rows with aggregated status counts", async () => {
      vi.spyOn(applicationsApi, "getMetrics").mockResolvedValue({
        tiers: {
          "Tier 1": [
            { ...sampleApp(1), status: "Healthy" },
            { ...sampleApp(2), status: "Warning" },
          ],
          "Tier 2": [{ ...sampleApp(3), status: "Critical" }],
        },
      });

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierSummary(), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() =>
        expect(result.current.tierSummary.find((t) => t.name === "Tier 1")?.apps).toBe(2),
      );

      const tier1 = result.current.tierSummary.find((t) => t.name === "Tier 1");
      expect(tier1?.apps).toBe(2);
      expect(tier1?.status).toEqual({ healthy: 1, warning: 1, critical: 0 });

      const tier2 = result.current.tierSummary.find((t) => t.name === "Tier 2");
      expect(tier2?.apps).toBe(1);
      expect(tier2?.status).toEqual({ healthy: 0, warning: 0, critical: 1 });

      const tier4 = result.current.tierSummary.find((t) => t.name === "Tier 4");
      expect(tier4?.apps).toBe(0);
      expect(tier4?.status).toEqual({ healthy: 0, warning: 0, critical: 0 });
    });
  });
});
