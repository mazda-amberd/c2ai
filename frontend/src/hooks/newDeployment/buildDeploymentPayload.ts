import { DEPLOY_DOMAIN } from "@/constants/deployment";
import type { TriggerDeploymentPayload } from "@/types/deployment";
import { workflowPrepareSubdomain } from "@/utils/subdomain";

import type { DeploymentFormValues } from "./types";

export type BuildDeploymentPayloadContext = {
  isUpdate: boolean;
  tierIndex: number;
  initialSubdomain: string;
  initialValues?: {
    customer_name?: string;
    env_instance?: string;
  };
};

export function buildDeploymentPayload(
  values: DeploymentFormValues,
  ctx: BuildDeploymentPayloadContext,
): TriggerDeploymentPayload {
  const tier = ctx.tierIndex + 1;
  if (ctx.isUpdate) {
    return {
      branch: values.branch.trim(),
      subdomain: ctx.initialSubdomain,
      customer_name: ctx.initialValues?.customer_name?.trim() ?? "",
      env_instance: ctx.initialValues?.env_instance?.trim() ?? "",
      domain: DEPLOY_DOMAIN,
      tier,
    };
  }
  return {
    branch: values.branch.trim(),
    subdomain: workflowPrepareSubdomain(
      values.customer_name.trim(),
      values.env_instance.trim(),
    ),
    customer_name: values.customer_name.trim(),
    env_instance: values.env_instance.trim(),
    domain: DEPLOY_DOMAIN,
    tier,
  };
}
