import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelPipeline,
  getActivePipelines,
  getGithubBranches,
  getGithubTags,
  terminateDeployment,
  triggerDeployment,
  triggerMoveTierDeployment,
  triggerUpdateDeployment,
} from "./deployments";
import {
  jsonResponse,
  basePipelineRun as samplePipelineRun,
  basePipelineStatus as samplePipelineStatus,
} from "@/test-utils/pipeline";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("deployment API helpers", () => {
  it("triggerDeployment posts JSON to /api/deploy", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(samplePipelineRun));

    const out = await triggerDeployment({
      branch: "main",
      subdomain: "amberd-acme-ada",
      customer_name: "acme",
      domain: "amberd.ai",
      env_instance: "ADA",
      tier: 2,
    });

    expect(out).toEqual(samplePipelineRun);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/deploy",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({
          branch: "main",
          subdomain: "amberd-acme-ada",
          customer_name: "acme",
          domain: "amberd.ai",
          env_instance: "ADA",
          tier: 2,
        }),
      }),
    );
  });

  it("triggerUpdateDeployment posts JSON to /api/deploy/update", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(samplePipelineRun));

    const out = await triggerUpdateDeployment({
      branch: "main",
      subdomain: "amberd-acme-ada",
      customer_name: "acme",
      domain: "amberd.ai",
      env_instance: "ADA",
      tier: 2,
    });

    expect(out).toEqual(samplePipelineRun);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/deploy/update",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({
          branch: "main",
          subdomain: "amberd-acme-ada",
          customer_name: "acme",
          domain: "amberd.ai",
          env_instance: "ADA",
          tier: 2,
        }),
      }),
    );
  });

  it("getActivePipelines fetches /api/pipeline/active", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([samplePipelineStatus]));

    const out = await getActivePipelines();

    expect(out).toEqual([samplePipelineStatus]);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/pipeline/active",
      expect.objectContaining({
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  it("triggerMoveTierDeployment posts JSON to /api/deploy/move-tier", async () => {
    const moveTierRun = { ...samplePipelineRun, operation: "migration", tier: 2 };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(moveTierRun));

    const out = await triggerMoveTierDeployment({
      subdomain: "amberd-acme-ada",
      tier: 2,
    });

    expect(out).toEqual(moveTierRun);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/deploy/move-tier",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({
          subdomain: "amberd-acme-ada",
          tier: 2,
        }),
      }),
    );
  });

  it("getGithubBranches encodes repo name", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(["a", "b"]));

    const out = await getGithubBranches("my repo");

    expect(out).toEqual(["a", "b"]);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/github/branches?repo=my%20repo",
      expect.objectContaining({
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  it("getGithubTags encodes repo name and hits /api/github/tags", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(["v1.0.0", "v1.1.0"]));

    const out = await getGithubTags("my repo");

    expect(out).toEqual(["v1.0.0", "v1.1.0"]);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/github/tags?repo=my%20repo",
      expect.objectContaining({
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  it("cancelPipeline posts pipeline_run_id to /api/pipeline/cancel", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(samplePipelineRun));

    const out = await cancelPipeline("run-uuid-1");

    expect(out).toEqual(samplePipelineRun);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/pipeline/cancel",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ pipeline_run_id: "run-uuid-1" }),
      }),
    );
  });

  it("terminateDeployment posts subdomain body and returns PipelineRunRecord", async () => {
    const terminateRun = { ...samplePipelineRun, operation: "terminate", tier: null };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(terminateRun));

    const out = await terminateDeployment("amberd-x");

    expect(out.operation).toBe("terminate");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8007/api/deploy/terminate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ subdomain: "amberd-x" }),
      }),
    );
  });
});
