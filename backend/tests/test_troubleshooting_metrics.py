"""Tests for LLM-selected PromQL from the configured JSON catalog."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from c2ai.schemas.grafana import GrafanaResponse
from c2ai.services.troubleshooting_metrics import (
    load_configured_metric_queries,
    query_application_metrics,
    select_available_metric_queries,
)


class FakeMetricClient:
    def __init__(self):
        self.body = None

    async def fetch_grafana_query(self, body: dict) -> GrafanaResponse:
        self.body = body
        start_ms = int(body["from"])
        end_ms = int(body["to"])
        midpoint_ms = start_ms + (end_ms - start_ms) // 2
        return GrafanaResponse.model_validate(
            {
                "results": {
                    query["refId"]: {
                        "status": 200,
                        "frames": [
                            {
                                "schema": {
                                    "refId": query["refId"],
                                    "fields": [
                                        {"name": "Time", "type": "time"},
                                        {"name": "Value", "type": "number"},
                                    ],
                                },
                                "data": {
                                    "values": [
                                        [start_ms, midpoint_ms, end_ms],
                                        [
                                            index + 0.100001,
                                            index + 0.200002,
                                            index + 0.300003,
                                        ],
                                    ],
                                },
                            }
                        ],
                    }
                    for index, query in enumerate(body["queries"])
                }
            }
        )


def _configured_queries(*, tier: int | None = 1):
    end = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    return load_configured_metric_queries(
        namespace="amberd-acme-ada",
        deployment="ada",
        tier=tier,
        start=end - timedelta(hours=4),
        end=end,
    )


def test_catalog_uses_query_ids_without_version_or_duplicate_names():
    catalog_path = (
        Path(__file__).parents[1]
        / "c2ai"
        / "constants"
        / "troubleshooting_metric_queries.json"
    )
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert "version" not in payload
    assert all("id" in metric for metric in payload["metrics"])
    assert all("name" not in metric for metric in payload["metrics"])


def test_loads_all_27_configured_queries_and_resolves_instance_variables():
    queries = _configured_queries()

    assert len(queries) == 27
    assert queries[0].id == "desired_replicas"
    assert queries[0].label == "Desired Replicas"
    assert queries[-1].id == "llm_output_tokens_per_call_by_application"
    assert all("$namespace" not in query.expr for query in queries)
    assert all("$app" not in query.expr for query in queries)
    assert all("$deployment" not in query.expr for query in queries)
    assert all("$tier" not in query.expr for query in queries)
    assert all("${ray_cluster:regex}" not in query.expr for query in queries)
    assert 'namespace=~"amberd-acme-ada"' in queries[0].expr
    assert 'label_app=~"ada"' in queries[0].expr
    assert 'label_tier=~"tier1"' in queries[0].expr
    gpu_query = next(query for query in queries if query.id == "tier_gpu_utilization_ray")
    assert 'ray_io_cluster=~"qwen-5254d"' in gpu_query.expr
    memory_query = next(query for query in queries if query.id == "memory_usage")
    assert memory_query.unit == "GiB"
    assert memory_query.value_divisor == 1024**3


def test_uses_all_tiers_and_ray_clusters_when_tier_is_unknown():
    queries = _configured_queries(tier=None)

    assert 'label_tier=~"tier1|tier2|tier3"' in queries[0].expr
    gpu_query = next(query for query in queries if query.id == "tier_gpu_utilization_ray")
    assert 'ray_io_cluster=~"qwen-5254d|qwen-pq9sc|qwen-l8dnl"' in (
        gpu_query.expr
    )


def test_rejects_an_invalid_or_unresolved_catalog(tmp_path: Path):
    invalid_catalog = tmp_path / "queries.json"
    invalid_catalog.write_text(
        json.dumps(
            {
                "metrics": [
                    {
                        "id": "bad",
                        "label": "Bad",
                        "expr": ["sum($unknown_metric)"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    end = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="unresolved variables"):
        load_configured_metric_queries(
            namespace="amberd-acme-ada",
            deployment="ada",
            tier=1,
            start=end - timedelta(hours=4),
            end=end,
            catalog_path=invalid_catalog,
        )


def test_metric_selection_only_accepts_ids_from_configured_catalog():
    available = _configured_queries()

    selected = select_available_metric_queries(
        [available[2].id, "invented-query-id", available[3].id],
        available,
    )

    assert [query.id for query in selected] == ["cpu_usage", "memory_usage"]


async def test_executes_four_exact_model_selected_configured_queries():
    client = FakeMetricClient()
    end = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=4)
    queries = _configured_queries()[2:6]

    metrics = await query_application_metrics(
        client,
        queries=queries,
        start=start,
        end=end,
    )

    assert [metric.name for metric in metrics] == [query.id for query in queries]
    assert metrics[0].value == 0.300003
    assert metrics[1].value == (1 + 0.300003) / (1024**3)
    assert metrics[2].value == 2 + 0.300003
    assert metrics[3].value == 3 + 0.300003
    assert metrics[0].points[-1].value == 0.300003
    assert metrics[1].points[-1].value == (1 + 0.300003) / (1024**3)
    assert metrics[1].unit == "GiB"
    assert all(len(metric.points) == 3 for metric in metrics)
    assert client.body is not None
    assert [query["expr"] for query in client.body["queries"]] == [
        query.expr for query in queries
    ]
    assert all(query["range"] is True for query in client.body["queries"])
    assert all(query["instant"] is False for query in client.body["queries"])
    assert all(query["maxDataPoints"] == 120 for query in client.body["queries"])


async def test_does_not_call_grafana_when_no_query_was_selected():
    client = FakeMetricClient()
    end = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    metrics = await query_application_metrics(
        client,
        queries=[],
        start=end - timedelta(hours=4),
        end=end,
    )

    assert metrics == []
    assert client.body is None
