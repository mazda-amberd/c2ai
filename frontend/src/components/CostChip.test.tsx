import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  calendarDay as day,
  neighbouringDaysAreHidden,
  nextMonth,
  previousMonth,
} from "@/test-utils/calendar";
import { getFinanceFilter } from "@/utils/financeApi";

import CostChip from "./CostChip";

// Mid-month, late evening: UTC is already the next day in US time zones.
const NOW = new Date(2026, 8, 15, 23, 30);

function openCalendar() {
  render(<CostChip cost={1234} />);
  fireEvent.click(screen.getByRole("button", { name: "Select cost date range" }));
}

describe("CostChip date range", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows this month and the last, and disables every day after today", () => {
    openCalendar();
    expect(day("August 17, 2026").getAttribute("aria-disabled")).toBeNull();
    expect(day("September 15, 2026").getAttribute("aria-disabled")).toBeNull();
    expect(day("September 16, 2026").getAttribute("aria-disabled")).toBe("true");
    expect(day("September 30, 2026").getAttribute("aria-disabled")).toBe("true");
    // Nothing to pick in October, so the calendar doesn't page there.
    expect(nextMonth().hasAttribute("disabled")).toBe(true);
    expect(neighbouringDaysAreHidden()).toBe(true);
  });

  it("selects a range across months in two clicks", () => {
    openCalendar();
    fireEvent.click(day("August 20, 2026"));
    fireEvent.click(day("September 3, 2026"));
    expect((screen.getByLabelText("Start date") as HTMLInputElement).value).toBe("2026-08-20");
    expect((screen.getByLabelText("End date") as HTMLInputElement).value).toBe("2026-09-03");
  });

  it("reaches earlier months one at a time", () => {
    openCalendar();
    fireEvent.click(previousMonth());
    fireEvent.click(day("July 20, 2026"));
    fireEvent.click(nextMonth());
    fireEvent.click(day("September 3, 2026"));
    expect((screen.getByLabelText("Start date") as HTMLInputElement).value).toBe("2026-07-20");
    expect((screen.getByLabelText("End date") as HTMLInputElement).value).toBe("2026-09-03");
  });

  it("caps the typed dates at today", () => {
    openCalendar();
    const end = screen.getByLabelText("End date") as HTMLInputElement;
    expect(end.max).toBe("2026-09-15");
    expect(end.value).toBe("2026-09-15"); // default range ends today, in local time

    fireEvent.change(end, { target: { value: "2026-12-31" } });
    expect(end.value).toBe("2026-09-15");

    const start = screen.getByLabelText("Start date") as HTMLInputElement;
    fireEvent.change(start, { target: { value: "2027-01-01" } });
    expect(start.value).toBe("2026-09-15");
  });

  it("clamps a saved range that ends in the future", () => {
    window.localStorage.setItem(
      "athena-finance-filter",
      JSON.stringify({ start: "2026-09-01", end: "2026-09-20" }),
    );
    expect(getFinanceFilter()).toEqual({ start: "2026-09-01", end: "2026-09-15" });
  });
});
