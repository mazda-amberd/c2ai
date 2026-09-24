import type { PipelineRunRecord } from "@/types/deployment";
import type { ApiDeployment } from "@api/services/registeredApplications";

export type DeploymentFormValues = {
  branch: string;
  customer_name: string;
  subdomain: string;
  env_instance: string;
};

export type UseNewDeploymentFormProps = {
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
  targetInstance?: {
    nodename: string;
  };
};
