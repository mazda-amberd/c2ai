import { useCallback, useRef, useState } from "react";
import type { UseFormSetValue, UseFormTrigger } from "react-hook-form";

import { splitWorkflowHostLabel } from "@/utils/subdomain";

import type { DeploymentFormValues } from "./types";

type Options = {
  isUpdate: boolean;
  initialSubdomain: string;
  setValue: UseFormSetValue<DeploymentFormValues>;
  trigger: UseFormTrigger<DeploymentFormValues>;
};

export function useSubdomainFieldLock({
  isUpdate,
  initialSubdomain,
  setValue,
  trigger,
}: Options) {
  const [unlocked, setUnlocked] = useState(false);
  const subdomainInputRef = useRef<HTMLInputElement | null>(null);

  const toggleSubdomainFieldLock = useCallback(() => {
    if (!isUpdate) return;
    if (unlocked) {
      setUnlocked(false);
      setValue("subdomain", initialSubdomain, { shouldValidate: true });
      setValue(
        "env_instance",
        splitWorkflowHostLabel(initialSubdomain).env,
        { shouldValidate: true },
      );
    } else {
      setUnlocked(true);
      window.setTimeout(() => {
        void trigger("subdomain");
        subdomainInputRef.current?.focus();
      }, 0);
    }
  }, [isUpdate, unlocked, initialSubdomain, setValue, trigger]);

  const resetSubdomainFieldLock = useCallback(() => {
    setUnlocked(false);
  }, []);

  return {
    subdomainFieldUnlocked: unlocked,
    subdomainReadOnly: isUpdate && !unlocked,
    subdomainInputRef,
    toggleSubdomainFieldLock,
    resetSubdomainFieldLock,
  };
}
