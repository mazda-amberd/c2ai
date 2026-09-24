import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ui/tooltip";
import {
  COST_UNAVAILABLE_HINT,
  COST_UNAVAILABLE_LABEL,
  formatCost,
  isCostAvailable,
  type CostValue,
} from "@/utils/financeApi";

type Props = {
  /** Resolved cost, or null while loading. */
  cost: CostValue | null;
  className?: string;
  /** Class applied only when the cost couldn't be calculated (muted look). */
  unavailableClassName?: string;
};

/** Formats a cost figure; renders "Not available" with an explanatory
 *  tooltip when the backend couldn't calculate it, and "…" while loading. */
export default function CostAmount({ cost, className, unavailableClassName }: Props) {
  if (cost === null) return <span className={className}>…</span>;
  if (isCostAvailable(cost)) return <span className={className}>{formatCost(cost)}</span>;

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            tabIndex={0}
            aria-label={`${COST_UNAVAILABLE_LABEL} — ${COST_UNAVAILABLE_HINT}`}
            className={[className, unavailableClassName, "cursor-help"].filter(Boolean).join(" ")}
          >
            {COST_UNAVAILABLE_LABEL}
          </span>
        </TooltipTrigger>
        <TooltipContent>{COST_UNAVAILABLE_HINT}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
