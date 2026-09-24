import {
  isValidWorkflowHostLabel,
  workflowPrepareSubdomain,
} from "@/utils/subdomain";

const BRANCH_CHAR_RE = /^[^\x00-\x1f\x7f\s]+$/;

export function validateBranchCharacters(value: string): true | string {
  return BRANCH_CHAR_RE.test(value.trim())
    ? true
    : "Branch name contains invalid characters";
}

export function createCustomerNameValidator(
  isUpdate: boolean,
  getEnvInstance: () => string,
): (value: string) => true | string {
  return (value: string) => {
    if (isUpdate) return true;
    if (!getEnvInstance().trim()) return true;
    const label = workflowPrepareSubdomain(value, getEnvInstance());
    return isValidWorkflowHostLabel(label)
      ? true
      : "Customer and environment together produce an invalid host label";
  };
}

export function createEnvInstanceValidator(
  isUpdate: boolean,
  getCustomerName: () => string,
): (value: string) => true | string {
  return (value: string) => {
    if (isUpdate) {
      const t = value.trim().toLowerCase();
      if (!t) {
        return "Host label must include an environment suffix (e.g. amberd-customer-prod)";
      }
      if (!/^[a-z0-9][a-z0-9-]*$/.test(t)) {
        return "Environment segment in the host label is invalid";
      }
      return true;
    }
    const t = value.trim().toLowerCase();
    if (!t) return "Environment / instance is required";
    if (!/^[a-z0-9][a-z0-9-]*$/.test(t)) {
      return "Use lowercase letters, digits, and hyphens (e.g. ada or ada-1)";
    }
    if (getCustomerName().trim()) {
      const label = workflowPrepareSubdomain(getCustomerName(), value);
      return isValidWorkflowHostLabel(label)
        ? true
        : "Customer and environment together produce an invalid host label";
    }
    return true;
  };
}

export function validateSubdomainWhenUpdate(value: string, isUpdate: boolean): true | string {
  if (!isUpdate) return true;
  const trimmed = value.trim();
  if (!trimmed) return "Subdomain is required";
  return isValidWorkflowHostLabel(trimmed)
    ? true
    : "Subdomain must be 3-63 lowercase alphanumeric characters or hyphens, and must start and end with an alphanumeric character";
}
