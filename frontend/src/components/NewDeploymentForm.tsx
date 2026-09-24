import {
  Check,
  CircleHelp,
  Copy,
  Loader2,
  Rocket,
  RefreshCw,
} from "lucide-react";

import { Button } from "@ui/button";
import { Input } from "@ui/input";
import { Label } from "@ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@ui/tooltip";
import {
  DialogFooter,
} from "@ui/dialog";
import type { NewDeploymentFormViewModel } from "@hooks/useNewDeploymentForm";
import { cn } from "@/lib/utils";
import BranchCombobox from "./BranchCombobox";
import { DEPLOY_DOMAIN } from "@/constants/deployment";
import type { AppStyle } from "@styles/appsColors";

type Props = {
  vm: NewDeploymentFormViewModel;
  appStyle?: AppStyle;
};

export default function NewDeploymentForm({ vm, appStyle }: Props) {
  const {
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
    handleSubmit,
    setValue,
    copyPreviewUrl,
    handleOpenChange,
    toggleSubdomainFieldLock,
    branches,
    tags,
    branchesLoading,
    branchesError,
  } = vm;

  return (
    <form onSubmit={handleSubmit} className="space-y-4 pt-2">
      {!isUpdate && (
        <div className="space-y-1.5">
          <Label>Product Name</Label>
          <p className="font-bold text-xl">Amberd</p>
        </div>
      )}
      <div className={`space-y-1.5 ${isUpdate ? "pt-4" : ""}`}>
        <Label htmlFor="branch">{isUpdate ? "New Version" : "Version"}</Label>
        <BranchCombobox
          value={branchValue}
          onChange={(val) => setValue("branch", val, { shouldValidate: true })}
          branches={branches}
          tags={tags}
          loading={branchesLoading}
          error={branchesError ?? undefined}
        />
        {errors.branch && (
          <p className="text-xs text-critical-text">{errors.branch.message}</p>
        )}
        <input type="hidden" {...branchFieldReg} />
      </div>

      {isUpdate ? (
        <>
          <input type="hidden" {...subdomainFieldReg} ref={subdomainFormRef} />
          <input type="hidden" {...customerNameFieldReg} ref={customerNameFormRef} />
          <input type="hidden" {...envInstanceFieldReg} />
        </>
      ) : (
        <>
          <div className="space-y-1.5">
            <Label htmlFor="customer_name">Client Name</Label>
            <Input
              id="customer_name"
              placeholder="e.g. Procon"
              {...customerNameFieldReg}
              ref={customerNameFormRef}
            />
            {errors.customer_name && (
              <p className="text-xs text-critical-text">
                {errors.customer_name.message}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="env_instance">Instance Name</Label>
            <Input
              id="env_instance"
              placeholder="e.g. prod, qa, etc."
              className="font-mono text-sm"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              {...envInstanceFieldReg}
            />
            {errors.env_instance && (
              <p className="text-xs text-critical-text">
                {errors.env_instance.message}
              </p>
            )}
          </div>

          <div className="rounded-md border border-border bg-muted/40 px-3 py-2 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-1">
                <p className="text-xs font-medium text-foreground">
                  Preview deployment URL
                </p>
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        className="inline-flex shrink-0 rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label="How this preview is computed"
                      >
                        <CircleHelp className="h-3.5 w-3.5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="max-w-[260px] leading-snug">
                      Preview is amberd-{"{customer}"}-{"{env}"} from the fields above; the API
                      checks the same shape on deploy.{" "}
                      <span className="font-mono">{DEPLOY_DOMAIN}</span> is only for this preview.
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              {deployUrlPreview && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 shrink-0 gap-1 px-2 text-xs"
                  onClick={() => void copyPreviewUrl()}
                >
                  {urlCopied ? (
                    <>
                      <Check className="h-3.5 w-3.5" />
                      Copied
                    </>
                  ) : (
                    <>
                      <Copy className="h-3.5 w-3.5" />
                      Copy
                    </>
                  )}
                </Button>
              )}
            </div>
            {deployUrlPreview ? (
              <p className="text-xs text-muted-foreground break-all font-mono">
                {deployUrlPreview}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Enter a customer name to preview the URL.
              </p>
            )}
          </div>
        </>
      )}

      {submitError && (
        <p className="rounded-sm border border-critical-text/30 bg-critical/20 px-3 py-2 text-xs text-critical-text">
          {submitError}
        </p>
      )}

      <DialogFooter className="pt-2">
        <Button
          type="button"
          variant="secondary"
          onClick={() => handleOpenChange(false)}
          disabled={isSubmitting}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          disabled={isSubmitting}
          style={appStyle ? { backgroundColor: appStyle.border, borderColor: appStyle.border } : undefined}
          className={appStyle ? "text-white hover:opacity-90" : ""}
        >
          {isSubmitting ? (
            <>
              <Loader2 className="animate-spin" />
              {isUpdate ? "Redeploying…" : "Deploying…"}
            </>
          ) : isUpdate ? (
            <>
              <RefreshCw />
              Trigger Update
            </>
          ) : (
            <>
              <Rocket />
              Trigger Deployment
            </>
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
