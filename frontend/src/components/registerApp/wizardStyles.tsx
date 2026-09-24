import { forwardRef } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

/* Shared visual language for the registration/deployment popup wizards —
 * literal colors from the Registered Applications Catalog template, kept in
 * one place so every wizard-style modal stays visually identical. */

export const ACCENT = "#20abc7";

export const FIELD_CLASS =
  "h-9 w-full rounded-[6px] border border-[#16202c] bg-[rgba(3,14,25,0.87)] px-3 text-[12px] text-[#eaf0f7] placeholder:text-[#57606c] outline-none transition-colors focus:border-[#20abc7] focus:ring-2 focus:ring-[rgba(32,171,199,0.12)]";

/** Native <select>, styled to hide the browser's own arrow (which otherwise
 *  sits flush against the right edge and reads as overflow) and replaced
 *  with a chevron that has proper breathing room, matching the template's
 *  dropdown treatment. Forwards its ref to the underlying <select> so
 *  react-hook-form's register() can attach directly. */
export const Select = forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, children, ...props }, ref) {
  return (
    <div className={cn("relative", className)}>
      <select
        ref={ref}
        className={cn(FIELD_CLASS, "appearance-none pr-8", className)}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[#8b97a5]" />
    </div>
  );
});

export const LABEL_CLASS =
  "flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-[#8b97a5]";

export const Required = () => <span className="text-[#f0655f]">*</span>;
export const Optional = () => <span className="normal-case text-[#57606c]">(optional)</span>;

export function Field({
  label,
  required,
  optional,
  helper,
  children,
}: {
  label: string;
  required?: boolean;
  optional?: boolean;
  helper?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className={LABEL_CLASS}>
        {label} {required && <Required />} {optional && <Optional />}
      </label>
      {children}
      {helper && <p className="text-[11px] text-[#57606c]">{helper}</p>}
    </div>
  );
}

/** Dialog panel background — same dark gradient every wizard modal uses. */
export const PANEL_BACKGROUND =
  "linear-gradient(150deg, rgba(14,32,52,.97), rgba(5,17,31,1) 72%)";

/** The cyan "context note" callout — e.g. "Deploying into Tier 1 — the
 *  target tier and namespace are set automatically from this context." */
export function ContextNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-2.5 rounded-[8px] border border-[rgba(32,171,199,0.4)] bg-[rgba(32,171,199,0.08)] p-3 text-[12.5px] text-[#8ff2ef]">
      <span>📍</span>
      <span>{children}</span>
    </div>
  );
}
