"""LogQL construction and Grafana Loki /ds/query response parsing."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from c2ai.schemas.logs import DeploymentLogEntryOut, LogLevelLiteral

# 7-bit CSI (colors, SGR) and common OSC (window title) — strip before text-level heuristics.
_ANSI_ESCAPE = re.compile(
    r"\x1b\][^\x1b\x07\x08]*(?:\x1b\\|\x07)"  # OSC
    r"|\x1b\][\x20-\x7e]*\x07"  # OSC variant
    r"|\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]",  # CSI
)


def _strip_ansi(s: str) -> str:
    if not s:
        return s
    return _ANSI_ESCAPE.sub("", s)


_LEVEL_ALIASES: dict[str, LogLevelLiteral] = {
    "debug": "debug",
    "trace": "trace",
    "verbose": "trace",
    "info": "info",
    "information": "info",
    "warn": "warning",
    "warning": "warning",
    "err": "error",
    "error": "error",
    "fatal": "fatal",
    "critical": "critical",
    "crit": "critical",
}


def escape_logql_label_value(value: str) -> str:
    """Escape a value for use inside a LogQL double-quoted label matcher."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def escape_logql_line_literal(value: str) -> str:
    """Escape a substring for LogQL ``|= "…"`` line filters."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


_LOG_LEVEL_ORDER: tuple[LogLevelLiteral, ...] = (
    "debug",
    "trace",
    "info",
    "warning",
    "error",
    "fatal",
    "critical",
)
_LEVEL_ALT = "|".join(_LOG_LEVEL_ORDER)
_LEVEL_QUERY_RE = re.compile(rf"\blevel:\s*({_LEVEL_ALT})\b", re.IGNORECASE)

# Coarse line matchers so LogQL can pre-filter before the API normalises levels.
# NOTE: Loki uses Go RE2 which does NOT support \b word boundaries (\b is treated as
# a literal backspace \x08 in RE2). Use plain strings instead; (?i) handles case.
_LEVEL_LINE_REGEX_ALTS: dict[LogLevelLiteral, str] = {
    "debug": r"DEBUG",
    "trace": r"TRACE|VERBOSE",
    "info": r"INFO|INFORMATION",
    "warning": r"WARNING|WARN",
    "error": r"ERROR|ERR",
    "fatal": r"FATAL",
    "critical": r"CRITICAL|CRIT",
}


def parse_level_filters_from_search(query: str) -> list[LogLevelLiteral]:
    """Parse ``level:…`` tokens (OR semantics), unique, first-appearance order."""
    seen: set[LogLevelLiteral] = set()
    out: list[LogLevelLiteral] = []
    for m in _LEVEL_QUERY_RE.finditer(query or ""):
        raw = m.group(1).lower()
        if raw not in seen and raw in _LOG_LEVEL_ORDER:
            lv = raw  # type: ignore[assignment]
            seen.add(lv)
            out.append(lv)
    return out


def strip_level_tokens_from_search(query: str) -> str:
    t = _LEVEL_QUERY_RE.sub(" ", query or "")
    return re.sub(r"\s+", " ", t).strip()


def build_loki_line_filter_pipeline(search: Optional[str]) -> str:
    """
    Build LogQL pipeline stages after the stream selector for server-side search.

    - Free text: whitespace-split tokens, each as a literal ``|=`` contains filter.
    - ``level:`` tokens: OR of permissive ``|~`` regex fragments on the raw line.
    """
    q = (search or "").strip()
    if not q:
        return ""
    if len(q) > 400:
        q = q[:400]

    levels = parse_level_filters_from_search(q)
    text = strip_level_tokens_from_search(q)

    parts: list[str] = []
    if text:
        for tok in text.split():
            if not tok:
                continue
            if len(tok) > 128:
                tok = tok[:128]
            parts.append(f'|= "{escape_logql_line_literal(tok)}"')
            if len(parts) >= 16:
                break

    if levels:
        alts = "|".join(_LEVEL_LINE_REGEX_ALTS[l] for l in levels)
        parts.append(f"|~ \"(?i)(?:{alts})\"")

    if not parts:
        return ""
    return " " + " ".join(parts)


def build_full_loki_logql(
    namespace: str,
    deployment: str,
    tier: Optional[int],
    search: Optional[str],
) -> str:
    """Stream selector plus optional line filter pipeline."""
    return build_loki_stream_selector(namespace, deployment, tier) + build_loki_line_filter_pipeline(
        search,
    )


def encode_logs_cursor_v1(ts_ns: int, entry_id: str) -> str:
    raw = json.dumps({"v": 1, "t": ts_ns, "i": entry_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_logs_cursor_v1(token: str) -> tuple[int, str]:
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode((token + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if not isinstance(obj, dict) or obj.get("v") != 1:
        raise ValueError("invalid cursor")
    ts = obj.get("t")
    eid = obj.get("i")
    if not isinstance(ts, int) or not isinstance(eid, str):
        raise ValueError("invalid cursor")
    return ts, eid


def ms_exclusive_after_cursor(ts_ns: int) -> int:
    """Advance at least one millisecond past ``ts_ns`` for the next Loki window."""
    return int(ts_ns // 1_000_000) + 1


def ms_upper_bound_before_cursor(ts_ns: int) -> int:
    """Upper ``to`` millisecond (exclusive) for the next backward page (older than ``ts_ns``)."""
    return int(ts_ns // 1_000_000) - 1


def build_loki_stream_selector(
    namespace: str,
    deployment: str,
    tier: Optional[int],
) -> str:
    """
    Build a LogQL stream selector `{label="value",...}` scoped to one workload.

    Label keys default to Kubernetes-style ``namespace`` and ``deployment``;
    override with GRAFANA_LOKI_NAMESPACE_LABEL, GRAFANA_LOKI_DEPLOYMENT_LABEL.
    Optional GRAFANA_LOKI_TIER_LABEL + numeric ``tier`` add a tier matcher
    (value ``tier1`` … ``tier4``) when the env var is set.
    """
    ns_key = (os.getenv("GRAFANA_LOKI_NAMESPACE_LABEL") or "namespace").strip() or "namespace"
    dep_key = (os.getenv("GRAFANA_LOKI_DEPLOYMENT_LABEL") or "deployment").strip() or "deployment"
    parts = [
        f'{ns_key}="{escape_logql_label_value(namespace)}"',
        f'{dep_key}="{escape_logql_label_value(deployment)}"',
    ]
    tier_label = (os.getenv("GRAFANA_LOKI_TIER_LABEL") or "").strip()
    if tier_label and tier is not None and 1 <= tier <= 4:
        tier_val = f"tier{tier}"
        parts.append(f'{tier_label}="{escape_logql_label_value(tier_val)}"')
    return "{" + ",".join(parts) + "}"


def _coerce_labels_cell(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if v is not None}
    return {}


def _field_indices(fields: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, f in enumerate(fields):
        name = (f.get("name") or "").strip().lower()
        if name and name not in out:
            out[name] = i
    return out


def _time_ns_from_cell(cell: Any) -> Optional[int]:
    if cell is None:
        return None
    if isinstance(cell, int):
        n = cell
    elif isinstance(cell, float):
        n = int(cell)
    else:
        return None
    # Heuristic: Grafana Loki uses nanoseconds; ms timestamps are ~1e12.
    if n > 10**15:
        return n
    if n > 10**12:
        return int(n * 10**6)
    return int(n * 10**9)


def _iso_from_ns(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def iter_loki_rows_from_ds_query_payload(
    payload: dict[str, Any],
) -> Iterator[tuple[int, str, dict[str, str]]]:
    """
    Yield (timestamp_ns, line, merged_labels) for each log line in a Grafana
    /api/ds/query JSON body (Loki datasource).
    """
    results = payload.get("results")
    if not isinstance(results, dict):
        return
    for _ref_id, block in results.items():
        if not isinstance(block, dict):
            continue
        frames = block.get("frames")
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            schema = frame.get("schema")
            data = frame.get("data")
            if not isinstance(schema, dict) or not isinstance(data, dict):
                continue
            fields_raw = schema.get("fields")
            values = data.get("values")
            if not isinstance(fields_raw, list) or not isinstance(values, list):
                continue
            fields = [f for f in fields_raw if isinstance(f, dict)]
            if not fields or not values:
                continue
            idx = _field_indices(fields)
            time_idx = idx.get("time")
            if time_idx is None:
                time_idx = idx.get("tsns")
            line_idx = idx.get("line")
            labels_idx = idx.get("labels")
            id_idx = idx.get("id")

            if time_idx is None or line_idx is None:
                continue
            time_col = values[time_idx] if time_idx < len(values) else []
            line_col = values[line_idx] if line_idx < len(values) else []
            labels_col: list[Any] = []
            if labels_idx is not None and labels_idx < len(values):
                labels_col = values[labels_idx]  # type: ignore[assignment]
            id_col: list[Any] = []
            if id_idx is not None and id_idx < len(values):
                id_col = values[id_idx]  # type: ignore[assignment]

            base_labels: dict[str, str] = {}
            for fdef in fields:
                embedded = fdef.get("labels")
                if isinstance(embedded, dict):
                    base_labels.update(
                        {str(k): str(v) for k, v in embedded.items() if v is not None}
                    )

            n = min(len(time_col), len(line_col))
            for i in range(n):
                ts = _time_ns_from_cell(time_col[i])
                if ts is None:
                    continue
                line = line_col[i]
                if not isinstance(line, str):
                    line = str(line)
                row_labels = dict(base_labels)
                if i < len(labels_col):
                    row_labels.update(_coerce_labels_cell(labels_col[i]))
                if i < len(id_col) and id_col[i] is not None:
                    row_labels.setdefault("_grafana_line_id", str(id_col[i]))
                yield ts, line, row_labels


_JSON_LEVEL_KEYS = ("level", "severity", "log_level", "logLevel")


def infer_level_from_line_and_labels(
    line: str,
    labels: dict[str, str],
) -> LogLevelLiteral:
    """Best-effort level: JSON body, syslog prefix, then INFO."""
    stripped = line.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                for key in _JSON_LEVEL_KEYS:
                    raw = obj.get(key)
                    if isinstance(raw, str):
                        lv = _LEVEL_ALIASES.get(raw.strip().lower())
                        if lv:
                            return lv
        except json.JSONDecodeError:
            pass
    for lbl_key in ("level", "severity", "detected_level"):
        raw = labels.get(lbl_key)
        if raw and raw != "unknown":
            lv = _LEVEL_ALIASES.get(str(raw).strip().lower())
            if lv:
                return lv
    # ANSI SGR (e.g. \x1b[32mINFO) leaves no \b before INFO; strip first.
    plain = _strip_ansi(line)
    m = re.search(
        r"\b(DEBUG|TRACE|INFO|INFORMATION|WARN|WARNING|ERR|ERROR|FATAL|CRITICAL|CRIT)\b",
        plain,
        re.IGNORECASE,
    )
    if m:
        token = m.group(1).lower()
        if token in ("information",):
            return "info"
        if token in ("warn", "warning"):
            return "warning"
        if token in ("err", "error"):
            return "error"
        if token in ("crit", "critical"):
            return "critical"
        mapped = _LEVEL_ALIASES.get(token)
        if mapped:
            return mapped
    return "info"


_DURATION_MS_RE = re.compile(
    r"(?:duration|elapsed|latency)[:\s=]+(\d+(?:\.\d+)?)\s*(ms|msec|milliseconds?)",
    re.IGNORECASE,
)


def _maybe_duration_ms(line: str) -> int | None:
    m = _DURATION_MS_RE.search(line)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return int(round(val))


def _stable_entry_id(ts_ns: int, line: str, labels: dict[str, str]) -> str:
    # Omit seq and internal labels so Grafana/Loki duplicate rows of the same line
    # (e.g. repeated frames) share one id and the UI can dedupe.
    stable = {k: v for k, v in labels.items() if not str(k).startswith("_")}
    label_part = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    raw = f"{ts_ns}|{label_part}|{line}".encode()
    return hashlib.sha256(raw).hexdigest()


def loki_row_to_deployment_entry(
    ts_ns: int,
    line: str,
    labels: dict[str, str],
    deployment_name: str,
) -> DeploymentLogEntryOut:
    level: LogLevelLiteral = infer_level_from_line_and_labels(line, labels)
    container = labels.get("container") or labels.get("container_name") or "log"
    pod = labels.get("pod") or ""
    namespace = labels.get("namespace") or ""
    dep = (
        labels.get("deployment")
        or labels.get("app")
        or labels.get("app_kubernetes_io_name")
        or deployment_name
    )
    entry_id = _stable_entry_id(ts_ns, line, labels)
    conn = labels.get("_grafana_line_id") or pod or entry_id[:16]
    duration_ms = _maybe_duration_ms(line)
    public_labels = {k: v for k, v in labels.items() if not str(k).startswith("_")}
    return DeploymentLogEntryOut(
        id=entry_id,
        timestamp=_iso_from_ns(ts_ns),
        level=level,
        logType=container,
        user="—",
        db=namespace or "—",
        app=dep or deployment_name,
        client=pod or "—",
        role="—",
        durationMs=duration_ms,
        connectionId=conn,
        message=line,
        labels=public_labels,
    )
