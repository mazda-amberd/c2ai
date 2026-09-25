import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/api/services/auth";
import * as usersApi from "@/api/services/users";
import type { IssuedPassword, ManagedUser, UserDirectory } from "@/api/services/users";
import { AuthProvider } from "@/auth/AuthContext";
import { ToastProvider } from "@components/Toast";

import Users from "./Users";

const person = (over: Partial<ManagedUser>): ManagedUser => ({
  user_id: "u-1",
  email: "admin@amberd.ai",
  first_name: "Amberd",
  last_name: "Admin",
  name: "Amberd Admin",
  role: "admin",
  role_label: "Admin",
  must_change_password: false,
  status: "active",
  created_at: "2026-09-25T09:30:00",
  ...over,
});

const ADMIN = person({});
const NEWCOMER = person({
  user_id: "u-2",
  email: "new.person@example.com",
  first_name: "New",
  last_name: "Person",
  name: "New Person",
  role: "user",
  role_label: "User",
  must_change_password: true,
  status: "invited",
});

const directory = (users: ManagedUser[], emailConfigured = true): UserDirectory => ({
  users,
  roles: [
    { value: "user", label: "User" },
    { value: "admin", label: "Admin" },
  ],
  admins: users.filter((u) => u.role === "admin").length,
  email_configured: emailConfigured,
});

const issued = (over: Partial<IssuedPassword> = {}): IssuedPassword => ({
  ...NEWCOMER,
  email_sent: true,
  email_error: "",
  temporary_password: "",
  ...over,
});

function renderPage(userType = "Admin") {
  vi.spyOn(authApi, "whoAmIRequest").mockResolvedValue({
    identifier: "admin@amberd.ai",
    service: "athena",
    first_name: "Amberd",
    last_name: "Admin",
    metadata: { role: "", user_type: userType, needs_password_reset: false },
  });
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/users"]}>
        <AuthProvider>
          <Routes>
            <Route path="/users" element={<Users />} />
            <Route path="/" element={<p>Home page</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </ToastProvider>,
  );
}

const row = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = async (element: HTMLElement) => {
  await act(async () => {
    fireEvent.click(element);
  });
};

describe("Users page", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("lists who can sign in, and will not let the only Admin be removed", async () => {
    vi.spyOn(usersApi, "fetchUsers").mockResolvedValue(directory([ADMIN, NEWCOMER]));
    renderPage();

    expect(await screen.findByText("new.person@example.com")).toBeTruthy();
    expect(within(row("Amberd Admin")).getByText("Admin")).toBeTruthy();
    expect(within(row("New Person")).getByText("Invited — must set a password")).toBeTruthy();
    expect(within(row("Amberd Admin")).getByText("2026-09-25")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Remove Amberd Admin" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Remove New Person" }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(screen.getByText(/A new user is emailed a temporary password/)).toBeTruthy();
  });

  it("adds someone and says the invitation was emailed", async () => {
    vi.spyOn(usersApi, "fetchUsers")
      .mockResolvedValueOnce(directory([ADMIN]))
      .mockResolvedValue(directory([ADMIN, NEWCOMER]));
    const add = vi.spyOn(usersApi, "addUser").mockResolvedValue(issued());
    renderPage();
    await screen.findByText("Amberd Admin");

    await click(screen.getByRole("button", { name: "Add user" }));
    const save = within(screen.getByRole("dialog")).getByRole("button", { name: "Add user" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    type("First name", " New ");
    type("Last name", "Person");
    type("Email", "new.person@example.com");
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "admin" } });
    expect(screen.getByText(/Admins can do everything a User can/)).toBeTruthy();
    await click(save);

    expect(add).toHaveBeenCalledWith({
      first_name: "New",
      last_name: "Person",
      email: "new.person@example.com",
      role: "admin",
    });
    expect(
      await screen.findByText("New Person added — invitation emailed to new.person@example.com"),
    ).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("new.person@example.com")).toBeTruthy();
  });

  it("shows the temporary password once when the email did not go", async () => {
    vi.spyOn(usersApi, "fetchUsers").mockResolvedValue(directory([ADMIN], false));
    vi.spyOn(usersApi, "addUser").mockResolvedValue(
      issued({
        email_sent: false,
        email_error: "Email is not configured on this deployment.",
        temporary_password: "Abcd-Efgh-Jkmn",
      }),
    );
    renderPage();
    expect(await screen.findByText(/Email is not configured on this deployment/)).toBeTruthy();

    await click(screen.getByRole("button", { name: "Add user" }));
    type("First name", "New");
    type("Last name", "Person");
    type("Email", "new.person@example.com");
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add user" }));

    const box = await screen.findByRole("status");
    expect(box.textContent).toContain("New Person added, but the email did not go.");
    expect(within(box).getByText("Abcd-Efgh-Jkmn")).toBeTruthy();
  });

  it("keeps the form open with the reason when saving is refused", async () => {
    vi.spyOn(usersApi, "fetchUsers").mockResolvedValue(directory([ADMIN, NEWCOMER]));
    vi.spyOn(usersApi, "updateUser").mockRejectedValue(
      new Error("admin@amberd.ai is already someone else's address."),
    );
    renderPage();
    await screen.findByText("New Person");

    await click(within(row("New Person")).getByRole("button", { name: "Edit" }));
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe(
      "new.person@example.com",
    );
    type("Email", "admin@amberd.ai");
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("admin@amberd.ai is already someone else's address.")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("issues a new temporary password and removes people after asking", async () => {
    vi.spyOn(usersApi, "fetchUsers").mockResolvedValue(directory([ADMIN, NEWCOMER]));
    const reset = vi.spyOn(usersApi, "resetUserPassword").mockResolvedValue(issued());
    const remove = vi
      .spyOn(usersApi, "removeUser")
      .mockResolvedValue({ deleted: "u-2", email: "new.person@example.com" });
    renderPage();
    await screen.findByText("New Person");

    await click(screen.getByRole("button", { name: "Issue New Person a new temporary password" }));
    expect(screen.getByRole("dialog", { name: "Issue a new temporary password?" })).toBeTruthy();
    await click(screen.getByRole("button", { name: "Issue it" }));
    expect(reset).toHaveBeenCalledWith("u-2");
    expect(
      await screen.findByText("New Person reset — invitation emailed to new.person@example.com"),
    ).toBeTruthy();

    await click(screen.getByRole("button", { name: "Remove New Person" }));
    expect(screen.getByRole("dialog", { name: "Remove New Person?" }).textContent).toContain(
      "They lose access immediately.",
    );
    await click(screen.getByRole("button", { name: "Remove" }));
    expect(remove).toHaveBeenCalledWith("u-2");
    expect(await screen.findByText("New Person removed")).toBeTruthy();
  });

  it("sends a User back to the home page", async () => {
    const list = vi.spyOn(usersApi, "fetchUsers");
    renderPage("User");
    expect(await screen.findByText("Home page")).toBeTruthy();
    expect(list).not.toHaveBeenCalled();
  });
});
