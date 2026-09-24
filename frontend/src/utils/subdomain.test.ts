import { describe, expect, it } from "vitest";

import {
  customerNameFromAmberdSubdomain,
  customerNameFromWorkflowSubdomain,
  deploymentPreviewUrl,
  isValidWorkflowHostLabel,
  splitWorkflowHostLabel,
  workflowPrepareSubdomain,
} from "./subdomain";

describe("customerNameFromAmberdSubdomain", () => {
  it("strips amberd- prefix", () => {
    expect(customerNameFromAmberdSubdomain("amberd-acme")).toBe("acme");
  });

  it("leaves strings without prefix unchanged", () => {
    expect(customerNameFromAmberdSubdomain("other")).toBe("other");
  });

  it("only removes leading prefix", () => {
    expect(customerNameFromAmberdSubdomain("x-amberd-y")).toBe("x-amberd-y");
  });
});

describe("workflowPrepareSubdomain", () => {
  it("builds amberd-{customer}-{env}", () => {
    expect(workflowPrepareSubdomain("Acme Co", "ADA")).toBe("amberd-acme-co-ada");
    expect(workflowPrepareSubdomain("x/y", "ada-1")).toBe("amberd-x-y-ada-1");
  });

  it("strips leading and trailing hyphens from slug segments", () => {
    expect(workflowPrepareSubdomain("--leading", "ada")).toBe("amberd-leading-ada");
    expect(workflowPrepareSubdomain("acme", "--ada--")).toBe("amberd-acme-ada");
  });

  it("produces a valid host label for common inputs", () => {
    const pairs: [string, string][] = [
      ["acme", "ada"],
      ["Acme Corp", "ada"],
      ["my-company", "ada"],
      ["x/y", "ada"],
    ];
    for (const [c, e] of pairs) {
      const label = workflowPrepareSubdomain(c, e);
      expect(isValidWorkflowHostLabel(label), `label for "${c}"/"${e}": ${label}`).toBe(
        true,
      );
    }
  });
});

describe("splitWorkflowHostLabel", () => {
  it("splits customer and env from a full label", () => {
    expect(splitWorkflowHostLabel("amberd-acme-corp-ada")).toEqual({
      customer: "acme-corp",
      env: "ada",
    });
  });

  it("returns env empty when no trailing segment", () => {
    expect(splitWorkflowHostLabel("amberd-acme")).toEqual({ customer: "acme", env: "" });
  });
});

describe("deploymentPreviewUrl", () => {
  it("builds https URL with trailing slash", () => {
    expect(deploymentPreviewUrl("amberd-acme-ada", "amberd.ai")).toBe(
      "https://amberd-acme-ada.amberd.ai/",
    );
  });
});

describe("customerNameFromWorkflowSubdomain", () => {
  it("parses amberd-{customer}-{env} when env matches", () => {
    expect(
      customerNameFromWorkflowSubdomain("amberd-acme-ada", "ADA"),
    ).toBe("acme");
  });

  it("falls back for legacy amberd-{customer}", () => {
    expect(
      customerNameFromWorkflowSubdomain("amberd-acme", "ADA"),
    ).toBe("acme");
  });
});

describe("isValidWorkflowHostLabel", () => {
  it("accepts valid labels", () => {
    expect(isValidWorkflowHostLabel("amberd-acme")).toBe(true);
    expect(isValidWorkflowHostLabel("amberd-acme-co")).toBe(true);
    expect(isValidWorkflowHostLabel("amberd-acme-ada")).toBe(true);
    expect(isValidWorkflowHostLabel("abc")).toBe(true);
    expect(isValidWorkflowHostLabel("a1b2c3")).toBe(true);
  });

  it("rejects labels with uppercase letters", () => {
    expect(isValidWorkflowHostLabel("Amberd-acme")).toBe(false);
    expect(isValidWorkflowHostLabel("AMBERD-ACME")).toBe(false);
  });

  it("rejects labels with leading or trailing hyphens", () => {
    expect(isValidWorkflowHostLabel("-amberd-acme")).toBe(false);
    expect(isValidWorkflowHostLabel("amberd-acme-")).toBe(false);
  });

  it("rejects labels that are too short", () => {
    expect(isValidWorkflowHostLabel("ab")).toBe(false);
  });

  it("rejects labels that are too long (> 63 chars)", () => {
    expect(isValidWorkflowHostLabel("a".repeat(64))).toBe(false);
  });

  it("rejects labels with spaces or special characters", () => {
    expect(isValidWorkflowHostLabel("amberd acme")).toBe(false);
    expect(isValidWorkflowHostLabel("amberd_acme")).toBe(false);
    expect(isValidWorkflowHostLabel("amberd.acme")).toBe(false);
  });
});
