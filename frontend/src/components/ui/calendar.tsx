import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  Button as AriaButton,
  CalendarCell,
  CalendarGrid,
  CalendarGridBody,
  CalendarGridHeader,
  CalendarHeaderCell,
  Heading,
  RangeCalendar,
} from "react-aria-components";
import { CalendarDate, getLocalTimeZone } from "@internationalized/date";
import type { RangeValue, DateValue } from "react-aria-components";

import { cn } from "@/lib/utils";

export type DateRange = {
  from?: Date;
  to?: Date;
};

export type CalendarProps = {
  className?: string;
  style?: React.CSSProperties;
  value?: DateRange;
  onChange?: (range: DateRange | undefined) => void;
  /** Last selectable day; later days are disabled. */
  maxDate?: Date;
};

function toCalendarDate(date: Date): CalendarDate {
  return new CalendarDate(
    date.getFullYear(),
    date.getMonth() + 1,
    date.getDate(),
  );
}

const navBtnClass = cn(
  "flex h-7 w-7 items-center justify-center rounded-md text-foreground/70",
  "hover:bg-accent hover:text-foreground",
  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-border-secondary",
  "disabled:pointer-events-none disabled:opacity-30",
  "transition-colors",
);

/** Inline date-range calendar backed by react-aria-components RangeCalendar. */
function Calendar({ className, style, value, onChange, maxDate }: CalendarProps) {
  const ariaValue: RangeValue<CalendarDate> | null =
    value?.from && value?.to
      ? {
          start: toCalendarDate(value.from),
          end: toCalendarDate(value.to),
        }
      : null;

  return (
    <RangeCalendar
      aria-label="Date range"
      value={ariaValue}
      maxValue={maxDate ? toCalendarDate(maxDate) : undefined}
      onChange={(range: RangeValue<DateValue> | null) => {
        if (!range) {
          onChange?.(undefined);
          return;
        }
        onChange?.({
          from: range.start.toDate(getLocalTimeZone()),
          to: range.end.toDate(getLocalTimeZone()),
        });
      }}
      className={cn(
        "rounded-lg border border-border bg-header p-3 text-foreground",
        className,
      )}
      style={style}
    >
      <header className="mb-3 flex items-center px-1">
        <AriaButton slot="previous" className={navBtnClass}>
          <ChevronLeft className="h-4 w-4" />
        </AriaButton>
        <Heading className="flex-1 text-center text-sm font-semibold text-foreground" />
        <AriaButton slot="next" className={navBtnClass}>
          <ChevronRight className="h-4 w-4" />
        </AriaButton>
      </header>

      <CalendarGrid className="border-collapse [&_td]:p-0 [&_th]:p-0">
        <CalendarGridHeader>
          {(day) => (
            <CalendarHeaderCell className="h-9 w-9 text-center text-[11px] font-medium uppercase text-foreground/55">
              {day}
            </CalendarHeaderCell>
          )}
        </CalendarGridHeader>
        <CalendarGridBody>
          {(date) => (
            <CalendarCell date={date} className="outline-none">
              {({
                formattedDate,
                isSelected,
                isSelectionStart,
                isSelectionEnd,
                isDisabled,
                isFocusVisible,
                isOutsideVisibleRange,
                isUnavailable,
              }) => (
                <div
                  className={cn(
                    "relative flex h-9 w-9 select-none items-center justify-center text-[13px]",
                    // Outside month / unavailable / disabled
                    isOutsideVisibleRange && "opacity-40",
                    (isDisabled || isUnavailable) && "opacity-30",
                    // ── Range middle ──────────────────────────────────────
                    isSelected &&
                      !isSelectionStart &&
                      !isSelectionEnd && [
                        "border-y border-border-secondary bg-button-secondary/25 text-text-secondary/90",
                        "shadow-shadow-secondary",
                      ],
                    // ── Start cap ─────────────────────────────────────────
                    isSelectionStart &&
                      !isSelectionEnd && [
                        "rounded-l-md border-y border-l border-border-secondary bg-button-secondary/45 font-semibold text-foreground",
                        "shadow-shadow-secondary",
                      ],
                    // ── End cap ───────────────────────────────────────────
                    !isSelectionStart &&
                      isSelectionEnd && [
                        "rounded-r-md border-y border-r border-border-secondary bg-button-secondary/45 font-semibold text-foreground",
                        "shadow-shadow-secondary",
                      ],
                    // ── Single-day selection (start === end) ──────────────
                    isSelectionStart &&
                      isSelectionEnd && [
                        "rounded-md border border-border-secondary bg-button-secondary/45 font-semibold text-foreground",
                        "shadow-shadow-secondary",
                      ],
                    // ── Hover (unselected only) ───────────────────────────
                    !isSelected &&
                      !isDisabled &&
                      "cursor-pointer rounded-md hover:bg-accent",
                    // ── Keyboard focus ring ───────────────────────────────
                    isFocusVisible &&
                      "z-10 ring-1 ring-border-secondary ring-offset-1 ring-offset-background",
                  )}
                >
                  {formattedDate}
                </div>
              )}
            </CalendarCell>
          )}
        </CalendarGridBody>
      </CalendarGrid>
    </RangeCalendar>
  );
}
Calendar.displayName = "Calendar";

export { Calendar };
