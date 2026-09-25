import { apiFetch } from "@/api";

import type { WhoAmIResponse } from "@/types/auth";

type LoginResponse = {
  access_token: string;
  token_type?: string;
};

export const loginRequest = (username: string, password: string) =>
  apiFetch<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ identifier: username, password }),
    credentials: "include",
  });

export const whoAmIRequest = () =>
  apiFetch<WhoAmIResponse>("/auth/whoami", {
    method: "GET",
    credentials: "include",
  });

export const logoutRequest = () =>
  apiFetch("/auth/logout", {
    method: "POST",
    credentials: "include",
  });

export type ForgotPasswordResponse = {
  ok: boolean;
  detail: string;
};

/** Same answer whether or not the address belongs to anyone. */
export const forgotPasswordRequest = (email: string) =>
  apiFetch<ForgotPasswordResponse>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });

/** Replace the signed-in user's own password (clears a temporary one). */
export const choosePasswordRequest = (newPassword: string) =>
  apiFetch<{ message: string }>("/users/update_password", {
    method: "PATCH",
    body: JSON.stringify({ new_password: newPassword }),
    credentials: "include",
  });
