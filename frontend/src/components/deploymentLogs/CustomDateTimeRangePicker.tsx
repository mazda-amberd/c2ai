import { format, subDays } from "date-fns";
import { Clock } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@ui/button";
import { Calendar, type DateRange } from "@ui/calendar";
import { Input } from "@ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@ui/popover";
import { cn } from "@/lib/utils";

function applyCalendarDay(base: Date, day: Date): Date {
  const d = new Date(base);
  d.setFullYear(day.getFullYear(), day.getMonth(), day.getDate());
  return d;
}

function applyTimeHHmm(base: Date, hhmm: string): Date {
  const [hs, ms] = hhmm.split(":");
  const h = Number(hs);
  const m = Number(ms);
  const d = new Date(base);
  if (!Number.isNaN(h)) d.setHours(h);
  if (!Number.isNaN(m)) d.setMinutes(m);
  d.setSeconds(0, 0);
  return d;
}

const timeInputClass = cn(
  "h-7 min-w-0 flex-1 border-0 bg-transparent p-0 font-mono text-[11px] text-foreground/90 shadow-none",
  "relative accent-white",
  "focus-visible:ring-0",
  "[&::-webkit-calendar-picker-indicator]:absolute [&::-webkit-calendar-picker-indicator]:inset-y-0 [&::-webkit-calendar-picker-indicator]:right-0",
  "[&::-webkit-calendar-picker-indicator]:m-0 [&::-webkit-calendar-picker-indicator]:h-full [&::-webkit-calendar-picker-indicator]:w-6",
  "[&::-webkit-calendar-picker-indicator]:cursor-pointer [&::-webkit-calendar-picker-indicator]:bg-transparent",
  "[&::-webkit-calendar-picker-indicator]:opacity-0",
);

export type CustomDateTimeRangePickerProps = {
  from: Date;
  to: Date;
  onFromChange: (next: Date) => void;
  onToChange: (next: Date) => void;
  onBeforeUserEdit?: () => void;
};

export function CustomDateTimeRangePicker({
  from,
  to,
  onFromChange,
  onToChange,
  onBeforeUserEdit,
}: CustomDateTimeRangePickerProps) {
  const bump = () => onBeforeUserEdit?.();
  const fromRef = useRef(from);
  const toRef = useRef(to);

  useEffect(() => {
    fromRef.current = from;
    toRef.current = to;
  }, [from, to]);

  const [rangeDraft, setRangeDraft] = useState<DateRange | undefined>(() => ({
    from,
    to,
  }));

  const resetDraftFromProps = useCallback(() => {
    setRangeDraft({ from: fromRef.current, to: toRef.current });
  }, []);

  const quickDays: { label: string; resolveRange: () => { from: Date; to: Date } }[] = [
    { label: "Today", resolveRange: () => { const d = new Date(); return { from: d, to: d }; } },
    { label: "Yesterday", resolveRange: () => { const d = subDays(new Date(), 1); return { from: d, to: d }; } },
    { label: "3 days ago", resolveRange: () => { const d = subDays(new Date(), 3); return { from: d, to: d }; } },
    { label: "Last week", resolveRange: () => ({ from: subDays(new Date(), 7), to: new Date() }) },
    { label: "Last 2 weeks", resolveRange: () => ({ from: subDays(new Date(), 14), to: new Date() }) },
  ];

  const applyQuickRange = ({ from, to }: { from: Date; to: Date }) => {
    bump();
    const nextFrom = applyCalendarDay(fromRef.current, from);
    const nextTo = applyCalendarDay(toRef.current, to);
    onFromChange(nextFrom);
    onToChange(nextTo);
    setRangeDraft({ from: nextFrom, to: nextTo });
  };

  return (
    <Popover
      onOpenChange={(open) => {
        if (open) resetDraftFromProps();
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          aria-label="Custom log time range"
          className="h-8 min-w-[12rem] max-w-full justify-start gap-2 font-mono text-[11px] lg:min-w-[20rem]"
        >
          <span className="min-w-0 truncate">
            {format(from, "MMM d, yyyy h:mm a")}
            <span className="text-muted-foreground/80"> — </span>
            {format(to, "MMM d, yyyy h:mm a")}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-auto rounded-xl border border-popover-border bg-popover p-3 shadow-xl"
      >
        <p className="mb-2 text-[10px] text-muted-foreground">
          First click: start · second click: end
        </p>
        <Calendar
          value={rangeDraft}
          onChange={(range) => {
            bump();
            if (!range?.from || !range?.to) {
              setRangeDraft(undefined);
              return;
            }
            setRangeDraft(range);
            onFromChange(applyCalendarDay(fromRef.current, range.from));
            onToChange(applyCalendarDay(toRef.current, range.to));
          }}
        />
        <div className="mt-2 grid grid-cols-6 gap-1.5">
          {quickDays.map((q, i) => (
            <Button
              key={q.label}
              type="button"
              variant="ghost"
              size="sm"
              className={cn(
                "h-7 border border-popover-border bg-input px-1.5 text-[10px] font-medium",
                "text-foreground/90 shadow-none hover:bg-accent hover:text-foreground/90",
                i < 3 ? "col-span-2" : "col-span-3",
              )}
              onClick={() => applyQuickRange(q.resolveRange())}
            >
              {q.label}
            </Button>
          ))}
        </div>
        <div className="mt-3 space-y-2 border-t border-popover-border pt-3">
          <div
            className={cn(
              "flex items-center gap-2 rounded-md border border-popover-border bg-input px-2 py-1.5",
              "[color-scheme:dark]",
            )}
          >
            <span className="w-9 shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Start
            </span>
            <Clock
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="time"
              aria-label="Range start time"
              value={format(from, "HH:mm")}
              onChange={(e) => {
                bump();
                onFromChange(applyTimeHHmm(from, e.target.value));
              }}
              className={timeInputClass}
            />
          </div>
          <div
            className={cn(
              "flex items-center gap-2 rounded-md border border-popover-border bg-input px-2 py-1.5",
              "[color-scheme:dark]",
            )}
          >
            <span className="w-9 shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              End
            </span>
            <Clock
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="time"
              aria-label="Range end time"
              value={format(to, "HH:mm")}
              onChange={(e) => {
                bump();
                onToChange(applyTimeHHmm(to, e.target.value));
              }}
              className={timeInputClass}
            />
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
