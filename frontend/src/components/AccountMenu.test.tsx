import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/api/services/auth";
import { AuthProvider } from "@/auth/AuthContext";
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
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<AccountMenu />} />
          <Route path="/users" element={<p>Users page</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
  const button = await screen.findByRole("button", { name: "Account menu" });
  expect(button.textContent).toBe("M");
  await act(async () => {
    fireEvent.keyDown(button, { key: "Enter" });
  });
}

describe("AccountMenu", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows who is signed in, Users, and Log out at the very bottom", async () => {
    await openMenu("Admin");

    const menu = screen.getByRole("menu");
    expect(menu.textContent).toContain("Mazda Marvasti");
    expect(menu.textContent).toContain("mazda@amberd.ai");
    const items = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(items).toEqual(["Users", "Log out"]);

    fireEvent.click(screen.getByRole("menuitem", { name: "Users" }));
    expect(await screen.findByText("Users page")).toBeTruthy();
  });

  it("offers a User only Log out", async () => {
    await openMenu("User");
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Log out"]);
  });
});
