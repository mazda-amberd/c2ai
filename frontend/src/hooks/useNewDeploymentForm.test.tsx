import { renderHook, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as deploymentsApi from "@api/services/deployments";
import * as registeredApplicationsApi from "@api/services/registeredApplications";
import * as registeredAppsApi from "@/utils/registeredAppsApi";
import { basePipelineRun } from "@/test-utils/pipeline";

import type { UseNewDeploymentFormProps } from "./newDeployment/types";
import { useNewDeploymentForm } from "./useNewDeploymentForm";
import { createQueryWrapper, createTestQueryClient } from "./queryTestUtils";

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  tierIndex: 0,
  onDeployed: vi.fn(),
  mode: "new" as const,
};

describe("useNewDeploymentForm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(deploymentsApi, "getGithubBranches").mockResolvedValue(["main", "dev"]);
    vi.spyOn(deploymentsApi, "getGithubTags").mockResolvedValue([]);
  });

  function renderForm(props: Partial<UseNewDeploymentFormProps> = {}) {
    const onOpenChange = vi.fn();
    const onDeployed = vi.fn();
    const queryClient = createTestQueryClient();
    const merged = { ...defaultProps, onOpenChange, onDeployed, ...props };
    const hook = renderHook(() => useNewDeploymentForm(merged), {
      wrapper: createQueryWrapper(queryClient),
    });
    return { ...hook, onOpenChange, onDeployed };
  }

  it("propagates close intent via handleOpenChange when not submitting", () => {
    const { result, onOpenChange } = renderForm();

    act(() => {
      result.current.handleOpenChange(false);
    });

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("exposes isUpdate=false in new mode", () => {
    const { result } = renderForm({ mode: "new" });
    expect(result.current.isUpdate).toBe(false);
  });

  it("exposes isUpdate=true in update mode", () => {
    const { result } = renderForm({
      mode: "update",
      initialValues: { subdomain: "amberd-acme-ada", customer_name: "acme", env_instance: "ada" },
    });
    expect(result.current.isUpdate).toBe(true);
  });

  it("computes deployUrlPreview from customer_name and env_instance in new mode", async () => {
    const { result } = renderForm({ mode: "new" });

    act(() => {
      result.current.setValue("customer_name", "acme");
      result.current.setValue("env_instance", "ada");
    });

    await waitFor(() => {
      expect(result.current.deployUrlPreview).toContain("acme");
    });
  });

  it("does not show deployUrlPreview in update mode", () => {
    const { result } = renderForm({
      mode: "update",
      initialValues: { subdomain: "amberd-acme-ada", customer_name: "acme", env_instance: "ada" },
    });
    expect(result.current.deployUrlPreview).toBeNull();
  });

  it("calls triggerDeployment and onDeployed on successful new-deploy submit", async () => {
    vi.spyOn(deploymentsApi, "triggerDeployment").mockResolvedValueOnce({
      ...basePipelineRun,
    });
    const { result, onDeployed, onOpenChange } = renderForm({ mode: "new" });

    act(() => {
      result.current.setValue("branch", "main");
      result.current.setValue("customer_name", "acme");
      result.current.setValue("env_instance", "ada");
    });

    await act(async () => {
      await result.current.handleSubmit({ preventDefault: vi.fn() } as never);
    });

    expect(deploymentsApi.triggerDeployment).toHaveBeenCalled();
    expect(onDeployed).toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("calls triggerUpdateDeployment on successful update submit", async () => {
    vi.spyOn(deploymentsApi, "triggerUpdateDeployment").mockResolvedValueOnce({
      ...basePipelineRun,
      operation: "update",
    });
    const { result, onDeployed } = renderForm({
      mode: "update",
      initialValues: { subdomain: "amberd-acme-ada", customer_name: "acme", env_instance: "ada" },
    });

    act(() => {
      result.current.setValue("branch", "main");
      result.current.setValue("env_instance", "ada");
    });

    await act(async () => {
      await result.current.handleSubmit({ preventDefault: vi.fn() } as never);
    });

    expect(deploymentsApi.triggerUpdateDeployment).toHaveBeenCalled();
    expect(onDeployed).toHaveBeenCalled();
  });

  it("loads and upgrades the registered deployment that matches the instance", async () => {
    const registeredDeployment = {
      id: "deployment-123",
      application_id: "application-456",
      application_name: "Containerized-test",
      application_type: "containerized" as const,
      application_version: 1,
      instance_name: "containerized-test-tier-1",
      tier: 1,
      status: "running",
      configuration: {},
      triggered_by: "admin",
      dispatch_reference: null,
      current_step: "completed",
      failure_reason: null,
      completed_at: null,
      subdomain: "containerized-test-tier-1",
    };
    vi.spyOn(
      registeredApplicationsApi,
      "findRegisteredDeploymentForInstance",
    ).mockResolvedValue(registeredDeployment);
    vi.spyOn(registeredAppsApi, "fetchRegisteredAppDetail").mockResolvedValue({
      id: "application-456",
      type: "container",
      defaultVersion: "latest",
      parameters: [],
    });
    vi.spyOn(registeredAppsApi, "fetchAppVersions").mockResolvedValue([
      "latest",
      "2.0.0",
    ]);
    const upgradeSpy = vi
      .spyOn(registeredApplicationsApi, "upgradeDeployment")
      .mockResolvedValue({ ...registeredDeployment, status: "updating" });
    const legacyUpdateSpy = vi.spyOn(
      deploymentsApi,
      "triggerUpdateDeployment",
    );

    const { result, onDeployed } = renderForm({
      mode: "update",
      targetInstance: { nodename: "containerized-test-tier-1" },
      initialValues: {
        subdomain: "containerized-test-tier-1",
        customer_name: "containerized-test",
        env_instance: "",
      },
    });

    await waitFor(() => expect(result.current.tags).toEqual(["latest", "2.0.0"]));
    expect(
      registeredApplicationsApi.findRegisteredDeploymentForInstance,
    ).toHaveBeenCalledWith(1, "containerized-test-tier-1");
    expect(deploymentsApi.getGithubBranches).not.toHaveBeenCalled();
    expect(deploymentsApi.getGithubTags).not.toHaveBeenCalled();

    act(() => {
      result.current.setValue("branch", "2.0.0");
    });
    await act(async () => {
      await result.current.handleSubmit({ preventDefault: vi.fn() } as never);
    });

    expect(upgradeSpy).toHaveBeenCalledWith("deployment-123", "2.0.0");
    expect(legacyUpdateSpy).not.toHaveBeenCalled();
    expect(onDeployed).toHaveBeenCalled();
  });

  it("sets submitError when deployment API throws", async () => {
    vi.spyOn(deploymentsApi, "triggerDeployment").mockRejectedValueOnce(
      new Error("Deployment failed"),
    );
    const { result } = renderForm({ mode: "new" });

    act(() => {
      result.current.setValue("branch", "main");
      result.current.setValue("customer_name", "acme");
      result.current.setValue("env_instance", "ada");
    });

    await act(async () => {
      await result.current.handleSubmit({ preventDefault: vi.fn() } as never);
    });

    await waitFor(() => {
      expect(result.current.submitError).toBe("Deployment failed");
    });
  });
});
