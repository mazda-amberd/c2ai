import { Check, CircleHelp, X } from "lucide-react";

import type { ApiRegistrationCheck } from "@api/services/registeredApplications";

/** The outcome of each check made while registering: passed, failed, or
 *  could not be told. */
export default function CheckList({ checks }: { checks: ApiRegistrationCheck[] }) {
  return (
    <ul aria-label="Check results" className="space-y-1.5 rounded-[8px] border border-[#1c2836] p-3">
      {checks.map((check) => (
        <li key={check.name} className="flex items-start gap-2 text-[12px] leading-snug">
          {check.ok === true && <Check aria-label="passed" className="mt-px h-3.5 w-3.5 shrink-0 text-[#4ade80]" />}
          {check.ok === false && <X aria-label="failed" className="mt-px h-3.5 w-3.5 shrink-0 text-[#f0655f]" />}
          {check.ok === null && (
            <CircleHelp aria-label="unknown" className="mt-px h-3.5 w-3.5 shrink-0 text-[#fbbf24]" />
          )}
          <span>
            <strong className="text-[#eef2f6]">{check.name}</strong>{" "}
            <span className="text-[#8b97a5]">— {check.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
