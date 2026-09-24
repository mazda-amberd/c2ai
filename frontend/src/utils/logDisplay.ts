import type {
  DeploymentLogEntry,
  DeploymentLogLevel,
  LogTimePreset,
} from "@/types/logs";

/** Canonical order for parsing multiple `level:` tokens (OR semantics). */
export const DEPLOYMENT_LOG_LEVELS: readonly DeploymentLogLevel[] = [
  "debug",
  "trace",
  "info",
  "warning",
  "error",
  "fatal",
  "critical",
] as const;

const LEVEL_ALT = DEPLOYMENT_LOG_LEVELS.join("|");
const LEVEL_QUERY_PATTERN = `\\blevel:\\s*(${LEVEL_ALT})\\b`;

/** CSI / OSC control sequences (container logs often use SGR e.g. \\u001B[32m for colored INFO). */
const ANSI_SEQUENCES =
  // eslint-disable-next-line no-control-regex
  /\u001b\][\s\S]*?(?:\u0007|\u001b\\)|\u001b\[[0-?]*[ -/]*[@-~]/g;

/**
 * Strips terminal escape codes so the message column and search work on real text
 * (colored log lines embed codes between tokens and would otherwise look "unparsed").
 */
export function stripAnsiSequences(text: string): string {
  if (!text) return text;
  return text.replace(ANSI_SEQUENCES, "");
}

/** Uvicorn/Chainlit/Python: spaces around `-`, `–`, or `—`. */
const DASH = String.raw`[\s\u00A0]*[-\u2013\u2014][\s\u00A0]*`;

/**
 * Level words often embedded in log text after a timestamp, redundant with
 * the viewer's level column. Order matters: longer words first.
 */
const EMBEDDED_LEVEL = String.raw`INFORMATION|WARNING|CRITICAL|DEBUG|TRACE|VERBOSE|FATAL|ERROR|INFO|WARN|ERR|CRIT`;

// 2026/04/23 11:07:37 - myservice - INFO - The message (optional logger segment)
const RE_LOG_PREFIX_THREE_PARTS = new RegExp(
  `^\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}[ T]\\d{1,2}:\\d{2}:\\d{2}(?:[.,]\\d+)?` +
    `${DASH}(.+?)${DASH}(${EMBEDDED_LEVEL})\\b(?:${DASH}|:[\\s\u00A0]*)`,
  "is",
);

// 2026/04/23 11:07:37 - INFO: …   or  … - INFO - …
const RE_LOG_PREFIX_TWO_PARTS = new RegExp(
  `^\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}[ T]\\d{1,2}:\\d{2}:\\d{2}(?:[.,]\\d+)?` +
    `${DASH}(${EMBEDDED_LEVEL})\\b(?:${DASH}|:[\\s\u00A0]*)`,
  "i",
);

/**
 * Removes an embedded "timestamp + level" prefix when the line already has
 * structured time/level columns in the UI (stops the same data appearing twice in one row).
 */
function stripRedundantInlineLogPreamble(plain: string): string {
  const original = plain.trim();
  if (!original) return original;
  let t = original;
  for (let i = 0; i < 3; i += 1) {
    const a = t.replace(RE_LOG_PREFIX_THREE_PARTS, "");
    if (a !== t) {
      t = a.trim();
      continue;
    }
    const b = t.replace(RE_LOG_PREFIX_TWO_PARTS, "");
    if (b !== t) {
      t = b.trim();
      continue;
    }
    break;
  }
  if (t.length === 0) return original;
  return t;
}

/**
 * Message as shown in the list / detail: no ANSI, no duplicate timestamp+level prefix.
 * Raw `entry.message` is unchanged in data from the API.
 */
export function formatLogMessageBodyForDisplay(message: string): string {
  return stripRedundantInlineLogPreamble(stripAnsiSequences(message));
}

function isDeploymentLogLevel(s: string): s is DeploymentLogLevel {
  return (DEPLOYMENT_LOG_LEVELS as readonly string[]).includes(s);
}

export const LOG_LEVEL_SEARCH_CHIPS = DEPLOYMENT_LOG_LEVELS.map(
  (l) => `level:${l}` as const,
);

/** Display like PostgreSQL-style log timestamps (local timezone). */
export function formatLogTimestampDisplay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

export function formatLogLevelLabel(level: DeploymentLogLevel): string {
  return level.toUpperCase();
}

/** Single-line log for clipboard. */
export function formatLogLineForCopy(entry: DeploymentLogEntry): string {
  const time = formatLogTimestampDisplay(entry.timestamp);
  const lvl = formatLogLevelLabel(entry.level);
  const bits = [
    `id=${entry.id}`,
    `type=${entry.logType}`,
    `role=${entry.role}`,
    `user=${entry.user}`,
    `instance=${entry.db}`,
    `app=${entry.app}`,
    `client=${entry.client}`,
    `conn=${entry.connectionId}`,
  ];
  if (entry.durationMs != null) {
    bits.splice(3, 0, `durationMs=${entry.durationMs}`);
  }
  if (entry.labels && Object.keys(entry.labels).length) {
    bits.push(`labels=${JSON.stringify(entry.labels)}`);
  }
  return `${time} ${lvl} ${bits.join(", ")} | ${formatLogMessageBodyForDisplay(entry.message)}`;
}

/** Keep the newest occurrence when the same log line id appears across polls. */
export function dedupeLogEntriesById(
  entries: DeploymentLogEntry[],
): DeploymentLogEntry[] {
  const seen = new Set<string>();
  const out: DeploymentLogEntry[] = [];
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const e = entries[i];
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    out.push(e);
  }
  out.reverse();
  return out;
}

/**
 * Every `level:(…)` token, unique, first occurrence order preserved.
 * Multiple tokens combine with OR in {@link filterDeploymentLogEntries}.
 */
export function parseLevelFilters(query: string): DeploymentLogLevel[] {
  const seen = new Set<DeploymentLogLevel>();
  const out: DeploymentLogLevel[] = [];
  let m: RegExpExecArray | null;
  const re = new RegExp(LEVEL_QUERY_PATTERN, "gi");
  while ((m = re.exec(query)) !== null) {
    const v = m[1].toLowerCase();
    if (!isDeploymentLogLevel(v) || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

export function stripLevelTokens(query: string): string {
  return query
    .replace(new RegExp(LEVEL_QUERY_PATTERN, "gi"), " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Every `level:(…)` match in scan order (duplicates preserved). */
export function levelTokensInOrder(
  query: string,
): { raw: string; level: DeploymentLogLevel }[] {
  const out: { raw: string; level: DeploymentLogLevel }[] = [];
  const re = new RegExp(LEVEL_QUERY_PATTERN, "gi");
  let m: RegExpExecArray | null;
  while ((m = re.exec(query)) !== null) {
    const v = m[1].toLowerCase();
    if (!isDeploymentLogLevel(v)) continue;
    out.push({ raw: m[0], level: v });
  }
  return out;
}

export function buildSearchFromLevelsAndText(
  tokens: readonly { raw: string }[],
  textRest: string,
): string {
  const t = textRest.replace(/\s+/g, " ").trim();
  const prefix = tokens.map((x) => x.raw).join(" ").trim();
  if (!prefix) return t;
  return t ? `${prefix} ${t}` : prefix;
}

/** Removes the first case-insensitive occurrence of `raw` and normalizes spaces. */
export function removeLevelTokenRaw(query: string, raw: string): string {
  const lower = query.toLowerCase();
  const needle = raw.toLowerCase();
  const idx = lower.indexOf(needle);
  if (idx < 0) return query.replace(/\s+/g, " ").trim();
  const out =
    query.slice(0, idx) + query.slice(idx + raw.length);
  return out.replace(/\s+/g, " ").trim();
}

function entryHaystack(e: DeploymentLogEntry): string {
  const labelBits = e.labels
    ? Object.values(e.labels)
        .map((v) => String(v))
        .join(" ")
    : "";
  return [
    e.id,
    formatLogMessageBodyForDisplay(e.message),
    e.connectionId,
    e.user,
    e.db,
    e.app,
    e.client,
    e.logType,
    e.role,
    e.durationMs != null ? String(e.durationMs) : "",
    labelBits,
  ]
    .join(" ")
    .toLowerCase();
}

export function filterDeploymentLogEntries(
  entries: DeploymentLogEntry[],
  query: string,
): DeploymentLogEntry[] {
  const levels = parseLevelFilters(query);
  const levelSet = new Set(levels);
  const textPart = stripLevelTokens(query).toLowerCase();
  if (!levelSet.size && !textPart) return entries;

  return entries.filter((e) => {
    if (levelSet.size && !levelSet.has(e.level)) return false;
    if (!textPart) return true;
    return entryHaystack(e).includes(textPart);
  });
}

/** Filter by entry timestamp (ISO). Null bounds = no filter (e.g. live tail). */
export function filterEntriesByTimeWindow(
  entries: DeploymentLogEntry[],
  fromMs: number | null,
  toMs: number | null,
): DeploymentLogEntry[] {
  if (fromMs == null || toMs == null) return entries;
  return entries.filter((e) => {
    const t = new Date(e.timestamp).getTime();
    if (Number.isNaN(t)) return false;
    return t >= fromMs && t <= toMs;
  });
}

/** Relative window length for each non-live, non-custom preset (milliseconds). */
export const LOG_RELATIVE_PRESET_MS: Record<
  Exclude<LogTimePreset, "live" | "custom">,
  number
> = {
  "15m": 15 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "4h": 4 * 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
};

const PRESET_MS = LOG_RELATIVE_PRESET_MS;

export function computeTimeWindowMs(
  preset: LogTimePreset,
  customFromAt: Date | null,
  customToAt: Date | null,
): { fromMs: number | null; toMs: number | null; customInvalid: boolean } {
  if (preset === "live") {
    return { fromMs: null, toMs: null, customInvalid: false };
  }
  const now = Date.now();
  if (preset === "custom") {
    if (!customFromAt || !customToAt) {
      return { fromMs: null, toMs: null, customInvalid: true };
    }
    const f = customFromAt.getTime();
    const t = customToAt.getTime();
    if (Number.isNaN(f) || Number.isNaN(t)) {
      return { fromMs: null, toMs: null, customInvalid: true };
    }
    if (f > t) {
      return { fromMs: null, toMs: null, customInvalid: true };
    }
    return { fromMs: f, toMs: t, customInvalid: false };
  }
  const delta = PRESET_MS[preset];
  return { fromMs: now - delta, toMs: now, customInvalid: false };
}

export function logTimePresetLabel(preset: LogTimePreset): string {
  switch (preset) {
    case "live":
      return "Live tail";
    case "15m":
      return "Last 15 minutes";
    case "1h":
      return "Last 1 hour";
    case "4h":
      return "Last 4 hours";
    case "24h":
      return "Last 24 hours";
    case "7d":
      return "Last 7 days";
    case "custom":
      return "Custom range…";
    default:
      return preset;
  }
}
