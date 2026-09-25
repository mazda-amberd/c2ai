import { apiFetch } from "@/api";

export type UserRole = "admin" | "user";

export type ManagedUser = {
  user_id: string;
  email: string;
  first_name: string;
  last_name: string;
  name: string;
  role: UserRole;
  role_label: string;
  must_change_password: boolean;
  status: "invited" | "active";
  created_at: string | null;
};

/** After adding someone or issuing a new temporary password. The password is
 *  present only when the email did not go - shown once, never retrievable. */
export type IssuedPassword = ManagedUser & {
  email_sent: boolean;
  email_error: string;
  temporary_password: string;
};

export type UserDirectory = {
  users: ManagedUser[];
  roles: { value: UserRole; label: string }[];
  admins: number;
  email_configured: boolean;
};

export type UserInput = {
  first_name: string;
  last_name: string;
  email: string;
  role: UserRole;
};

export const fetchUsers = () => apiFetch<UserDirectory>("/api/users");

export const addUser = (input: UserInput) =>
  apiFetch<IssuedPassword>("/api/users", { method: "POST", body: JSON.stringify(input) });

export const updateUser = (userId: string, input: UserInput) =>
  apiFetch<ManagedUser>(`/api/users/${encodeURIComponent(userId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });

export const resetUserPassword = (userId: string) =>
  apiFetch<IssuedPassword>(`/api/users/${encodeURIComponent(userId)}/reset-password`, {
    method: "POST",
  });

export const removeUser = (userId: string) =>
  apiFetch<{ deleted: string; email: string }>(`/api/users/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
