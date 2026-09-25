import { useState } from "react";
import { CalendarDays } from "lucide-react";

import { Button } from "@ui/button";
import { Calendar, type DateRange } from "@ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@ui/popover";
import CostAmount from "@components/CostAmount";
import {
  fromLocalIsoDate,
  isCostAvailable,
  toLocalIsoDate as toIso,
  useFinanceFilter,
  type CostValue,
  type FinanceFilter,
} from "@/utils/financeApi";

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** Costs only exist up to today, so no date may be later than it. */
function notAfter(date: Date | undefined, today: Date): Date | undefined {
  return date && date > today ? today : date;
}

function toRange(filter: FinanceFilter, today: Date): DateRange {
  return {
    from: notAfter(fromLocalIsoDate(filter.start), today),
    to: notAfter(fromLocalIsoDate(filter.end), today),
  };
}

function earlier(a: Date | undefined, b: Date): Date {
  return a && a < b ? a : b;
}

type Props = {
  /** Cost for the active date range; null while loading, or the
   *  "unavailable" marker when it couldn't be calculated. */
  cost: CostValue | null;
};

/** Yellow total-cost indicator with a calendar popover that sets the shared
 *  start/end date filter (Epic 10 — Financial Tracking). */
export default function CostChip({ cost }: Props) {
  const [filter, setFilter] = useFinanceFilter();
  const [open, setOpen] = useState(false);
  const [today, setToday] = useState(startOfToday);
  const [draft, setDraft] = useState<DateRange>(() => toRange(filter, today));

  const handleOpenChange = (next: boolean) => {
    if (next) {
      // The page may have stayed open past midnight.
      const now = startOfToday();
      setToday(now);
      setDraft(toRange(filter, now));
    }
    setOpen(next);
  };

  const typedDate = (value: string) => (value ? notAfter(fromLocalIsoDate(value), today) : undefined);

  const applyDisabled = !draft.from || !draft.to;

  const handleApply = () => {
    if (!draft.from || !draft.to) return;
    setFilter({ start: toIso(draft.from), end: toIso(draft.to) });
    setOpen(false);
  };

  // Muted (grey) chip when the cost couldn't be calculated, so it doesn't
  // read as a real figure.
  const unavailable = cost !== null && !isCostAvailable(cost);

  return (
    <div
      className={`flex h-9 items-center gap-2 rounded-md border py-2 pl-3.5 pr-1.5 ${
        unavailable
          ? "border-[#57606c]/50 bg-white/[0.04]"
          : "border-[#fbbf24]/40 bg-[rgba(251,191,36,0.08)]"
      }`}
    >
      <CostAmount
        cost={cost}
        className="text-base font-bold text-[#fbbf24]"
        unavailableClassName="text-sm font-semibold text-[#8b97a5]"
      />
      <Popover open={open} onOpenChange={handleOpenChange}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label="Select cost date range"
            className="flex h-7 w-7 items-center justify-center rounded-md text-[#fbbf24]/80 transition-colors hover:bg-[rgba(251,191,36,0.15)] hover:text-[#fbbf24]"
          >
            <CalendarDays className="h-4 w-4" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="p-4">
          <div className="space-y-3">
            {/* Editable inputs mirroring the calendar selection — typing a
                date here moves the calendar, and picking on the calendar
                fills these in. */}
            <div className="flex items-center gap-2">
              <input
                type="date"
                aria-label="Start date"
                value={draft.from ? toIso(draft.from) : ""}
                max={toIso(earlier(draft.to, today))}
                onChange={(e) => setDraft({ ...draft, from: typedDate(e.target.value) })}
                className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm [color-scheme:dark]"
              />
              <span className="text-muted-foreground">–</span>
              <input
                type="date"
                aria-label="End date"
                value={draft.to ? toIso(draft.to) : ""}
                min={draft.from ? toIso(draft.from) : undefined}
                max={toIso(today)}
                onChange={(e) => setDraft({ ...draft, to: typedDate(e.target.value) })}
                className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm [color-scheme:dark]"
              />
            </div>
            {/* One range calendar: click a start day, then an end day. */}
            <Calendar
              value={draft}
              maxDate={today}
              onChange={(range) => setDraft(range ?? {})}
            />
            <Button
              className="w-full"
              variant="secondary"
              disabled={applyDisabled}
              onClick={handleApply}
            >
              Apply
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
