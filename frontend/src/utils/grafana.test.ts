import { describe, expect, it } from "vitest";

import type { Application } from "@/types/application";

import {
  buildAppMetricsUrl,
  buildDeploymentMetricsUrl,
  buildTierMetricsUrl,
} from "./grafana";

const sampleApp: Application = {
  id: 1,
  name: "ada",
  nodename: "amberd-acme-prod-ada",
  cpu: 0,
  memory: 0,
  gpu: 0,
  status: "Healthy",
  client_name: null,
  instance_name: null,
  version: null,
};

describe("grafana URL builders", () => {
  it("orders tier metrics variables from parent to child", () => {
    expect(buildTierMetricsUrl(1)).toContain(
      "var-tier=tier2&var-namespace=%24__all&var-app=%24__all&var-deployment=%24__all",
    );
  });

  it("orders deployment metrics variables from parent to child", () => {
    expect(buildDeploymentMetricsUrl(2, sampleApp)).toContain(
      "var-tier=tier3&var-namespace=amberd-acme-prod-ada&var-app=ada&var-deployment=ada",
    );
  });

  it("preserves app metrics filters while keeping parent variables first", () => {
    expect(buildAppMetricsUrl("ada")).toContain(
      "var-tier=%24__all&var-namespace=%24__all&var-app=ada&var-deployment=ada",
    );
  });
});
