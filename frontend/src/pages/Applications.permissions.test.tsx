import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/auth/AuthContext";
import { ToastProvider } from "@components/Toast";
import * as financeApi from "@/utils/financeApi";
import * as registeredAppsApi from "@/utils/registeredAppsApi";
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

  it("shows a User the tier and its metrics, but not Deploy or the catalog", async () => {
    await visit("/apps/1", "User");
    expect(await screen.findByText("Applications list")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Tier Metrics/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Deploy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Registered Applications" })).toBeNull();
  });

  it("gives an Admin Deploy and the catalog", async () => {
    await visit("/apps/1", "Admin");
    expect(await screen.findByRole("button", { name: "Deploy" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Registered Applications" })).toBeTruthy();
  });

  it("sends a User away from the catalog of templates", async () => {
    const catalog = vi.spyOn(registeredAppsApi, "fetchRegisteredApps");
    await visit("/registered-applications", "User");
    expect(await screen.findByText("Home page")).toBeTruthy();
    expect(catalog).not.toHaveBeenCalled();
  });
});
