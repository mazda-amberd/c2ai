import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/auth/AuthContext";
import type { Application } from "@/types/application";
import type { PipelineStatusRecord } from "@/types/deployment";
import * as financeApi from "@/utils/financeApi";
import { signInAs } from "@/test-utils/auth";
import { basePipelineStatus } from "@/test-utils/pipeline";

import ApplicationList from "./ApplicationList";

const deploying = {
  ...basePipelineStatus,
  id: "op-1",
  subdomain: "amberd-globex-ada",
  run_id: 12345,
} as unknown as PipelineStatusRecord;

vi.mock("@hooks/useDeployments", () => ({
  useTierDeploymentActions: () => ({
    pendingDeployments: [deploying],
    isAppMigrating: () => false,
    isAppTerminating: () => false,
    isAppUpdating: () => false,
    migrationPipelineForApp: () => null,
    updatePipelineForApp: () => null,
    terminatePipelineForApp: () => null,
    updateTarget: null,
    setUpdateTarget: () => {},
    openUpdate: () => {},
    updateModalInitialValues: undefined,
    onUpdateDeployed: () => {},
    triggerMoveTier: async () => {},
    bombTarget: null,
    setBombTarget: () => {},
    openTerminate: () => {},
    isBombing: false,
    confirmTerminate: async () => {},
    cancelPipelineRun: async () => {},
  }),
}));

// The update dialog is closed throughout; its form needs a query client.
vi.mock("./NewDeploymentModal", () => ({ default: () => null }));

const app: Application = {
  id: 1,
  name: "ada",
  nodename: "amberd-acme-ada",
  client_name: "Acme",
  instance_name: "ada",
  version: "v1.2.0",
  cpu: 10,
  memory: 20,
  gpu: 0,
  status: "Healthy",
};

async function renderTier(role: "Admin" | "User") {
  signInAs(role);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <AuthProvider>
          <ApplicationList tierIndex={0} apps={[app]} loading={false} />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Acme");
}

async function cardMenu() {
  await act(async () => {
    fireEvent.keyDown(screen.getByRole("button", { name: "Deployment actions" }), { key: "Enter" });
  });
  return screen.getAllByRole("menuitem").map((item) => item.textContent);
}

describe("what the tier page lets each role do", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(financeApi, "fetchAppCost").mockResolvedValue(financeApi.COST_UNAVAILABLE);
  });

  it("lets a User open the app and read its logs, and nothing else", async () => {
    await renderTier("User");
    expect(screen.getByText("amberd-globex-ada")).toBeTruthy(); // sees what is deploying
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
    expect(await cardMenu()).toEqual(["Go to URL", "View logs"]);
  });

  it("lets an Admin move, update, terminate and cancel", async () => {
    await renderTier("Admin");
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeTruthy();
    expect(await cardMenu()).toEqual([
      "Go to URL",
      "Move to Tier",
      "Update",
      "View logs",
      "Terminate",
    ]);
  });
});
