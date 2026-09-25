import { LogOut, Users } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@auth/AuthContext";
import Avatar from "@components/users/Avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@ui/dropdown-menu";

const ITEM_CLASS = "cursor-pointer gap-2.5 px-3 py-2 text-[13px] text-[#cfd8e3] focus:bg-[rgba(32,171,199,0.12)] focus:text-[#eef2f6]";

/** The round button with your initial, top right: who is signed in, the
 *  Users page (Admins), and Log out at the very bottom. */
export function AccountMenu() {
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;

  const name = [user.first_name, user.last_name].filter(Boolean).join(" ").trim();
  const role = user.metadata?.user_type || "User";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="Account menu"
          className="rounded-full outline-none ring-offset-2 ring-offset-[#040b14] transition-[filter] hover:brightness-125 focus-visible:ring-2 focus-visible:ring-[#20abc7]"
        >
          <Avatar name={name} email={user.identifier} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={10} className="min-w-[242px] p-0">
        <div className="border-b border-[#1c2836] px-4 py-3">
          <div className="text-[13.5px] font-bold text-[#eef2f6]">{name || user.identifier}</div>
          <div className="text-[12px] text-[#8b97a5]">{user.identifier}</div>
          <div className="mt-0.5 text-[11px] text-[#57606c]">{role}</div>
        </div>
        <div className="p-1">
          {isAdmin && (
            <>
              <DropdownMenuItem className={ITEM_CLASS} onSelect={() => navigate("/users")}>
                <Users className="h-4 w-4" />
                Users
              </DropdownMenuItem>
              <DropdownMenuSeparator />
            </>
          )}
          <DropdownMenuItem className={ITEM_CLASS} onSelect={() => void logout()}>
            <LogOut className="h-4 w-4" />
            Log out
          </DropdownMenuItem>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
