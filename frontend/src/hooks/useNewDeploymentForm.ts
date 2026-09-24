import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";

import { triggerDeployment, triggerUpdateDeployment } from "@api/services/deployments";
import { DEPLOY_DOMAIN } from "@/constants/deployment";
import { deploymentPreviewUrl, workflowPrepareSubdomain } from "@/utils/subdomain";
import { useDeployBranchOptions } from "@hooks/useDeployments";
import {
  findRegisteredDeploymentForInstance,
  upgradeDeployment,
} from "@api/services/registeredApplications";
import {
  fetchAppVersions,
  fetchRegisteredAppDetail,
} from "@/utils/registeredAppsApi";

import { buildDeploymentPayload } from "./newDeployment/buildDeploymentPayload";
import {
  createCustomerNameValidator,
  createEnvInstanceValidator,
  validateBranchCharacters,
  validateSubdomainWhenUpdate,
} from "./newDeployment/deploymentFormValidators";
import type { DeploymentFormValues, UseNewDeploymentFormProps } from "./newDeployment/types";
import { useSubdomainFieldLock } from "./newDeployment/useSubdomainFieldLock";

export type { DeploymentFormValues } from "./newDeployment/types";

export function useNewDeploymentForm({
  open,
  onOpenChange,
  tierIndex,
  onDeployed,
  mode = "new",
  initialValues,
  targetInstance,
}: UseNewDeploymentFormProps) {
  const isUpdate = mode === "update";
  const initialSubdomain = initialValues?.subdomain ?? "";

  const [submitError, setSubmitError] = useState<string | null>(null);
  const [urlCopied, setUrlCopied] = useState(false);

  const registeredDeploymentQuery = useQuery({
    queryKey: [
      "registered-deployment-for-instance",
      tierIndex + 1,
      targetInstance?.nodename,
    ],
    queryFn: async () => {
      const nodename = targetInstance?.nodename;
      if (!nodename) return null;
      return await findRegisteredDeploymentForInstance(tierIndex + 1, nodename);
    },
    enabled: open && isUpdate && Boolean(targetInstance?.nodename),
    staleTime: 0,
  });

  const registeredDeployment = registeredDeploymentQuery.data ?? null;
  const isRegisteredUpdate = isUpdate && registeredDeployment !== null;
  const useLegacyVersions =
    !isUpdate ||
    (registeredDeploymentQuery.isSuccess && registeredDeployment === null);
  const legacyOptions = useDeployBranchOptions(open && useLegacyVersions);

  const registeredVersionQuery = useQuery({
    queryKey: [
      "registered-application-version-options",
      registeredDeployment?.application_id,
    ],
    queryFn: async () => {
      if (!registeredDeployment) return { branches: [], tags: [] };
      const detail = await fetchRegisteredAppDetail(
        registeredDeployment.application_id,
      );
      const versions = await fetchAppVersions(detail);
      return detail.type === "github"
        ? { branches: versions, tags: [] }
        : { branches: [], tags: versions };
    },
    enabled: open && Boolean(registeredDeployment),
    staleTime: 0,
  });

  const branches = registeredDeployment
    ? (registeredVersionQuery.data?.branches ?? [])
    : legacyOptions.branches;
  const tags = registeredDeployment
    ? (registeredVersionQuery.data?.tags ?? [])
    : legacyOptions.tags;
  const branchesLoading =
    (isUpdate && registeredDeploymentQuery.isLoading) ||
    (registeredDeployment
      ? registeredVersionQuery.isLoading
      : legacyOptions.branchesLoading);
  const versionQueryError =
    registeredDeploymentQuery.error ?? registeredVersionQuery.error;
  const branchesError = versionQueryError
    ? versionQueryError instanceof Error
      ? versionQueryError.message
      : "Failed to load versions"
    : legacyOptions.branchesError;

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    trigger,
    formState: { errors, isSubmitting },
  } = useForm<DeploymentFormValues>({
    mode: "onChange",
    defaultValues: {
      branch: "",
      customer_name: "",
      subdomain: "",
      env_instance: "",
    },
  });

  const {
    subdomainFieldUnlocked,
    subdomainReadOnly,
    subdomainInputRef,
    toggleSubdomainFieldLock,
    resetSubdomainFieldLock,
  } = useSubdomainFieldLock({
    isUpdate,
    initialSubdomain,
    setValue,
    trigger,
  });

  useEffect(() => {
    if (open) {
      setUrlCopied(false);
      resetSubdomainFieldLock();
      reset({
        branch: "",
        customer_name: isUpdate ? (initialValues?.customer_name ?? "") : "",
        subdomain: isUpdate ? initialSubdomain : "",
        env_instance: isUpdate ? (initialValues?.env_instance ?? "") : "",
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const branchValue = watch("branch");
  const customerNameValue = watch("customer_name");
  const envInstanceValue = watch("env_instance");

  const branchFieldReg = register("branch", {
    required: "Branch is required",
    maxLength: { value: 255, message: "Branch name must be at most 255 characters" },
    validate: validateBranchCharacters,
  });

  const {
    ref: customerNameFormRef,
    ...customerNameFieldReg
  } = register("customer_name", {
    required: isRegisteredUpdate ? false : "Customer name is required",
    maxLength: { value: 200, message: "Customer name must be at most 200 characters" },
    validate: isRegisteredUpdate
      ? () => true
      : createCustomerNameValidator(isUpdate, () => envInstanceValue),
  });

  const envInstanceFieldReg = register("env_instance", {
    required: isRegisteredUpdate ? false : "Environment / instance is required",
    maxLength: { value: 32, message: "At most 32 characters" },
    validate: isRegisteredUpdate
      ? () => true
      : createEnvInstanceValidator(isUpdate, () => customerNameValue),
  });

  const {
    ref: subdomainFormRef,
    ...subdomainFieldReg
  } = register("subdomain", {
    validate: (value) =>
      isRegisteredUpdate
        ? true
        : validateSubdomainWhenUpdate(value, isUpdate),
  });

  const deployHost =
    !isUpdate && customerNameValue.trim().length > 0 && envInstanceValue.trim().length > 0
      ? workflowPrepareSubdomain(customerNameValue, envInstanceValue)
      : "";
  const deployUrlPreview =
    !isUpdate && deployHost.length > 0
      ? deploymentPreviewUrl(deployHost, DEPLOY_DOMAIN)
      : null;

  const copyPreviewUrl = async () => {
    if (!deployUrlPreview) return;
    try {
      await navigator.clipboard.writeText(deployUrlPreview);
      setUrlCopied(true);
      window.setTimeout(() => setUrlCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };

  const onSubmit = async (values: DeploymentFormValues) => {
    setSubmitError(null);
    try {
      let deployment;
      if (isRegisteredUpdate) {
        deployment = await upgradeDeployment(
          registeredDeployment.id,
          values.branch.trim(),
        );
      } else {
        const payload = buildDeploymentPayload(values, {
          isUpdate,
          tierIndex,
          initialSubdomain,
          initialValues,
        });
        deployment = isUpdate
          ? await triggerUpdateDeployment(payload)
          : await triggerDeployment(payload);
      }
      onDeployed(deployment);
      reset();
      onOpenChange(false);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Deployment failed. Please try again.";
      setSubmitError(message);
    }
  };

  const handleOpenChange = (next: boolean) => {
    if (!isSubmitting) {
      reset();
      setSubmitError(null);
      if (!next) resetSubdomainFieldLock();
      onOpenChange(next);
    }
  };

  return {
    branches,
    tags,
    branchesLoading,
    branchesError,
    isUpdate,
    subdomainFieldUnlocked,
    subdomainReadOnly,
    errors,
    isSubmitting,
    submitError,
    urlCopied,
    branchValue,
    deployUrlPreview,
    branchFieldReg,
    customerNameFieldReg,
    customerNameFormRef,
    envInstanceFieldReg,
    subdomainFieldReg,
    subdomainFormRef,
    subdomainInputRef,
    handleSubmit: handleSubmit(onSubmit),
    setValue,
    copyPreviewUrl,
    handleOpenChange,
    toggleSubdomainFieldLock,
  };
}

export type NewDeploymentFormViewModel = ReturnType<typeof useNewDeploymentForm>;
