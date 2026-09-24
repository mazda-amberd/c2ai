"""Unit tests for troubleshooting output validation."""

import json
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage

from c2ai.schemas.troubleshooting import TroubleshootingEvent
from c2ai.services.troubleshooting import (
    analyse_events,
    parse_troubleshooting_model_output,
)
from c2ai.services.troubleshooting_metrics import load_configured_metric_queries


def _metric_queries():
    return load_configured_metric_queries(
        namespace="amberd-acme-ada",
        deployment="ada",
        tier=1,
        start=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    )[:4]


def _model_json(actions: list[str]) -> str:
    query_ids = [query.id for query in _metric_queries()]
    return json.dumps(
        {
            "severity": "warning",
            "summary": "A failure occurred.",
            "most_likely_root_cause": "Dependency unavailable.",
            "issue_started_event_id": "one",
            "most_critical_event_id": "one",
            "recommended_actions": actions,
            "recommended_metric_query_ids": query_ids,
            "relevant_event_ids": ["one"],
            "relevant_cluster_event_ids": [],
        }
    )


def _event() -> TroubleshootingEvent:
    return TroubleshootingEvent(
        id="one",
        timestamp=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        source="application",
        severity="error",
        resource="deployment/ada",
        event_type="application-log",
        reason="ConnectionRefused",
        message="Dependency connection failed.",
    )


class SequenceLLM:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def ainvoke(self, _messages):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return AIMessage(content=response)


def test_parse_troubleshooting_model_output_accepts_json_fence():
    result = parse_troubleshooting_model_output(
        """```json
        {
          "severity": "Warning",
          "summary": "A failure occurred.",
          "most_likely_root_cause": "Dependency unavailable.",
          "issue_started_event_id": "one",
          "most_critical_event_id": "two",
          "recommended_actions": [
            "Check the dependency.",
            "Verify connectivity.",
            "Restart the affected worker.",
            "Monitor recovery."
          ],
          "recommended_metric_query_ids": ["promql-query-one"],
          "relevant_event_ids": ["one", "two"]
        }
        ```"""
    )
    assert result.most_critical_event_id == "two"
    assert result.severity == "warning"


def test_parse_troubleshooting_model_output_rejects_non_json():
    with pytest.raises(ValueError, match="did not contain"):
        parse_troubleshooting_model_output("I think the service is down")


def test_parse_troubleshooting_model_output_requires_actions():
    with pytest.raises(ValueError, match="invalid troubleshooting report"):
        parse_troubleshooting_model_output(
            """{
              "severity": "warning",
              "summary": "A failure occurred.",
              "most_likely_root_cause": "Unknown.",
              "recommended_actions": [],
              "relevant_event_ids": []
            }"""
        )


async def test_analyse_events_retries_empty_actions_once():
    corrected_actions = [f"Corrected action {index}" for index in range(4)]
    llm = SequenceLLM([_model_json([]), _model_json(corrected_actions)])

    result = await analyse_events(
        llm,
        subdomain="amberd-acme-ada",
        deployment="ada",
        events=[_event()],
        available_metric_queries=_metric_queries(),
    )

    assert llm.calls == 2
    assert result.recommended_actions == corrected_actions


async def test_analyse_events_uses_grounded_actions_when_retry_is_empty():
    llm = SequenceLLM([_model_json([]), _model_json([])])

    result = await analyse_events(
        llm,
        subdomain="amberd-acme-ada",
        deployment="ada",
        events=[_event()],
        available_metric_queries=_metric_queries(),
    )

    assert llm.calls == 2
    assert len(result.recommended_actions) == 4
    assert "deployment/ada" in result.recommended_actions[0]


def test_parse_troubleshooting_model_output_caps_oversized_lists():
    event_ids = [f"event-{index}" for index in range(45)]
    actions = [f"Action {index}" for index in range(15)]
    metric_query_ids = [f"query-{index}" for index in range(10)]
    cluster_event_ids = [f"cluster-{index}" for index in range(12)]

    result = parse_troubleshooting_model_output(
        json.dumps(
            {
                "severity": "critical",
                "summary": "A failure occurred.",
                "most_likely_root_cause": "Dependency unavailable.",
                "recommended_actions": actions,
                "recommended_metric_query_ids": metric_query_ids,
                "relevant_event_ids": event_ids,
                "relevant_cluster_event_ids": cluster_event_ids,
            }
        )
    )

    assert result.relevant_event_ids == event_ids[:20]
    assert result.recommended_actions == actions[:4]
    assert result.recommended_metric_query_ids == metric_query_ids[:4]
    assert result.relevant_cluster_event_ids == cluster_event_ids[:10]
