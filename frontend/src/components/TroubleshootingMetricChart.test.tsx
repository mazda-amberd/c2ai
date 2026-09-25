import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TroubleshootingMetricChart from "./TroubleshootingMetricChart";

const metric = (points?: { timestamp: string; value: number }[]) => ({
  name: "cpu_usage",
  label: "CPU usage",
  value: 1,
  unit: "cores",
  plot_data_url: null,
  points,
});

describe("TroubleshootingMetricChart", () => {
  it("says so when the report has no samples", () => {
    render(<TroubleshootingMetricChart metric={metric([])} />);
    expect(screen.getByText("No metric data available")).toBeTruthy();
  });

  it("labels the window it charts", () => {
    const start = "2026-09-24T10:00:00Z";
    const end = "2026-09-24T14:00:00Z";
    render(
      <TroubleshootingMetricChart
        metric={metric([
          { timestamp: start, value: 0.5 },
          { timestamp: end, value: 1 },
        ])}
      />,
    );
    const clock = (iso: string) =>
      new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    expect(screen.getByText(clock(start))).toBeTruthy();
    expect(screen.getByText(clock(end))).toBeTruthy();
    expect(screen.queryByText("No metric data available")).toBeNull();
  });
});
