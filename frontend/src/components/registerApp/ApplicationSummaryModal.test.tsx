import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as registeredApplicationsApi from "@api/services/registeredApplications";
import * as registrationApi from "@/utils/registrationApi";
import type { RegisteredApp } from "@/utils/registeredAppsApi";

import ApplicationSummaryModal from "./ApplicationSummaryModal";

const catalogRow = (overrides: Partial<RegisteredApp> = {}): RegisteredApp => ({
  id: "ada00000-0000-4000-8000-000000000001",
  name: "ADA",
  desc: "",
  type: "github",
  status: "active",
  version: 1,
  instances: 0,
  tiers: {},
  canDelete: true,
  created: "2026-09-24",
  ...overrides,
});

const adaDetail: registeredApplicationsApi.ApiGithubApplicationDetail = {
  id: "ada00000-0000-4000-8000-000000000001",
  name: "ADA",
  description: "Amberd ADA, deployed with the devops ada-* workflows.",
  status: "active",
  version: 1,
  created_by: "system",
  created_at: "2026-09-24T23:43:55.460141Z",
  application_type: "github_workflow",
  github: {
    github_connection: "athena-environment",
    trigger_method: "workflow_dispatch",
    repository: "amberd-ai/devops",
    code_repository: "amberd-ai/dealership_new",
    workflow_file_path: ".github/workflows/ada-deploy.yaml",
    ref: "main",
  },
  parameters: [
    { key: "customer_name", type: "text" },
    { key: "env_instance", type: "text" },
  ],
  llm: null,
};

describe("ApplicationSummaryModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing while no application is selected", () => {
    const detail = vi.spyOn(registeredApplicationsApi, "getRegisteredApplication");
    render(<ApplicationSummaryModal app={null} onOpenChange={() => {}} />);
    expect(screen.queryByText("Registered Application")).toBeNull();
    expect(detail).not.toHaveBeenCalled();
  });

  it("summarizes a GitHub workflow app, falling back to a legacy connection id", async () => {
    vi.spyOn(registeredApplicationsApi, "getRegisteredApplication").mockResolvedValue(adaDetail);
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([]);

    render(<ApplicationSummaryModal app={catalogRow()} onOpenChange={() => {}} />);

    expect(screen.getByRole("dialog", { name: "ADA" })).toBeTruthy();
    expect(await screen.findByText("Workflow Configuration")).toBeTruthy();
    expect(screen.getByText("athena-environment")).toBeTruthy();
    expect(screen.getByText(".github/workflows/ada-deploy.yaml")).toBeTruthy();
    expect(screen.getByText("customer_name")).toBeTruthy();
    expect(screen.getByText("2026-09-24")).toBeTruthy();
    expect(screen.queryByText("••••••••")).toBeNull(); // no LLM token registered
  });

  it("shows the managed connection's name", async () => {
    const managed = { ...adaDetail, github: { ...adaDetail.github, github_connection: "c-1" } };
    vi.spyOn(registeredApplicationsApi, "getRegisteredApplication").mockResolvedValue(managed);
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([
      { id: "c-1", name: "Amberd devops", repoUrl: "https://github.com/amberd-ai/devops" },
    ]);

    render(<ApplicationSummaryModal app={catalogRow()} onOpenChange={() => {}} />);

    expect(await screen.findByText("Amberd devops")).toBeTruthy();
  });

  it("shows the error when the application cannot be loaded", async () => {
    vi.spyOn(registeredApplicationsApi, "getRegisteredApplication").mockRejectedValue(
      new Error("Registered application not found."),
    );
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([]);

    render(<ApplicationSummaryModal app={catalogRow()} onOpenChange={() => {}} />);

    expect(await screen.findByText("Registered application not found.")).toBeTruthy();
  });
});
