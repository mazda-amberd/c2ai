import { AccountMenu } from "@components/AccountMenu";
import athenaLogo from "../assets/c2ai.png";

const Header = () => {
  return (
    <div
      className="py-3 px-6 flex h-[60px] items-center justify-between gap-2 relative border-b border-muted bg-header"
      id="header"
    >
      <img src={athenaLogo} alt="Logo" className="h-10 w-auto" />
      <AccountMenu />
    </div>
  );
};

export { Header };
