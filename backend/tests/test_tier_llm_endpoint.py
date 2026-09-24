"""Routing a registered LLM endpoint to the gateway of the deployment Tier."""

import pytest

from c2ai.deployments.configuration import (
    resolve_tier_llm_endpoint,
)


@pytest.mark.parametrize(
    ("endpoint", "tier", "expected"),
    [
        (
            "https://amberd-llm-gateway:8010",
            1,
            "https://amberd-llm-gateway.tier1.svc:8010",
        ),
        (
            "https://amberd-llm-gateway:8010",
            2,
            "https://amberd-llm-gateway.tier2.svc:8010",
        ),
        ("amberd-llm-gateway:8010", 3, "amberd-llm-gateway.tier3.svc:8010"),
        (
            " http://amberd-llm-gateway:8010/v1 ",
            4,
            "http://amberd-llm-gateway.tier4.svc:8010/v1",
        ),
    ],
)
def test_service_endpoint_is_routed_to_the_tier_namespace(endpoint, tier, expected):
    assert resolve_tier_llm_endpoint(endpoint, tier) == expected


def test_an_existing_namespace_is_replaced_by_the_tier():
    assert (
        resolve_tier_llm_endpoint("https://amberd-llm-gateway.qwen.svc:8010", 2)
        == "https://amberd-llm-gateway.tier2.svc:8010"
    )
    assert (
        resolve_tier_llm_endpoint(
            "https://amberd-llm-gateway.tier1.svc.cluster.local:8010", 3
        )
        == "https://amberd-llm-gateway.tier3.svc.cluster.local:8010"
    )


@pytest.mark.parametrize(
    "endpoint",
    ["https://api.openai.com/v1", "https://llm.example.com/v1"],
)
def test_external_endpoints_are_sent_as_registered(endpoint):
    assert resolve_tier_llm_endpoint(endpoint, 1) == endpoint
