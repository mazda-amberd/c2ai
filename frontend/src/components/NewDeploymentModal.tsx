import { Rocket, RefreshCw } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@ui/dialog";
import type { PipelineRunRecord } from "@/types/deployment";
import type { ApiDeployment } from "@api/services/registeredApplications";
import NewDeploymentForm from "./NewDeploymentForm";
import { useNewDeploymentForm } from "@hooks/useNewDeploymentForm";
import { appsColors } from "@styles/appsColors";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tierIndex: number;
  onDeployed: (deployment: PipelineRunRecord | ApiDeployment) => void;
  mode?: "new" | "update";
  initialValues?: {
    subdomain?: string;
    customer_name?: string;
    env_instance?: string;
  };
  targetApp?: {
    name: string;
    nodename: string;
    version?: string | null;
  };
};

export default function NewDeploymentModal({
  open,
  onOpenChange,
  tierIndex,
  onDeployed,
  mode = "new",
  initialValues,
  targetApp,
}: Props) {
  const vm = useNewDeploymentForm({
    open,
    onOpenChange,
    tierIndex,
    onDeployed,
    mode,
    initialValues,
    targetInstance: targetApp,
  });
  const { isUpdate, handleOpenChange } = vm;

  const appStyle = appsColors[tierIndex] ?? appsColors[0];

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="sm:max-w-[440px]"
        style={{
          background: `${appStyle.cardBackground}, hsl(var(--background))`,
          borderColor: appStyle.border,
          boxShadow: appStyle.shadow,
        }}
      >
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2"
            style={{ color: appStyle.text }}
          >
            {isUpdate ? (
              <RefreshCw className="h-5 w-5" />
            ) : (
              <Rocket className="h-5 w-5" />
            )}
            {isUpdate ? "Update Deployment" : "New Deployment"}
          </DialogTitle>
          {isUpdate && targetApp ? (
            <div className="pt-0.5 space-y-0.5">
              <p className="text-sm font-medium text-foreground">{targetApp.name}</p>
              <p className="text-xs text-muted-foreground font-mono">{targetApp.nodename}</p>
              {targetApp.version && (
                <p className="text-xs text-muted-foreground">
                  Current version: <span className="font-medium text-foreground">{targetApp.version}</span>
                </p>
              )}
            </div>
          ) : (
            <DialogDescription>
              Deploy a new instance on Tier {tierIndex + 1}.
            </DialogDescription>
          )}
        </DialogHeader>

        <NewDeploymentForm vm={vm} appStyle={appStyle} />
      </DialogContent>
    </Dialog>
  );
}
