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
