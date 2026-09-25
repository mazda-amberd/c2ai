import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/api/services/auth";
import { AuthProvider } from "@/auth/AuthContext";
import { ToastProvider } from "@components/Toast";
import type { WhoAmIResponse } from "@/types/auth";

import { AccountMenu } from "./AccountMenu";

const me = (userType: string): WhoAmIResponse => ({
  identifier: "mazda@amberd.ai",
  service: "athena",
  first_name: "Mazda",
  last_name: "Marvasti",
  metadata: { role: "", user_type: userType, needs_password_reset: false },
});

async function openMenu(userType: string) {
  vi.spyOn(authApi, "whoAmIRequest").mockResolvedValue(me(userType));
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<AccountMenu />} />
            <Route path="/users" element={<p>Users page</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </ToastProvider>,
  );
  const button = await screen.findByRole("button", { name: "Account menu" });
  expect(button.textContent).toBe("M");
  await act(async () => {
    fireEvent.keyDown(button, { key: "Enter" });
  });
}

describe("AccountMenu", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows who is signed in, Users, Change password, and Log out at the very bottom", async () => {
    await openMenu("Admin");

    const menu = screen.getByRole("menu");
    expect(menu.textContent).toContain("Mazda Marvasti");
    expect(menu.textContent).toContain("mazda@amberd.ai");
    const items = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(items).toEqual(["Users", "Change password", "Log out"]);

    fireEvent.click(screen.getByRole("menuitem", { name: "Users" }));
    expect(await screen.findByText("Users page")).toBeTruthy();
  });

  it("offers a User Change password and Log out, but not Users", async () => {
    await openMenu("User");
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Change password",
      "Log out",
    ]);
  });

  it("changes your own password, answering every complaint inside the dialog", async () => {
    const change = vi
      .spyOn(authApi, "choosePasswordRequest")
      .mockRejectedValueOnce(new Error("Password must include at least one number."))
      .mockResolvedValue({ message: "Password updated successfully." });
    await openMenu("User");
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "Change password" }));
    });

    const dialog = await screen.findByRole("dialog", { name: "Change your password" });
    const type = (label: string, value: string) =>
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
    const submit = () =>
      act(async () => {
        fireEvent.click(within(dialog).getByRole("button", { name: "Change password" }));
      });

    type("New password", "N3w!secret");
    type("Type it again", "N3w!secreT");
    await submit();
    expect(within(dialog).getByRole("alert").textContent).toBe("The two passwords are not the same.");
    expect(change).not.toHaveBeenCalled();

    type("New password", "Nnew!secret");
    type("Type it again", "Nnew!secret");
    await submit();
    expect(within(dialog).getByRole("alert").textContent).toBe(
      "Password must include at least one number.",
    );

    type("New password", "N3w!secret");
    type("Type it again", "N3w!secret");
    await submit();
    expect(change).toHaveBeenLastCalledWith("N3w!secret");
    expect(
      await screen.findByText("Password changed. Other browsers you were signed in on are signed out."),
    ).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
