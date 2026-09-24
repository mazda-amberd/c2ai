import { describe, expect, it } from "vitest";

import type { DeploymentLogEntry, LogTimePreset } from "@/types/logs";

import {
  buildSearchFromLevelsAndText,
  computeTimeWindowMs,
  dedupeLogEntriesById,
  filterDeploymentLogEntries,
  filterEntriesByTimeWindow,
  formatLogLevelLabel,
  formatLogLineForCopy,
  formatLogMessageBodyForDisplay,
  formatLogTimestampDisplay,
  levelTokensInOrder,
  logTimePresetLabel,
  parseLevelFilters,
  removeLevelTokenRaw,
  stripAnsiSequences,
  stripLevelTokens,
} from "./logDisplay";

function entry(partial: Partial<DeploymentLogEntry>): DeploymentLogEntry {
  return {
    id: "test-id-1",
    timestamp: "2026-01-01T12:00:00Z",
    level: "info",
    logType: "statement",
    user: "u",
    db: "d",
    app: "a",
    client: "c",
    role: "none",
    durationMs: null,
    connectionId: "x",
    message: "hello world",
    ...partial,
  };
}

describe("dedupeLogEntriesById", () => {
  it("keeps one row per id (backend may emit duplicate Loki rows with the same id)", () => {
    const e1 = entry({ id: "same", message: "m1" });
    const e2 = entry({ id: "same", message: "m1" });
    const e3 = entry({ id: "other", message: "m2" });
    expect(dedupeLogEntriesById([e1, e2, e3])).toHaveLength(2);
  });
});

describe("stripAnsiSequences", () => {
  it("removes SGR so INFO is readable", () => {
    const s = "2026-04-23 - \u001b[32mINFO\u001b[0m: done";
    expect(stripAnsiSequences(s)).toBe("2026-04-23 - INFO: done");
  });
});

describe("formatLogMessageBodyForDisplay", () => {
  it("strips embedded date + level that duplicate the table columns (slash date)", () => {
    const raw =
      "2026/04/23 11:07:37 - INFO - Listing system bookmarks for roles: ['x']";
    expect(formatLogMessageBodyForDisplay(raw)).toBe(
      "Listing system bookmarks for roles: ['x']",
    );
  });

  it("strips optional logger name (Python / Chainlit) before re-stripping two-part", () => {
    const raw = "2026-04-23 10:00:00 - chainlit - INFO - Authentication successful";
    expect(formatLogMessageBodyForDisplay(raw)).toBe("Authentication successful");
  });

  it("keeps lines that are not a standard prefix", () => {
    expect(formatLogMessageBodyForDisplay("plain message")).toBe("plain message");
  });
});

describe("parseLevelFilters", () => {
  it("collects unique levels in order of first appearance", () => {
    expect(parseLevelFilters("level:error level:info")).toEqual([
      "error",
      "info",
    ]);
  });

  it("deduplicates repeated tokens", () => {
    expect(parseLevelFilters("level:info foo level:info")).toEqual(["info"]);
  });

  it("is case-insensitive", () => {
    expect(parseLevelFilters("Level:WARNING x level:debug")).toEqual([
      "warning",
      "debug",
    ]);
  });
});

describe("stripLevelTokens", () => {
  it("removes all level tokens and normalizes spaces", () => {
    expect(stripLevelTokens("a level:info b level:error c")).toBe("a b c");
  });
});

describe("levelTokensInOrder", () => {
  it("preserves scan order and raw casing", () => {
    const q = "x Level:INFO y level:error";
    const toks = levelTokensInOrder(q);
    expect(toks.map((t) => t.level)).toEqual(["info", "error"]);
    expect(toks[0].raw.toLowerCase()).toBe("level:info");
    expect(toks[1].raw.toLowerCase()).toBe("level:error");
  });
});

describe("buildSearchFromLevelsAndText / removeLevelTokenRaw", () => {
  it("rebuilds query from tokens and free text", () => {
    const q = "level:info slow";
    const toks = levelTokensInOrder(q);
    expect(buildSearchFromLevelsAndText(toks, "checkpoint")).toBe(
      "level:info checkpoint",
    );
  });

  it("removes one level token by raw substring", () => {
    expect(
      removeLevelTokenRaw("level:info foo Level:ERROR", "level:info"),
    ).toBe("foo Level:ERROR");
  });
});

describe("filterDeploymentLogEntries", () => {
  const rows = [
    entry({ id: "r1", level: "error", message: "e1" }),
    entry({ id: "r2", level: "info", message: "i1" }),
    entry({ id: "r3", level: "debug", message: "slow_query trace" }),
  ];

  it("OR-matches multiple level tokens", () => {
    const q = "level:error level:info";
    const out = filterDeploymentLogEntries(rows, q);
    expect(out.map((r) => r.level).sort()).toEqual(["error", "info"]);
  });

  it("combines OR levels with free-text AND", () => {
    const out = filterDeploymentLogEntries(
      rows,
      "level:debug level:info slow_query",
    );
    expect(out).toHaveLength(1);
    expect(out[0].level).toBe("debug");
  });

  it("applies only text when no level tokens", () => {
    const out = filterDeploymentLogEntries(rows, "e1");
    expect(out).toHaveLength(1);
    expect(out[0].level).toBe("error");
  });
});

describe("filterEntriesByTimeWindow", () => {
  const eMiddle = entry({ id: "mid", timestamp: "2026-01-01T12:00:00Z" });
  const eLater  = entry({ id: "later", timestamp: "2026-01-01T13:00:00Z" });
  const eEarlier = entry({ id: "early", timestamp: "2026-01-01T11:00:00Z" });

  it("returns all entries when both bounds are null", () => {
    expect(filterEntriesByTimeWindow([eMiddle, eLater, eEarlier], null, null)).toHaveLength(3);
  });

  it("returns all when only fromMs is null (both must be non-null to filter)", () => {
    const to = new Date("2026-01-01T12:30:00Z").getTime();
    expect(filterEntriesByTimeWindow([eMiddle, eLater], null, to)).toHaveLength(2);
  });

  it("includes entries exactly on the boundaries", () => {
    const from = new Date("2026-01-01T12:00:00Z").getTime();
    const to   = new Date("2026-01-01T13:00:00Z").getTime();
    const result = filterEntriesByTimeWindow([eMiddle, eLater, eEarlier], from, to);
    expect(result.map((e) => e.id).sort()).toEqual(["later", "mid"]);
  });

  it("excludes all entries outside the window", () => {
    const from = new Date("2026-01-01T14:00:00Z").getTime();
    const to   = new Date("2026-01-01T15:00:00Z").getTime();
    expect(filterEntriesByTimeWindow([eMiddle, eLater, eEarlier], from, to)).toHaveLength(0);
  });

  it("excludes entries with invalid timestamps", () => {
    const bad = entry({ id: "bad", timestamp: "not-a-date" });
    const from = 0;
    const to   = Date.now() + 999_999_999;
    expect(filterEntriesByTimeWindow([bad], from, to)).toHaveLength(0);
  });
});

describe("computeTimeWindowMs", () => {
  it("live preset returns null bounds and customInvalid=false", () => {
    expect(computeTimeWindowMs("live", null, null)).toEqual({
      fromMs: null,
      toMs: null,
      customInvalid: false,
    });
  });

  it("custom with missing from returns customInvalid=true", () => {
    const result = computeTimeWindowMs("custom", null, new Date());
    expect(result).toEqual({ fromMs: null, toMs: null, customInvalid: true });
  });

  it("custom with missing to returns customInvalid=true", () => {
    const result = computeTimeWindowMs("custom", new Date(), null);
    expect(result).toEqual({ fromMs: null, toMs: null, customInvalid: true });
  });

  it("custom with from > to returns customInvalid=true", () => {
    const from = new Date("2026-01-01T13:00:00Z");
    const to   = new Date("2026-01-01T12:00:00Z");
    expect(computeTimeWindowMs("custom", from, to)).toEqual({
      fromMs: null,
      toMs: null,
      customInvalid: true,
    });
  });

  it("custom with valid range returns correct bounds", () => {
    const from = new Date("2026-01-01T10:00:00Z");
    const to   = new Date("2026-01-01T12:00:00Z");
    const result = computeTimeWindowMs("custom", from, to);
    expect(result.customInvalid).toBe(false);
    expect(result.fromMs).toBe(from.getTime());
    expect(result.toMs).toBe(to.getTime());
  });

  it.each([
    ["15m", 15 * 60 * 1000],
    ["1h",  60 * 60 * 1000],
    ["4h",  4 * 60 * 60 * 1000],
    ["24h", 24 * 60 * 60 * 1000],
    ["7d",  7 * 24 * 60 * 60 * 1000],
  ] as [LogTimePreset, number][])(
    "%s preset returns a window of exactly %d ms",
    (preset, expectedDelta) => {
      const before = Date.now();
      const result = computeTimeWindowMs(preset, null, null);
      const after  = Date.now();
      expect(result.customInvalid).toBe(false);
      expect(result.toMs).toBeGreaterThanOrEqual(before);
      expect(result.toMs).toBeLessThanOrEqual(after);
      expect(result.toMs! - result.fromMs!).toBe(expectedDelta);
    },
  );
});

describe("logTimePresetLabel", () => {
  it.each([
    ["live",   "Live tail"],
    ["15m",    "Last 15 minutes"],
    ["1h",     "Last 1 hour"],
    ["4h",     "Last 4 hours"],
    ["24h",    "Last 24 hours"],
    ["7d",     "Last 7 days"],
    ["custom", "Custom range…"],
  ] as [LogTimePreset, string][])(
    "preset '%s' → '%s'",
    (preset, label) => {
      expect(logTimePresetLabel(preset)).toBe(label);
    },
  );
});

describe("formatLogTimestampDisplay", () => {
  it("formats a valid ISO string to a human-readable locale string", () => {
    const result = formatLogTimestampDisplay("2026-01-01T12:00:00Z");
    expect(result).not.toBe("2026-01-01T12:00:00Z");
    expect(result).toMatch(/Jan/);
    expect(result).toMatch(/1/);
  });

  it("returns the raw input for an unparseable date string", () => {
    expect(formatLogTimestampDisplay("not-a-date")).toBe("not-a-date");
  });
});

describe("formatLogLevelLabel", () => {
  it.each([
    ["debug",    "DEBUG"],
    ["info",     "INFO"],
    ["warning",  "WARNING"],
    ["error",    "ERROR"],
    ["fatal",    "FATAL"],
    ["critical", "CRITICAL"],
  ] as [Parameters<typeof formatLogLevelLabel>[0], string][])(
    "level '%s' → '%s'",
    (level, expected) => {
      expect(formatLogLevelLabel(level)).toBe(expected);
    },
  );
});

describe("formatLogLineForCopy", () => {
  it("includes timestamp, level, id and message", () => {
    const e = entry({ id: "abc", level: "error", message: "boom" });
    const result = formatLogLineForCopy(e);
    expect(result).toContain("id=abc");
    expect(result).toContain("ERROR");
    expect(result).toContain("boom");
  });

  it("includes durationMs when present", () => {
    const e = entry({ durationMs: 123 });
    expect(formatLogLineForCopy(e)).toContain("durationMs=123");
  });

  it("omits durationMs when null", () => {
    const e = entry({ durationMs: null });
    expect(formatLogLineForCopy(e)).not.toContain("durationMs");
  });

  it("appends labels as JSON when present and non-empty", () => {
    const e = entry({ labels: { pod: "p1", ns: "default" } });
    const result = formatLogLineForCopy(e);
    expect(result).toContain("labels=");
    expect(result).toContain('"pod":"p1"');
  });

  it("omits labels field when labels is empty object", () => {
    const e = entry({ labels: {} });
    expect(formatLogLineForCopy(e)).not.toContain("labels=");
  });

  it("omits labels field when labels is undefined", () => {
    const e = entry({ labels: undefined });
    expect(formatLogLineForCopy(e)).not.toContain("labels=");
  });
});
