"""Authenticated endpoint for stored historical financial summaries."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.crud.financial import get_financial_cost_summary
from c2ai.db.session import get_db_session
from c2ai.schemas.financial import (
    FinancialApplicationCostOut,
    FinancialCostsOut,
    FinancialCostType,
    FinancialFilterOut,
    FinancialTierCostOut,
)

router = APIRouter()


def resolve_financial_dates(
    start_date: date | None,
    end_date: date | None,
    *,
    reference_date: date | None = None,
) -> tuple[date, date]:
    """Resolve an explicit range or the current calendar month to date."""
    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date and end_date must be provided together",
        )

    if start_date is None and end_date is None:
        today = reference_date or datetime.now(tz=UTC).date()
        return today.replace(day=1), today

    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date",
        )
    return start_date, end_date


def _inclusive_dates_to_utc_period(
    start_date: date,
    end_date: date,
) -> tuple[datetime, datetime]:
    """Convert inclusive dates into a half-open UTC datetime period."""
    try:
        exclusive_end_date = end_date + timedelta(days=1)
    except OverflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date is outside the supported range",
        ) from exc
    return (
        datetime.combine(start_date, time.min, tzinfo=UTC),
        datetime.combine(exclusive_end_date, time.min, tzinfo=UTC),
    )


@router.get(
    "/api/financial/costs",
    response_model=FinancialCostsOut,
    status_code=status.HTTP_200_OK,
    summary="Stored financial costs for the cluster or one tier",
)
async def get_financial_costs(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    cost_type: FinancialCostType = Query(default=FinancialCostType.BOTH),
    tier: int | None = Query(default=None, ge=1, le=4),
    source_namespace: str | None = Query(default=None, max_length=253),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db_session),
) -> FinancialCostsOut:
    """Return one consistently filtered summary without recalculating old costs."""
    resolved_start, resolved_end = resolve_financial_dates(start_date, end_date)
    period_start, period_end = _inclusive_dates_to_utc_period(
        resolved_start,
        resolved_end,
    )
    stored_cost_type = None if cost_type is FinancialCostType.BOTH else cost_type.value
    resolved_namespace = None
    if source_namespace is not None:
        resolved_namespace = source_namespace.strip()
        if not resolved_namespace:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="source_namespace must not be empty",
            )
    summary = await get_financial_cost_summary(
        db,
        period_start=period_start,
        period_end=period_end,
        cost_type=stored_cost_type,
        tier=tier,
        source_namespace=resolved_namespace,
    )

    return FinancialCostsOut(
        filters=FinancialFilterOut(
            start_date=resolved_start,
            end_date=resolved_end,
            cost_type=cost_type,
            tier=tier,
            source_namespace=resolved_namespace,
        ),
        total_cost=summary.total_cost,
        tiers=[
            FinancialTierCostOut(tier=tier_number, cost=cost)
            for tier_number, cost in summary.tier_costs.items()
        ],
        applications=[
            FinancialApplicationCostOut(
                application_key=application.application_key,
                tier=application.tier,
                cost=application.cost,
            )
            for application in summary.application_costs
        ],
    )
