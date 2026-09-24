"""Unit tests for LogQL builder and Loki payload parsing."""

from __future__ import annotations

from c2ai.services.loki_logs import (
    build_full_loki_logql,
    build_loki_line_filter_pipeline,
    build_loki_stream_selector,
    decode_logs_cursor_v1,
    encode_logs_cursor_v1,
    infer_level_from_line_and_labels,
    iter_loki_rows_from_ds_query_payload,
    loki_row_to_deployment_entry,
    ms_exclusive_after_cursor,
    ms_upper_bound_before_cursor,
)


def test_build_loki_stream_selector_default_labels(monkeypatch):
    monkeypatch.delenv("GRAFANA_LOKI_NAMESPACE_LABEL", raising=False)
    monkeypatch.delenv("GRAFANA_LOKI_DEPLOYMENT_LABEL", raising=False)
    monkeypatch.delenv("GRAFANA_LOKI_TIER_LABEL", raising=False)
    s = build_loki_stream_selector("ns-one", "my-workload", None)
    assert s == '{namespace="ns-one",deployment="my-workload"}'


def test_build_loki_stream_selector_tier_label(monkeypatch):
    monkeypatch.setenv("GRAFANA_LOKI_TIER_LABEL", "label_tier")
    s = build_loki_stream_selector("ns-one", "app", 2)
    assert 'label_tier="tier2"' in s


def test_infer_level_json():
    assert (
        infer_level_from_line_and_labels('{"level":"warning","x":1}', {})
        == "warning"
    )


def test_infer_level_ansi_wrapped_token():
    """SGR codes break \\b before level; strip first (typical Python/k8s colored logs)."""
    line = "2026-04-23 11:10:28 - \x1b[32mINFO\x1b[0m: Scheduler cycle completed\n"
    assert infer_level_from_line_and_labels(line, {}) == "info"


def test_infer_level_detected_level_label():
    assert (
        infer_level_from_line_and_labels("anything", {"detected_level": "error"})
        == "error"
    )
    assert infer_level_from_line_and_labels("x", {"detected_level": "unknown"}) == "info"


def test_loki_entry_id_ignores_sequence_grafana_duplicates():
    """Same ts/line/labels from repeated frames must share an id (UI dedupes on id)."""
    ts = 1_000_000_000_000_000_000
    line = "Converting bookmark to dictionary."
    labels = {"app": "ada", "namespace": "ns1", "pod": "p1", "_x": "1"}
    a = loki_row_to_deployment_entry(ts, line, labels, "ada")
    b = loki_row_to_deployment_entry(ts, line, {**labels, "_x": "2"}, "ada")
    assert a.id == b.id


def test_iter_loki_rows_minimal():
    payload = {
        "results": {
            "A": {
                "status": 200,
                "frames": [
                    {
                        "schema": {
                            "refId": "A",
                            "fields": [
                                {"name": "Time", "type": "time"},
                                {"name": "Line", "type": "string"},
                            ],
                        },
                        "data": {
                            "values": [
                                [1_000_000_000_000_000_000],
                                ["hello"],
                            ],
                        },
                    }
                ],
            }
        }
    }
    rows = list(iter_loki_rows_from_ds_query_payload(payload))
    assert len(rows) == 1
    assert rows[0][0] == 1_000_000_000_000_000_000
    assert rows[0][1] == "hello"
    assert rows[0][2] == {}


def test_build_loki_line_filter_pipeline_text_tokens():
    pipe = build_loki_line_filter_pipeline("hello world")
    assert pipe.startswith(" ")
    assert '|= "hello"' in pipe
    assert '|= "world"' in pipe


def test_build_loki_line_filter_pipeline_level_tokens():
    pipe = build_loki_line_filter_pipeline("level:error level:info")
    assert "|~" in pipe
    assert "ERR" in pipe


def test_build_full_loki_logql_includes_pipeline():
    expr = build_full_loki_logql("ns", "app", None, "boom")
    assert expr.startswith("{")
    assert "boom" in expr


def test_logs_cursor_roundtrip():
    ts = 1_704_067_200_000_000_001
    tok = encode_logs_cursor_v1(ts, "abc")
    assert decode_logs_cursor_v1(tok) == (ts, "abc")


def test_ms_exclusive_after_cursor():
    assert ms_exclusive_after_cursor(1_704_067_200_000_000_000) == 1_704_067_200_000 + 1


def test_ms_upper_bound_before_cursor():
    assert ms_upper_bound_before_cursor(1_704_067_200_000_000_000) == 1_704_067_200_000 - 1
