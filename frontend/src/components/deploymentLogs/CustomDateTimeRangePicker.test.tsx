import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CustomDateTimeRangePicker } from "./CustomDateTimeRangePicker";

const NOW = new Date(2026, 8, 15, 18, 30);

function renderPicker(from: Date, to: Date) {
  const onFromChange = vi.fn();
  const onToChange = vi.fn();
  render(
    <CustomDateTimeRangePicker
      from={from}
      to={to}
      onFromChange={onFromChange}
      onToChange={onToChange}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Custom log time range" }));
  return { onFromChange, onToChange };
}

describe("CustomDateTimeRangePicker", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("disables every day after today", () => {
    renderPicker(new Date(2026, 8, 15, 17, 30), NOW);
    const day = (label: RegExp) => screen.getByRole("button", { name: label });
    expect(day(/September 15, 2026/).getAttribute("aria-disabled")).toBeNull();
    expect(day(/September 16, 2026/).getAttribute("aria-disabled")).toBe("true");
  });

  it("caps a time later today at now", () => {
    const { onToChange } = renderPicker(new Date(2026, 8, 15, 17, 30), NOW);
    fireEvent.change(screen.getByLabelText("Range end time"), { target: { value: "23:00" } });
    expect(onToChange).toHaveBeenLastCalledWith(NOW);

    fireEvent.change(screen.getByLabelText("Range end time"), { target: { value: "18:00" } });
    expect(onToChange).toHaveBeenLastCalledWith(new Date(2026, 8, 15, 18, 0));
  });

  it("caps the Today shortcut at now", () => {
    // The range's times are kept when a shortcut picks the days.
    const { onFromChange, onToChange } = renderPicker(
      new Date(2026, 8, 10, 9, 0),
      new Date(2026, 8, 10, 23, 0),
    );
    fireEvent.click(screen.getByRole("button", { name: "Today" }));
    expect(onFromChange).toHaveBeenLastCalledWith(new Date(2026, 8, 15, 9, 0));
    expect(onToChange).toHaveBeenLastCalledWith(NOW);
  });
});
