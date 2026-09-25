import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/auth/AuthContext";
import { ToastProvider } from "@components/Toast";
import * as financeApi from "@/utils/financeApi";
import * as registeredAppsApi from "@/utils/registeredAppsApi";
import type { RegisteredApp } from "@/utils/registeredAppsApi";
import * as registeredApplicationsApi from "@api/services/registeredApplications";
import * as registrationApi from "@/utils/registrationApi";
import { signInAs } from "@/test-utils/auth";

import Applications from "./Applications";
import RegisteredApplications from "./RegisteredApplications";

// The tier's contents and 3D cube are beside the point here.
vi.mock("@hooks/useMetrics", () => ({
  useTierData: () => ({ apps: [], isLoading: false, tierGpuTotal: null }),
}));
vi.mock("@hooks/useDeployments", () => ({ useInvalidateDeploymentsForTierIndex: () => () => {} }));
vi.mock("@components/ApplicationList", () => ({ default: () => <p>Applications list</p> }));
vi.mock("@components/Cube", () => ({ default: () => null }));
vi.mock("@components/registerApp/DeployApplicationModal", () => ({ default: () => null }));

const ADA: RegisteredApp = {
  id: "ada00000-0000-4000-8000-000000000001",
  name: "ADA",
  desc: "Amberd ADA",
  type: "github",
  status: "active",
  version: 1,
  instances: 0,
  tiers: {},
  canDelete: true,
  canEdit: true,
  created: "2026-09-24",
};

async function visit(path: string, role: "Admin" | "User") {
  signInAs(role);
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <Routes>
            <Route path="/apps/:tierIndex" element={<Applications />} />
            <Route path="/registered-applications" element={<RegisteredApplications />} />
            <Route path="/" element={<p>Home page</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </ToastProvider>,
  );
}

describe("tier page and catalog by role", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(financeApi, "fetchTierCost").mockResolvedValue(financeApi.COST_UNAVAILABLE);
  });

  it("shows a User the tier, its metrics and the catalog, but not Deploy", async () => {
    await visit("/apps/1", "User");
    expect(await screen.findByText("Applications list")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Tier Metrics/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Registered Applications" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Deploy" })).toBeNull();
  });

  it("gives an Admin Deploy and the catalog", async () => {
    await visit("/apps/1", "Admin");
    expect(await screen.findByRole("button", { name: "Deploy" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Registered Applications" })).toBeTruthy();
  });

  it("lets a User browse the catalog and open a summary, without Register or Delete", async () => {
    vi.spyOn(registeredAppsApi, "fetchRegisteredApps").mockResolvedValue([ADA]);
    const detail = vi
      .spyOn(registeredApplicationsApi, "getRegisteredApplication")
      .mockRejectedValue(new Error("not needed here"));
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([]);
    await visit("/registered-applications", "User");

    expect(await screen.findByText("ADA")).toBeTruthy();
    expect(screen.getByText("View only")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Register Application" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete ADA" })).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByText("ADA"));
    });
    expect(detail).toHaveBeenCalledWith(ADA.id);
  });

  it("gives an Admin Register and Delete in the catalog", async () => {
    vi.spyOn(registeredAppsApi, "fetchRegisteredApps").mockResolvedValue([ADA]);
    await visit("/registered-applications", "Admin");

    expect(await screen.findByText("ADA")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Register Application" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete ADA" })).toBeTruthy();
    expect(screen.queryByText("View only")).toBeNull();
  });
});
