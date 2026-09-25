import { vi } from "vitest";

import * as authApi from "@/api/services/auth";

/** The signed-in person the AuthProvider will load, as an Admin or a User. */
export function signInAs(role: "Admin" | "User") {
  vi.spyOn(authApi, "whoAmIRequest").mockResolvedValue({
    identifier: role === "Admin" ? "admin@amberd.ai" : "viewer@example.com",
    service: "athena",
    first_name: role === "Admin" ? "Amberd" : "Vera",
    last_name: role === "Admin" ? "Admin" : "Viewer",
    metadata: { role: "", user_type: role, needs_password_reset: false },
  });
}
