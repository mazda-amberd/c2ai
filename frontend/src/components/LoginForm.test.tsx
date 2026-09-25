import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/api/services/auth";
import { AuthProvider } from "@/auth/AuthContext";
import ProtectedRoute from "@/auth/ProtectedRoute";
import type { WhoAmIResponse } from "@/types/auth";

import { LoginForm } from "./LoginForm";

const ANSWER =
  "If that address belongs to an account here, a temporary password is on its way. " +
  "Check your email, then sign in with it - you will be asked to choose your own.";

const me = (needsPasswordReset: boolean): WhoAmIResponse => ({
  identifier: "alice@example.com",
  service: "athena",
  first_name: "Alice",
  metadata: { role: "", user_type: "Admin", needs_password_reset: needsPasswordReset },
});

function renderLogin(path = "/login") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginForm />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <p>Home page</p>
              </ProtectedRoute>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

async function signIn(password: string) {
  type("Username", "alice@example.com");
  type("Password", password);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  });
}

describe("LoginForm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Nobody is signed in when the page opens.
    vi.spyOn(authApi, "whoAmIRequest").mockRejectedValue(new Error("401"));
  });

  it("emails a temporary password from Forgot password? and says the same whatever happens", async () => {
    const forgot = vi
      .spyOn(authApi, "forgotPasswordRequest")
      .mockResolvedValue({ ok: true, detail: ANSWER });
    renderLogin();

    type("Username", "alice@example.com");
    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));

    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeTruthy();
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("alice@example.com");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Email me a temporary password" }));
    });

    expect(forgot).toHaveBeenCalledWith("alice@example.com");
    expect(screen.getByRole("heading", { name: "Log in to your account" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(ANSWER);
    expect((screen.getByLabelText("Username") as HTMLInputElement).value).toBe(
      "alice@example.com",
    );
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Password")));
  });

  it("asks for an address, and goes back to sign in without sending", async () => {
    const forgot = vi.spyOn(authApi, "forgotPasswordRequest");
    renderLogin();

    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Email me a temporary password" }));
    });
    expect(screen.getByRole("alert").textContent).toBe("Enter the address you sign in with.");

    fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(screen.getByRole("heading", { name: "Log in to your account" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(forgot).not.toHaveBeenCalled();
  });

  it("makes somebody on a temporary password choose their own before going in", async () => {
    vi.spyOn(authApi, "loginRequest").mockResolvedValue({ access_token: "t" });
    const whoami = vi.spyOn(authApi, "whoAmIRequest");
    whoami.mockRejectedValueOnce(new Error("401")).mockResolvedValue(me(true));
    const choose = vi
      .spyOn(authApi, "choosePasswordRequest")
      .mockResolvedValue({ message: "Password updated successfully." });
    renderLogin();
    await waitFor(() => expect(whoami).toHaveBeenCalledTimes(1));

    await signIn("Abcd-Efgh-Jkmn");

    expect(screen.getByRole("heading", { name: "Choose a password" })).toBeTruthy();
    expect(screen.getByText(/Welcome, Alice\. The password you were sent is temporary/)).toBeTruthy();

    type("New password", "N3w!secret");
    type("Confirm new password", "N3w!secreT");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    });
    expect(screen.getByRole("alert").textContent).toBe("The two passwords are not the same.");
    expect(choose).not.toHaveBeenCalled();

    whoami.mockResolvedValue(me(false));
    type("Confirm new password", "N3w!secret");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    });
    expect(choose).toHaveBeenCalledWith("N3w!secret");
    expect(await screen.findByText("Home page")).toBeTruthy();
  });

  it("shows the server's reason when the new password is refused", async () => {
    vi.spyOn(authApi, "whoAmIRequest").mockResolvedValue(me(true));
    vi.spyOn(authApi, "choosePasswordRequest").mockRejectedValue(
      new Error("Password must include at least one uppercase letter."),
    );
    renderLogin();

    await screen.findByLabelText("New password");
    type("New password", "weakpass1!");
    type("Confirm new password", "weakpass1!");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    });
    expect(screen.getByRole("alert").textContent).toBe(
      "Password must include at least one uppercase letter.",
    );
  });

  it("keeps somebody on a temporary password out of every other page", async () => {
    vi.spyOn(authApi, "whoAmIRequest").mockResolvedValue(me(true));
    renderLogin("/");
    expect(await screen.findByRole("heading", { name: "Choose a password" })).toBeTruthy();
    expect(screen.queryByText("Home page")).toBeNull();
  });

  it("goes straight in with a password that is not temporary", async () => {
    vi.spyOn(authApi, "loginRequest").mockResolvedValue({ access_token: "t" });
    const whoami = vi.spyOn(authApi, "whoAmIRequest");
    whoami.mockRejectedValueOnce(new Error("401")).mockResolvedValue(me(false));
    renderLogin();
    await waitFor(() => expect(whoami).toHaveBeenCalledTimes(1));

    await signIn("admin@amberd.ai");

    expect(await screen.findByText("Home page")).toBeTruthy();
  });
});
