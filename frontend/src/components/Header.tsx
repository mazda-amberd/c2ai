import { Button } from "@ui/button";
import { useAuth } from "@auth/AuthContext";
import athenaLogo from "../assets/c2ai.png";

const Header = () => {
  const { logout } = useAuth();

  return (
    <div
      className="py-3 px-6 flex h-[60px] items-center justify-between gap-2 relative border-b border-muted bg-header"
      id="header"
    >
      <img src={athenaLogo} alt="Logo" className="h-10 w-auto" />
      <Button variant="ghost" onClick={() => logout()}>
        Log Out
      </Button>
    </div>
  );
};

export { Header };
