import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/auth/AuthContext";
import { ToastProvider } from "@components/Toast";
import * as registeredAppsApi from "@/utils/registeredAppsApi";
import type { RegisteredApp } from "@/utils/registeredAppsApi";
import * as registeredApplicationsApi from "@api/services/registeredApplications";
import * as registrationApi from "@/utils/registrationApi";
import { signInAs } from "@/test-utils/auth";

import RegisteredApplications from "./RegisteredApplications";

const row = (overrides: Partial<RegisteredApp>): RegisteredApp => ({
  id: "app-1",
  name: "chat-service",
  desc: "",
  type: "container",
  status: "active",
  version: 1,
  instances: 0,
  tiers: {},
  canDelete: true,
  canEdit: true,
  created: "2026-09-24",
  ...overrides,
});

const IDLE = row({});
const LIVE = row({ id: "app-2", name: "billing", instances: 2, tiers: { "Tier 1": 2 }, canEdit: false });
const ADA = row({ id: "ada", name: "ADA", type: "github", canEdit: false });

async function visit(role: "Admin" | "User") {
  signInAs(role);
  vi.spyOn(registeredAppsApi, "fetchRegisteredApps").mockResolvedValue([IDLE, LIVE, ADA]);
  render(
    <ToastProvider>
      <MemoryRouter>
        <AuthProvider>
          <RegisteredApplications />
        </AuthProvider>
      </MemoryRouter>
    </ToastProvider>,
  );
  await screen.findByText("chat-service");
}

describe("editing a registered application", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([]);
    vi.spyOn(registrationApi, "fetchContainerRegistries").mockResolvedValue([]);
  });

  it("is not offered to a User", async () => {
    await visit("User");
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
  });

  it("opens the wizard on a template with nothing deployed", async () => {
    const detail = vi
      .spyOn(registeredApplicationsApi, "getRegisteredApplication")
      .mockReturnValue(new Promise(() => {}));
    await visit("Admin");

    expect(screen.getByRole("button", { name: "Edit chat-service" }).getAttribute("aria-disabled")).toBe(
      "false",
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Edit chat-service" }));
    });
    expect(await screen.findByText("Edit chat-service")).toBeTruthy();
    expect(detail).toHaveBeenCalledWith("app-1");
  });

  it("says why a deployed template, or ADA, cannot be edited", async () => {
    const detail = vi.spyOn(registeredApplicationsApi, "getRegisteredApplication");
    await visit("Admin");

    const live = screen.getByRole("button", { name: "Edit billing" });
    expect(live.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(live);
    expect(
      await screen.findByText("Can't edit 'billing' while it's deployed — terminate its 2 instances first."),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit ADA" }));
    expect(await screen.findByText(/'ADA' is C2AI's built-in application/)).toBeTruthy();
    await waitFor(() => expect(detail).not.toHaveBeenCalled());
  });
});
