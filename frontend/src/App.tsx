import { RouterProvider } from "react-router-dom";
import { AuthProvider } from "@auth/AuthContext";
import { ToastProvider } from "@components/Toast";

import { router } from "@/router";

function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </AuthProvider>
  );
}

export default App;
