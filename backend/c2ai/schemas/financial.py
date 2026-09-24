"""API schemas for historical financial cost summaries."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class FinancialCostType(str, Enum):
    """Cost-source filter accepted by the financial summary endpoint."""

    PRIVATE_LLM = "private_llm"
    PUBLIC_API = "public_api"
    BOTH = "both"


class FinancialFilterOut(BaseModel):
    """The resolved filter applied consistently to the whole response."""

    start_date: date
    end_date: date
    cost_type: FinancialCostType
    tier: int | None = Field(default=None, ge=1, le=4)
    source_namespace: str | None = None


class FinancialTierCostOut(BaseModel):
    """Aggregated historical cost for one tier."""

    tier: int = Field(ge=1, le=4)
    cost: Decimal


class FinancialApplicationCostOut(BaseModel):
    """Aggregated historical cost for one application in the selected tier."""

    application_key: str
    tier: int = Field(ge=1, le=4)
    cost: Decimal


class FinancialCostsOut(BaseModel):
    """Cluster or tier financial summary for one shared filter."""

    filters: FinancialFilterOut
    currency: str = "USD"
    total_cost: Decimal
    tiers: list[FinancialTierCostOut]
    applications: list[FinancialApplicationCostOut]
