import { Outlet } from "react-router-dom";
import { Header } from "@components/Header";
import ProtectedRoute from "@auth/ProtectedRoute";

export default function Layout() {
  return (
    <ProtectedRoute>
    <div className="min-h-screen flex flex-col">
      <Header />
      <div className="flex-1">
        <Outlet />
      </div>
    </div>
    </ProtectedRoute>
  );
}
