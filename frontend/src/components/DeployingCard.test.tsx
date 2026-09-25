import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { basePipelineStatus } from "@/test-utils/pipeline";
import { appsColors } from "@styles/appsColors";
import type { PipelineStatusRecord } from "@/types/deployment";

import DeployingCard from "./DeployingCard";

const failed: PipelineStatusRecord = {
  ...basePipelineStatus,
  gh_status: "completed",
  gh_conclusion: "failure",
  ended_at: "2026-04-01T12:05:00+00:00",
};

describe("DeployingCard", () => {
  it("lets a failed deployment be removed from the page", () => {
    const onDismiss = vi.fn();
    render(<DeployingCard deployment={failed} appStyle={appsColors[0]} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss failed deployment" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("offers no dismissal while the deployment is running", () => {
    render(
      <DeployingCard deployment={basePipelineStatus} appStyle={appsColors[0]} onDismiss={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Dismiss failed deployment" })).toBeNull();
  });

  it("is titled with the registered application, else the customer", () => {
    const { rerender } = render(
      <DeployingCard
        deployment={{ ...basePipelineStatus, application_name: "Billing Sync" }}
        appStyle={appsColors[0]}
      />,
    );
    expect(screen.getByRole("heading", { name: "Billing Sync" })).toBeTruthy();

    rerender(<DeployingCard deployment={basePipelineStatus} appStyle={appsColors[0]} />);
    expect(screen.getByRole("heading", { name: "acme" })).toBeTruthy();
  });
});
