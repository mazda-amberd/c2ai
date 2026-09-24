import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as deploymentsApi from "@api/services/deployments";
import * as registeredApplicationsApi from "@api/services/registeredApplications";
import { basePipelineStatus } from "@/test-utils/pipeline";
import type { Application } from "@/types/application";

import { createQueryWrapper, createTestQueryClient } from "./queryTestUtils";
import {
  deploymentsQueryKey,
  useDeployBranchOptions,
  useDeployments,
  useInvalidateDeploymentsForTierIndex,
  useTierDeploymentActions,
} from "./useDeployments";

const sampleApp = (overrides: Partial<Application> = {}): Application => ({
  id: 10,
  name: "ada",
  nodename: "amberd-acme-ada",
  client_name: "acme",
  instance_name: "ada",
  version: null,
  cpu: 0,
  memory: 0,
  gpu: 0,
  status: "Healthy",
  ...overrides,
});

describe("useDeployments hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(deploymentsApi, "getGithubTags").mockResolvedValue([]);
  });

  describe("useDeployments", () => {
    it("returns only deploy rows for the requested tier", async () => {
      const rows = [
        { ...basePipelineStatus, id: "d2", tier: 2, operation: "deploy" as const },
        { ...basePipelineStatus, id: "d1", tier: 1, operation: "deploy" as const },
        { ...basePipelineStatus, id: "u2", tier: 2, operation: "update" as const },
      ];
      vi.spyOn(deploymentsApi, "getActivePipelines").mockResolvedValue(rows);

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useDeployments(2), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.data).toHaveLength(1));
      expect(result.current.data[0].id).toBe("d2");
    });
  });

  describe("useDeployBranchOptions", () => {
    it("does not fetch branches while disabled", () => {
      const spy = vi.spyOn(deploymentsApi, "getGithubBranches").mockResolvedValue(["main"]);
      const queryClient = createTestQueryClient();

      renderHook(() => useDeployBranchOptions(false), {
        wrapper: createQueryWrapper(queryClient),
      });

      expect(spy).not.toHaveBeenCalled();
    });

    it("loads branches when enabled", async () => {
      vi.spyOn(deploymentsApi, "getGithubBranches").mockResolvedValue(["main", "dev"]);
      const queryClient = createTestQueryClient();

      const { result } = renderHook(() => useDeployBranchOptions(true), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.branches).toEqual(["main", "dev"]));
      expect(result.current.branchesLoading).toBe(false);
    });

    it("maps non-Error failures to a generic message", async () => {
      vi.spyOn(deploymentsApi, "getGithubBranches").mockRejectedValue("boom");
      const queryClient = createTestQueryClient();

      const { result } = renderHook(() => useDeployBranchOptions(true), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.branchesError).toBe("Failed to load branches"));
    });
  });

  describe("useInvalidateDeploymentsForTierIndex", () => {
    it("invalidates the legacy per-tier deployments query key", () => {
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result } = renderHook(() => useInvalidateDeploymentsForTierIndex(0), {
        wrapper: createQueryWrapper(queryClient),
      });

      act(() => {
        result.current();
      });

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: deploymentsQueryKey(1),
      });
    });
  });

  describe("useTierDeploymentActions", () => {
    it("exposes pending new-deploy rows for the tier", async () => {
      const rows = [
        { ...basePipelineStatus, id: "p2", tier: 2, operation: "deploy" as const },
        { ...basePipelineStatus, id: "p1", tier: 1, operation: "deploy" as const },
      ];
      vi.spyOn(deploymentsApi, "getActivePipelines").mockResolvedValue(rows);

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierDeploymentActions(1, []), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.pendingDeployments).toHaveLength(1));
      expect(result.current.pendingDeployments[0].id).toBe("p2");
    });

    it("treats an in-flight update run as updating for that app", async () => {
      const app = sampleApp({ nodename: "ns-update" });
      const rows = [
        {
          ...basePipelineStatus,
          id: "upd",
          tier: 2,
          operation: "update" as const,
          subdomain: "ns-update",
          gh_conclusion: null,
          ended_at: null,
        },
      ];
      vi.spyOn(deploymentsApi, "getActivePipelines").mockResolvedValue(rows);

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierDeploymentActions(1, [app]), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.isAppUpdating(app)).toBe(true));
    });

    it("treats an in-flight migration run as migrating for that app", async () => {
      const app = sampleApp({ nodename: "ns-migrate" });
      const rows = [
        {
          ...basePipelineStatus,
          id: "mig",
          tier: 2,
          operation: "migration" as const,
          subdomain: "ns-migrate",
          gh_conclusion: null,
          ended_at: null,
        },
      ];
      vi.spyOn(deploymentsApi, "getActivePipelines").mockResolvedValue(rows);

      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierDeploymentActions(1, [app]), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.isAppMigrating(app)).toBe(true));
    });

    it("triggerMoveTier calls move-tier API and marks the app as migrating", async () => {
      vi.spyOn(deploymentsApi, "getActivePipelines").mockResolvedValue([]);
      const moveSpy = vi
        .spyOn(deploymentsApi, "triggerMoveTierDeployment")
        .mockResolvedValue({
          ...basePipelineStatus,
          operation: "migration",
          tier: 3,
        });

      const app = sampleApp({ nodename: "amberd-move-me" });
      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierDeploymentActions(1, [app]), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.pendingDeployments).toEqual([]));

      await act(async () => {
        await result.current.triggerMoveTier(app, 3);
      });

      expect(moveSpy).toHaveBeenCalledWith({
        subdomain: "amberd-move-me",
        tier: 3,
      });
      expect(result.current.isAppMigrating(app)).toBe(true);
    });

    it("confirmTerminate calls terminateDeployment for a legacy ada target", async () => {
      vi.spyOn(deploymentsApi, "getActivePipelines").mockResolvedValue([]);
      vi.spyOn(
        registeredApplicationsApi,
        "findRegisteredDeploymentForInstance",
      ).mockResolvedValue(null);
      const terminateSpy = vi
        .spyOn(deploymentsApi, "terminateDeployment")
        .mockResolvedValue({
          ...basePipelineStatus,
          operation: "terminate",
        });

      const app = sampleApp({ nodename: "amberd-z" });
      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierDeploymentActions(0, [app]), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.pendingDeployments).toEqual([]));

      act(() => {
        result.current.openTerminate(app);
      });

      await act(async () => {
        await result.current.confirmTerminate();
      });

      expect(terminateSpy).toHaveBeenCalledWith("amberd-z");
    });

    it("confirmTerminate uses the registered pipeline for a registered deployment", async () => {
      vi.spyOn(deploymentsApi, "getActivePipelines").mockResolvedValue([]);
      const registeredDeployment = {
        id: "deployment-123",
        application_id: "application-456",
        application_name: "Containerized-test",
        application_type: "containerized" as const,
        application_version: 1,
        instance_name: "containerized-test-deployment",
        tier: 1,
        status: "running",
        configuration: {},
        triggered_by: "admin",
        dispatch_reference: null,
        current_step: "completed",
        failure_reason: null,
        completed_at: null,
        subdomain: "containerized-test-deployment",
      };
      vi.spyOn(
        registeredApplicationsApi,
        "findRegisteredDeploymentForInstance",
      ).mockResolvedValue(registeredDeployment);
      const registeredTerminateSpy = vi
        .spyOn(registeredApplicationsApi, "terminateRegisteredDeployment")
        .mockResolvedValue({ ...registeredDeployment, status: "terminating" });
      const legacyTerminateSpy = vi.spyOn(deploymentsApi, "terminateDeployment");

      const app = sampleApp({ nodename: "containerized-test-deployment" });
      const queryClient = createTestQueryClient();
      const { result } = renderHook(() => useTierDeploymentActions(0, [app]), {
        wrapper: createQueryWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.pendingDeployments).toEqual([]));

      act(() => {
        result.current.openTerminate(app);
      });

      await act(async () => {
        await result.current.confirmTerminate();
      });

      expect(
        registeredApplicationsApi.findRegisteredDeploymentForInstance,
      ).toHaveBeenCalledWith(1, "containerized-test-deployment");
      // The confirmation the destructive endpoint requires is the instance name.
      expect(registeredTerminateSpy).toHaveBeenCalledWith(
        "deployment-123",
        "containerized-test-deployment",
      );
      expect(legacyTerminateSpy).not.toHaveBeenCalled();
    });
  });
});
