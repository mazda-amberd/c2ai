import getRouterBasename from "@/lib/router";

// Backend URL from environment variable, with fallback
const backendUrl = import.meta.env.VITE_BACKEND_URL || "http://localhost:8007";

// Base URL depending on environment
const url = import.meta.env.DEV
  ? backendUrl + getRouterBasename()
  : window.origin + getRouterBasename();

// Remove trailing slash to avoid double-slash when joining with path
const BASE_URL = new URL(url).toString().replace(/\/$/, "");

// Handle 401 globally
const handle401 = () => {
  const loginPath = getRouterBasename() + "/login";
  if (window.location.pathname !== loginPath) {
    window.location.href = loginPath;
  }
};

// Handle general errors
const handleError = (error: unknown) => {
  console.error(error);
};

/** Network-level failures (`fetch` TypeError) carry browser wording like
 *  "Failed to fetch" — replace with something a user can act on. Aborts are
 *  passed through so callers that cancel stale requests can ignore them. */
function toUserFacingError(error: unknown): unknown {
  if (error instanceof ApiFetchError) return error;
  if (error instanceof DOMException && error.name === "AbortError") return error;
  if (error instanceof TypeError) {
    return new Error("Could not reach the backend. Check that it is running and try again.");
  }
  return error;
}

type ValidationIssue = { loc?: unknown[]; msg?: string; type?: string };

/** "body.container.registry_username" → "registry_username". */
function fieldKey(loc: unknown[] | undefined): string | null {
  if (!loc) return null;
  const parts = loc.filter(
    (p): p is string => typeof p === "string" && p !== "body" && p !== "query" && p !== "path",
  );
  return parts[parts.length - 1] ?? null;
}

/** "body.container.registry_username" → "Registry username". */
function fieldLabel(loc: unknown[] | undefined): string | null {
  const key = fieldKey(loc);
  if (!key) return null;
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** End a message with exactly one sentence terminator. */
function asSentence(text: string): string {
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

/** Pydantic's messages are terse ("String should have at least 1 character");
 *  turn the common ones into plain language. */
function humanizeIssue(issue: ValidationIssue): string {
  const label = fieldLabel(issue.loc);
  const msg = issue.msg ?? "is invalid";
  const type = issue.type ?? "";
  if (type === "value_error" || msg.startsWith("Value error, ")) {
    const reason = msg.replace(/^Value error, /, "").trim();
    // A backend validator that writes a full sentence already names the field
    // (and the value the user typed), so show it exactly as written.
    if (/^[A-Z]/.test(reason)) return asSentence(reason);
    // Older validators start with the raw field key ("repository_url must …"):
    // swap that key for its label instead of printing both.
    const key = fieldKey(issue.loc);
    if (key && label && reason.startsWith(`${key} `)) {
      return asSentence(`${label} ${reason.slice(key.length + 1)}`);
    }
  }
  let text: string;
  if (type === "string_too_short" && /at least 1 character/.test(msg)) text = "is required";
  else if (type === "missing") text = "is required";
  else if (type === "string_too_long") text = msg.replace(/^String should have /, "must have ");
  else if (type === "string_pattern_mismatch") text = "has an invalid format";
  else text = msg.replace(/^(Value error, |Input )/, "").replace(/^[A-Z]/, (c) => c.toLowerCase());
  return label ? `${label} ${text}.` : `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

/**
 * Turn any FastAPI/Athena error body into a sentence a user can act on.
 * Handles `detail` as a string, a list of strings, a list of Pydantic
 * validation issues (`{loc, msg, type}`), or an object with a
 * `message` / `error` (e.g. GrafanaFetchError).
 */
export function parseFastApiDetail(body: string): string | null {
  try {
    const o = JSON.parse(body) as { detail?: unknown };
    const detail = o.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) =>
          typeof item === "string"
            ? item
            : item && typeof item === "object"
              ? humanizeIssue(item as ValidationIssue)
              : null,
        )
        .filter((p): p is string => !!p);
      if (parts.length === 0) return null;
      // Plain string lists keep the historical "; " separator; humanized
      // validation issues are already full sentences.
      const allPlain = detail.every((item) => typeof item === "string");
      return parts.join(allPlain ? "; " : " ");
    }
    if (detail && typeof detail === "object") {
      const d = detail as { message?: unknown; error?: unknown };
      if (typeof d.message === "string") return d.message;
      if (typeof d.error === "string") return d.error;
    }
    return null;
  } catch {
    return null;
  }
}

export class ApiFetchError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = "ApiFetchError";
    this.status = status;
    this.body = body;
  }
}

// Generic fetch wrapper
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  try {
    const response = await fetch(BASE_URL + path, {
      credentials: "include",
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
    });

    if (!response.ok) {
      if (response.status === 401) handle401();

      const errorText = await response.text();
      const parsed = parseFastApiDetail(errorText);
      const message = parsed ?? (errorText || "Unknown error");
      throw new ApiFetchError(message, response.status, errorText);
    }

    // A successful 204 response intentionally has no body or Content-Type.
    // Return before the JSON-only guard so DELETE endpoints can use the
    // standard HTTP no-content response without being mistaken for the SPA.
    if (response.status === 204) return undefined as T;

    // The backend's SPA catch-all answers unknown paths with index.html and a
    // 200 — surface that as a clear "route missing" error instead of letting
    // response.json() fail on "<!doctype html>".
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) {
      const body = await response.text();
      throw new ApiFetchError(
        `Expected JSON from ${path} but received ${contentType || "no content-type"} — ` +
          "the backend has no route for this path (SPA fallback).",
        response.status,
        body,
      );
    }

    return (await response.json()) as T;
  } catch (error) {
    handleError(error);
    throw toUserFacingError(error);
  }
}

/** Like apiFetch, but returns the raw body as a Blob (e.g. PDF downloads). */
export async function apiFetchBlob(
  path: string,
  options: RequestInit = {},
): Promise<Blob> {
  try {
    const response = await fetch(BASE_URL + path, {
      credentials: "include",
      ...options,
    });

    if (!response.ok) {
      if (response.status === 401) handle401();

      const errorText = await response.text();
      const parsed = parseFastApiDetail(errorText);
      const message = parsed ?? (errorText || "Unknown error");
      throw new ApiFetchError(message, response.status, errorText);
    }

    return await response.blob();
  } catch (error) {
    handleError(error);
    throw error;
  }
}