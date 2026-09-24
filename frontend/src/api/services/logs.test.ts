import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse } from "@/test-utils/pipeline";

import { getDeploymentLogs } from "./logs";

const baseResponse = {
  entries: [],
  source: "loki" as const,
  has_more: false,
  next_cursor: null,
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("getDeploymentLogs", () => {
  it("always includes subdomain and deployment in the URL", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "acme", deployment: "my-app" });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain("subdomain=acme");
    expect(url).toContain("deployment=my-app");
  });

  it("includes tier when provided", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b", tier: 2 });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain("tier=2");
  });

  it("omits tier when undefined", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b" });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).not.toContain("tier=");
  });

  it("includes from and to when provided", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({
      subdomain: "a",
      deployment: "b",
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-01T01:00:00Z",
    });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain("from=2026-01-01T00%3A00%3A00Z");
    expect(url).toContain("to=2026-01-01T01%3A00%3A00Z");
  });

  it("omits from/to when not provided", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b" });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).not.toContain("from=");
    expect(url).not.toContain("to=");
  });

  it("includes search when non-empty", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b", search: "level:error" });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain("search=level%3Aerror");
  });

  it("omits search when whitespace-only", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b", search: "   " });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).not.toContain("search=");
  });

  it("omits search when undefined", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b" });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).not.toContain("search=");
  });

  it("includes limit and cursor when provided", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({
      subdomain: "a",
      deployment: "b",
      limit: 500,
      cursor: "tok123",
    });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain("limit=500");
    expect(url).toContain("cursor=tok123");
  });

  it("omits limit and cursor when not provided", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b" });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).not.toContain("limit=");
    expect(url).not.toContain("cursor=");
  });

  it("includes tail=true when tail is true", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b", tail: true });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain("tail=true");
  });

  it("omits tail when false or undefined", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(baseResponse));
    await getDeploymentLogs({ subdomain: "a", deployment: "b", tail: false });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).not.toContain("tail=");
  });

  it("returns the parsed response body on success", async () => {
    const response = { ...baseResponse, has_more: true, next_cursor: "cur-abc" };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    const result = await getDeploymentLogs({ subdomain: "a", deployment: "b" });
    expect(result).toEqual(response);
  });

  it("rewrites a 404 with detail 'Not Found' to a helpful message", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Not Found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );
    let caught: unknown;
    try {
      await getDeploymentLogs({ subdomain: "a", deployment: "b" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(Error);
    expect((caught as Error).message).toContain("Logs endpoint not found (404)");
    expect((caught as Error).message).toContain("Server: Not Found");
  });

  it("rewrites any 404 by status and appends backend detail when JSON", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "no matching route" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );
    let caught: unknown;
    try {
      await getDeploymentLogs({ subdomain: "a", deployment: "b" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(Error);
    expect((caught as Error).message).toContain("Logs endpoint not found (404)");
    expect((caught as Error).message).toContain("Server: no matching route");
  });

  it("rethrows non-404 HTTP errors unchanged", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Internal Server Error" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(
      getDeploymentLogs({ subdomain: "a", deployment: "b" }),
    ).rejects.toThrow("Internal Server Error");
  });
});
