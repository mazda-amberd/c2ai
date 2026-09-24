"""API tests for the shared historical financial filter and summaries."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from c2ai.api.financial import resolve_financial_dates
from c2ai.crud.financial import (
    ApplicationCostTotal,
    FinancialCostSummary,
)


def test_default_financial_period_is_current_calendar_month_to_date():
    assert resolve_financial_dates(None, None, reference_date=date(2026, 8, 25)) == (
        date(2026, 8, 1),
        date(2026, 8, 25),
    )


def test_financial_endpoint_requires_authentication(test_client):
    response = test_client.get(
        "/api/financial/costs",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
    )

    assert response.status_code == 401


def test_cluster_financial_endpoint_uses_one_shared_filter(deploy_auth_client):
    summary = FinancialCostSummary(
        total_cost=Decimal("12.50000000"),
        tier_costs={
            1: Decimal("10.00000000"),
            2: Decimal("2.50000000"),
            3: Decimal("0.00000000"),
            4: Decimal("0.00000000"),
        },
        application_costs=(),
    )

    with patch(
        "c2ai.api.financial.get_financial_cost_summary",
        new_callable=AsyncMock,
        return_value=summary,
    ) as aggregate:
        response = deploy_auth_client.get(
            "/api/financial/costs",
            params={
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
                "cost_type": "both",
                "source_namespace": "amberd-test-deploy",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["filters"] == {
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "cost_type": "both",
        "tier": None,
        "source_namespace": "amberd-test-deploy",
    }
    assert body["total_cost"] == "12.50000000"
    assert body["applications"] == []
    assert [item["tier"] for item in body["tiers"]] == [1, 2, 3, 4]

    call_kwargs = aggregate.await_args.kwargs
    assert call_kwargs["period_start"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert call_kwargs["period_end"] == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert call_kwargs["cost_type"] is None
    assert call_kwargs["tier"] is None
    assert call_kwargs["source_namespace"] == "amberd-test-deploy"


def test_tier_financial_endpoint_returns_application_costs(deploy_auth_client):
    summary = FinancialCostSummary(
        total_cost=Decimal("7.00000000"),
        tier_costs={2: Decimal("7.00000000")},
        application_costs=(
            ApplicationCostTotal(
                application_key="customer-app",
                tier=2,
                cost=Decimal("7.00000000"),
            ),
        ),
    )

    with patch(
        "c2ai.api.financial.get_financial_cost_summary",
        new_callable=AsyncMock,
        return_value=summary,
    ):
        response = deploy_auth_client.get(
            "/api/financial/costs",
            params={
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
                "cost_type": "private_llm",
                "tier": 2,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tiers"] == [{"tier": 2, "cost": "7.00000000"}]
    assert body["applications"] == [
        {"application_key": "customer-app", "tier": 2, "cost": "7.00000000"}
    ]


def test_financial_endpoint_rejects_partial_or_reversed_dates(deploy_auth_client):
    partial = deploy_auth_client.get(
        "/api/financial/costs",
        params={"start_date": "2026-08-01"},
    )
    reversed_range = deploy_auth_client.get(
        "/api/financial/costs",
        params={"start_date": "2026-08-31", "end_date": "2026-08-01"},
    )

    assert partial.status_code == 422
    assert reversed_range.status_code == 422


def test_financial_endpoint_rejects_blank_source_namespace(deploy_auth_client):
    response = deploy_auth_client.get(
        "/api/financial/costs",
        params={"source_namespace": "   "},
    )

    assert response.status_code == 422
