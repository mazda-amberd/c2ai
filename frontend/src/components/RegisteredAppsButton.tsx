import { Library } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@auth/AuthContext";

/** Purple "Registered Applications" action from the template — opens the
 *  catalog page. Shown next to the cost chip on Tier Management and between
 *  the cost chip and Deploy on tier pages. Admins only: the catalog is where
 *  templates are created and deleted. */
export default function RegisteredAppsButton() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  if (!isAdmin) return null;

  return (
    <button
      type="button"
      onClick={() => navigate("/registered-applications")}
      className="inline-flex h-9 shrink-0 items-center gap-2 rounded-md border border-[#a855f7]/40 bg-[rgba(168,85,247,0.12)] px-3.5 py-2 text-sm font-semibold text-[#c084fc] transition-colors hover:bg-[rgba(168,85,247,0.2)] hover:text-[#d8b4fe]"
    >
      <Library className="h-4 w-4" />
      Registered Applications
    </button>
  );
}
