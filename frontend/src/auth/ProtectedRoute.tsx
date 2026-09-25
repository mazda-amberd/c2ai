import { Navigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import type { JSX } from "react";

export default function ProtectedRoute({
  children,
}: {
  children: JSX.Element;
}) {
  const { isAuthenticated, checkingAuth, mustChoosePassword } = useAuth();

  if (checkingAuth) {
    return null;
  }

  // On a temporary password the only page is the one that replaces it.
  if (!isAuthenticated || mustChoosePassword) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
