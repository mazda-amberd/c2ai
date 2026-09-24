import React, { createContext, useContext, useEffect, useState } from "react";
import getRouterBasename from "@/lib/router";
import {
  loginRequest,
  whoAmIRequest,
  logoutRequest,
} from "@/api/services/auth";
import type { WhoAmIResponse } from "@/types/auth";

type AuthContextType = {
  isAuthenticated: boolean;
  checkingAuth: boolean;
  user: WhoAmIResponse | null;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [user, setUser] = useState<WhoAmIResponse | null>(null);

  const checkSession = async () => {
    try {
      const me = await whoAmIRequest();
      setUser(me);
      setIsAuthenticated(true);
    } catch {
      setUser(null);
      setIsAuthenticated(false);
    } finally {
      setCheckingAuth(false);
    }
  };

  useEffect(() => {
    checkSession();
  }, []);

  const login = async (username: string, password: string) => {
    try {
      await loginRequest(username, password);

      // Immediately hydrate user after login
      const me = await whoAmIRequest();
      setUser(me);
      setIsAuthenticated(true);

      return true;
    } catch (error) {
      console.error("Login failed:", error);
      return false;
    }
  };

  const logout = async () => {
    try {
      await logoutRequest();
    } finally {
      setUser(null);
      setIsAuthenticated(false);
      window.location.href = getRouterBasename() + "/login";
    }
  };

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        checkingAuth,
        user,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
