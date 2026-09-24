import { describe, expect, it } from "vitest";

import { parseFastApiDetail } from "./index";

describe("parseFastApiDetail", () => {
  it("returns a plain string detail as is", () => {
    expect(parseFastApiDetail(JSON.stringify({ detail: "Missing token" }))).toBe("Missing token");
  });

  it("joins a list of string details", () => {
    expect(parseFastApiDetail(JSON.stringify({ detail: ["a", "b"] }))).toBe("a; b");
  });

  it("turns Pydantic validation issues into readable sentences", () => {
    // Exact shape FastAPI returns for POST /api/github-connections/validate
    // with an empty form.
    const body = JSON.stringify({
      detail: [
        {
          loc: ["body", "connection_name"],
          msg: "String should have at least 1 character",
          type: "string_too_short",
        },
        {
          loc: ["body", "repository_url"],
          msg: "Value error, repository_url must be a GitHub repository URL",
          type: "value_error",
        },
        { loc: ["body", "access_token"], msg: "Field required", type: "missing" },
      ],
      code: "RequestValidationError",
    });

    expect(parseFastApiDetail(body)).toBe(
      "Connection name is required. " +
        "Repository url must be a GitHub repository URL. " +
        "Access token is required.",
    );
  });

  it("uses the nested field name for container registration errors", () => {
    const body = JSON.stringify({
      detail: [
        {
          loc: ["body", "container", "storage"],
          msg: "String should have at least 1 character",
          type: "string_too_short",
        },
      ],
    });
    expect(parseFastApiDetail(body)).toBe("Storage is required.");
  });

  it("reads message/error objects such as GrafanaFetchError", () => {
    const body = JSON.stringify({
      detail: { error: "Grafana request failed", message: "Grafana query failed: 400" },
      code: "GrafanaFetchError",
    });
    expect(parseFastApiDetail(body)).toBe("Grafana query failed: 400");
  });

  it("returns null for bodies it cannot interpret", () => {
    expect(parseFastApiDetail("<!doctype html>")).toBeNull();
    expect(parseFastApiDetail(JSON.stringify({ detail: 42 }))).toBeNull();
  });
});
