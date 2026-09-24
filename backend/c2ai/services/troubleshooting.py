"""Collection, grounding, and model parsing for troubleshooting reports."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import SystemMessage
from pydantic import ValidationError

from c2ai.llm.model_switcher import extract_response_text
from c2ai.llm.prompts import build_troubleshooting_prompt
from c2ai.schemas.troubleshooting import (
    TroubleshootingEvent,
    TroubleshootingMetricQuery,
    TroubleshootingModelOutput,
)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
logger = logging.getLogger(__name__)

_ACTION_CORRECTION_PROMPT = SystemMessage(
    content=(
        "Your previous troubleshooting response did not satisfy the required JSON "
        "schema. Return the complete JSON object again with exactly 4 distinct, "
        "non-empty recommended_actions ordered by priority. Return JSON only."
    )
)


def select_troubleshooting_events(
    events: list[TroubleshootingEvent],
    *,
    limit: int = 100,
) -> list[TroubleshootingEvent]:
    """Return the newest unique provider events, ordered chronologically."""

    events_by_id = {event.id: event for event in events}
    newest = sorted(
        events_by_id.values(),
        key=lambda event: (event.timestamp, event.id),
        reverse=True,
    )[:limit]
    return sorted(newest, key=lambda event: (event.timestamp, event.id))


def parse_troubleshooting_model_output(raw: str) -> TroubleshootingModelOutput:
    """Extract and validate the single JSON object returned by the model."""

    candidate = _JSON_FENCE_RE.sub("", raw.strip()).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model response did not contain a JSON object.")
    try:
        data = json.loads(candidate[start : end + 1])
        return TroubleshootingModelOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(
            "The model returned an invalid troubleshooting report."
        ) from exc


def _fallback_recommended_actions(
    events: list[TroubleshootingEvent],
) -> list[str]:
    """Build conservative actions grounded in the collected evidence."""

    event = max(
        events,
        key=lambda item: (item.timestamp, item.id),
    )
    condition = event.reason or event.event_type
    return [
        f"Inspect {event.resource} around {event.timestamp.isoformat()} and verify the recorded {condition} condition.",
        "Correlate the application logs and Kubernetes events across the analysis window to identify the first recurring failure signal.",
        "Verify the affected deployment configuration, dependencies, and health checks before applying a corrective change.",
        "After the change, regenerate this report and confirm that the selected Prometheus metrics and failure events recover.",
    ]


def _parse_with_action_fallback(
    raw: str,
    fallback_actions: list[str],
) -> TroubleshootingModelOutput:
    """Repair only a missing or incomplete actions list in otherwise valid JSON."""

    candidate = _JSON_FENCE_RE.sub("", raw.strip()).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model response did not contain a JSON object.")
    try:
        data = json.loads(candidate[start : end + 1])
        if not isinstance(data, dict):
            raise ValueError("The model response was not a JSON object.")
        supplied_actions = data.get("recommended_actions")
        actions = (
            [
                action.strip()
                for action in supplied_actions
                if isinstance(action, str) and action.strip()
            ]
            if isinstance(supplied_actions, list)
            else []
        )
        for action in fallback_actions:
            if action not in actions:
                actions.append(action)
            if len(actions) == 4:
                break
        data["recommended_actions"] = actions[:4]
        return TroubleshootingModelOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(
            "The model returned an invalid troubleshooting report."
        ) from exc


async def analyse_events(
    llm: Any,
    *,
    subdomain: str,
    deployment: str,
    events: list[TroubleshootingEvent],
    available_metric_queries: list[TroubleshootingMetricQuery],
) -> TroubleshootingModelOutput:
    """Ask the model for conclusions and source event IDs."""

    prompt = build_troubleshooting_prompt(
        subdomain=subdomain,
        deployment=deployment,
        events=events,
        available_metric_queries=available_metric_queries,
    )
    response = await llm.ainvoke([prompt])
    try:
        return parse_troubleshooting_model_output(extract_response_text(response))
    except ValueError:
        logger.warning("Retrying invalid troubleshooting model output once.")

    corrected_response = await llm.ainvoke([prompt, _ACTION_CORRECTION_PROMPT])
    corrected_text = extract_response_text(corrected_response)
    try:
        return parse_troubleshooting_model_output(corrected_text)
    except ValueError:
        logger.warning(
            "Troubleshooting model retry was invalid; applying action fallback."
        )
        return _parse_with_action_fallback(
            corrected_text,
            _fallback_recommended_actions(events),
        )
