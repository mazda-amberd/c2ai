import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as registeredApplicationsApi from "@api/services/registeredApplications";
import { ToastProvider } from "@components/Toast";
import * as registeredAppsApi from "@/utils/registeredAppsApi";
import type { RegisteredApp, RegisteredAppDetail } from "@/utils/registeredAppsApi";
import { emptyParameterDef } from "@/utils/registrationApi";

import DeployApplicationModal from "./DeployApplicationModal";

const app = (overrides: Partial<RegisteredApp> = {}): RegisteredApp => ({
  id: "app-1",
  name: "ADA",
  desc: "",
  type: "github",
  status: "active",
  version: 1,
  instances: 0,
  tiers: {},
  canDelete: true,
  canEdit: true,
  created: "2026-09-24",
  ...overrides,
});

const param = (name: string) => ({ ...emptyParameterDef(), name });

const githubDetail: RegisteredAppDetail = {
  id: "app-1",
  type: "github",
  workflowFile: ".github/workflows/ada-deploy.yaml",
  defaultVersion: "main",
  parameters: ["customer_name", "env_instance", "region", "slack_user"].map(param),
};

const deployed = { instance_name: "ada-tier-2" } as registeredApplicationsApi.ApiDeployment;

function renderModal() {
  return render(
    <ToastProvider>
      <DeployApplicationModal open onOpenChange={() => {}} tierIndex={1} />
    </ToastProvider>,
  );
}

function input(label: string): HTMLInputElement {
  const field = screen.getByText(label).closest("div")!;
  return field.querySelector("input")!;
}

describe("DeployApplicationModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(registeredApplicationsApi, "listDeployments").mockResolvedValue([]);
    vi.spyOn(registeredAppsApi, "fetchAppVersions").mockResolvedValue(["main"]);
  });

  it("asks for the customer and sends it with the instance name", async () => {
    vi.spyOn(registeredAppsApi, "fetchDeployableApps").mockResolvedValue([app()]);
    vi.spyOn(registeredAppsApi, "fetchRegisteredAppDetail").mockResolvedValue(githubDetail);
    const deploy = vi
      .spyOn(registeredApplicationsApi, "deployGithubApplication")
      .mockResolvedValue(deployed);

    renderModal();
    await screen.findByText("region");
    // Filled by the form's own fields or by Athena, so never shown.
    expect(screen.queryByText("customer_name")).toBeNull();
    expect(screen.queryByText("slack_user")).toBeNull();
    await waitFor(() => expect(input("Instance Name").value).toBe("ada-tier-2"));

    fireEvent.click(screen.getByRole("button", { name: /Deploy Application/ }));
    expect(await screen.findByText("Customer Name is required.")).toBeTruthy();
    expect(deploy).not.toHaveBeenCalled();

    fireEvent.change(input("Customer Name"), { target: { value: " Acme " } });
    fireEvent.change(input("region"), { target: { value: "eu" } });
    fireEvent.click(screen.getByRole("button", { name: /Deploy Application/ }));

    await waitFor(() => expect(deploy).toHaveBeenCalledOnce());
    expect(deploy).toHaveBeenCalledWith("app-1", {
      tier: 2,
      version: "main",
      instance_name: "ada-tier-2",
      parameters: { region: "eu", customer_name: "Acme", env_instance: "ada-tier-2" },
    });
  });

  it("sends the customer with a container deployment", async () => {
    vi.spyOn(registeredAppsApi, "fetchDeployableApps").mockResolvedValue([
      app({ id: "app-2", name: "chat-service", type: "container" }),
    ]);
    vi.spyOn(registeredAppsApi, "fetchRegisteredAppDetail").mockResolvedValue({
      id: "app-2",
      type: "container",
      defaultVersion: "1.2.3",
      parameters: [],
    });
    vi.spyOn(registeredAppsApi, "fetchAppVersions").mockResolvedValue(["1.2.3"]);
    const deploy = vi
      .spyOn(registeredApplicationsApi, "deployContainerApplication")
      .mockResolvedValue(deployed);

    renderModal();
    await waitFor(() => expect(input("Instance Name").value).toBe("chat-service-tier-2"));
    fireEvent.change(input("Customer Name"), { target: { value: "Acme" } });
    fireEvent.click(screen.getByRole("button", { name: /Deploy Application/ }));

    await waitFor(() => expect(deploy).toHaveBeenCalledOnce());
    expect(deploy).toHaveBeenCalledWith("app-2", 2, {
      instance_name: "chat-service-tier-2",
      customer_name: "Acme",
      version: "1.2.3",
    });
  });

  it("starts each parameter at this tier's value, and fills in from the last deploy", async () => {
    vi.spyOn(registeredAppsApi, "fetchDeployableApps").mockResolvedValue([app()]);
    vi.spyOn(registeredAppsApi, "fetchRegisteredAppDetail").mockResolvedValue({
      ...githubDetail,
      parameters: [
        {
          ...emptyParameterDef(),
          name: "size",
          type: "select",
          label: "Size",
          description: "How big an instance to start",
          options: ["small", "large"],
          value: "small",
          tierValues: { "2": "large" },
        },
        { ...emptyParameterDef(), name: "dry_run", type: "boolean", value: "false" },
        { ...emptyParameterDef(), name: "replicas", type: "number", required: false },
        param("region"),
      ],
    });
    vi.spyOn(registeredAppsApi, "fetchAppVersions").mockResolvedValue(["main", "release-2"]);
    vi.spyOn(registeredApplicationsApi, "listDeployments").mockResolvedValue([
      {
        instance_name: "amberd-acme-prod",
        tier: 1,
        configuration: {
          customer_name: "Acme",
          parameters: { size: "small", region: "eu", branch: "release-2" },
        },
      } as unknown as registeredApplicationsApi.ApiDeployment,
    ]);
    const deploy = vi
      .spyOn(registeredApplicationsApi, "deployGithubApplication")
      .mockResolvedValue(deployed);

    renderModal(); // Tier 2
    const size = (await screen.findByLabelText("Size")) as HTMLSelectElement;
    expect(size.value).toBe("large"); // Tier 2's value, not the default
    expect(screen.getByText("How big an instance to start")).toBeTruthy();
    expect((screen.getByLabelText("dry_run") as HTMLSelectElement).value).toBe("false");

    fireEvent.change(input("Customer Name"), { target: { value: "Beta" } });
    fireEvent.click(screen.getByRole("button", { name: /Deploy Application/ }));
    expect(await screen.findByText("region is required.")).toBeTruthy();
    expect(deploy).not.toHaveBeenCalled();

    expect(await screen.findByText("amberd-acme-prod")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("option", { name: "release-2" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Fill in from it/ }));
    expect(size.value).toBe("small");
    expect(input("region").value).toBe("eu");
    expect(input("Customer Name").value).toBe("Acme");
    fireEvent.click(screen.getByRole("button", { name: /Deploy Application/ }));

    await waitFor(() => expect(deploy).toHaveBeenCalledOnce());
    // replicas is optional and blank: left out.
    expect(deploy).toHaveBeenCalledWith("app-1", {
      tier: 2,
      version: "release-2",
      instance_name: "ada-tier-2",
      parameters: {
        size: "small",
        dry_run: false,
        region: "eu",
        customer_name: "Acme",
        env_instance: "ada-tier-2",
      },
    });
  });
});
