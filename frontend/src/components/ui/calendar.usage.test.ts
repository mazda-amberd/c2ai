import { describe, expect, it } from "vitest";

/* Every calendar in the app behaves the same way (two months, no dates after
 * today) because every one is the shared Calendar with a maxDate. These
 * checks keep a new screen from adding one that isn't. */

const sources = Object.entries(
  import.meta.glob<string>("/src/**/*.tsx", { query: "?raw", import: "default", eager: true }),
).filter(([path]) => !path.includes(".test."));

const SHARED = "/src/components/ui/calendar.tsx";

describe("calendar usage", () => {
  it("builds every calendar from the shared component", () => {
    const direct = sources
      .filter(([path]) => path !== SHARED)
      .filter(([, code]) =>
        /\b(RangeCalendar|DateRangePicker|DatePicker|CalendarGrid)\b/.test(code) ||
        /from "react-day-picker"|from "react-datepicker"/.test(code),
      )
      .map(([path]) => path);
    expect(direct).toEqual([]);
  });

  it("limits every calendar to today", () => {
    const users = sources.filter(([, code]) => code.includes('from "@ui/calendar"'));
    expect(users.map(([path]) => path).sort()).toEqual(
      expect.arrayContaining([
        "/src/components/CostChip.tsx",
        "/src/components/deploymentLogs/CustomDateTimeRangePicker.tsx",
      ]),
    );
    const unlimited = users
      .filter(([, code]) => {
        const uses = code.match(/<Calendar\b[^>]*?>/gs) ?? [];
        return uses.length === 0 || uses.some((tag) => !tag.includes("maxDate="));
      })
      .map(([path]) => path);
    expect(unlimited).toEqual([]);
  });

  it("caps every typed date input", () => {
    const uncapped = sources
      .flatMap(([path, code]) =>
        (code.match(/<input\b[^>]*type="date"[^>]*>/gs) ?? [])
          .filter((tag) => !tag.includes("max="))
          .map(() => path),
      );
    expect(uncapped).toEqual([]);
  });
});
