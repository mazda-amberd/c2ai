# pylint: disable=import-error
"""SQLAlchemy models for effective-dated rates and historical cost records."""

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.sql import func

from .base import Base


class FinancialRate(Base):
    """A public-token or private-GPU rate that applies during a time range."""

    __tablename__ = "financial_rates"
    __table_args__ = (
        CheckConstraint(
            "cost_type IN ('public_api', 'private_llm')",
            name="ck_financial_rates_cost_type",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_financial_rates_effective_period",
        ),
        CheckConstraint(
            "currency = upper(currency) AND char_length(currency) = 3",
            name="ck_financial_rates_currency",
        ),
        CheckConstraint(
            "(input_rate_per_million_tokens IS NULL OR input_rate_per_million_tokens >= 0) "
            "AND (output_rate_per_million_tokens IS NULL OR output_rate_per_million_tokens >= 0) "
            "AND (gpu_hourly_rate IS NULL OR gpu_hourly_rate >= 0)",
            name="ck_financial_rates_non_negative",
        ),
        CheckConstraint(
            "(cost_type = 'public_api' AND provider IS NOT NULL AND model_name IS NOT NULL "
            "AND resource_type IS NULL AND input_rate_per_million_tokens IS NOT NULL "
            "AND output_rate_per_million_tokens IS NOT NULL AND gpu_hourly_rate IS NULL) "
            "OR (cost_type = 'private_llm' AND provider IS NULL AND model_name IS NULL "
            "AND resource_type IS NOT NULL AND input_rate_per_million_tokens IS NULL "
            "AND output_rate_per_million_tokens IS NULL AND gpu_hourly_rate IS NOT NULL)",
            name="ck_financial_rates_shape",
        ),
        Index(
            "uq_financial_rates_current_public",
            "provider",
            "model_name",
            unique=True,
            postgresql_where="cost_type = 'public_api' AND effective_to IS NULL",
        ),
        Index(
            "uq_financial_rates_current_private",
            "resource_type",
            unique=True,
            postgresql_where="cost_type = 'private_llm' AND effective_to IS NULL",
        ),
        Index(
            "ix_financial_rates_effective_period",
            "cost_type",
            "effective_from",
            "effective_to",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    cost_type = Column(String, nullable=False)
    provider = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    resource_type = Column(String, nullable=True)
    input_rate_per_million_tokens = Column(Numeric(20, 8), nullable=True)
    output_rate_per_million_tokens = Column(Numeric(20, 8), nullable=True)
    gpu_hourly_rate = Column(Numeric(20, 8), nullable=True)
    currency = Column(String(3), nullable=False, default="USD", server_default="USD")
    effective_from = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    effective_to = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FinancialCostRecord(Base):
    """One immutable cost amount for an application and usage time interval."""

    __tablename__ = "financial_cost_records"
    __table_args__ = (
        CheckConstraint(
            "cost_type IN ('public_api', 'private_llm')",
            name="ck_financial_cost_records_cost_type",
        ),
        CheckConstraint("tier BETWEEN 1 AND 4", name="ck_financial_cost_records_tier"),
        CheckConstraint(
            "period_end > period_start", name="ck_financial_cost_records_period"
        ),
        CheckConstraint(
            "currency = upper(currency) AND char_length(currency) = 3",
            name="ck_financial_cost_records_currency",
        ),
        CheckConstraint(
            "calculated_cost >= 0 AND (input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0) "
            "AND (gpu_hours IS NULL OR gpu_hours >= 0) "
            "AND (input_rate_per_million_tokens IS NULL OR input_rate_per_million_tokens >= 0) "
            "AND (output_rate_per_million_tokens IS NULL OR output_rate_per_million_tokens >= 0) "
            "AND (gpu_hourly_rate IS NULL OR gpu_hourly_rate >= 0)",
            name="ck_financial_cost_records_non_negative",
        ),
        CheckConstraint(
            "(cost_type = 'public_api' AND provider IS NOT NULL AND model_name IS NOT NULL "
            "AND resource_type IS NULL AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
            "AND gpu_hours IS NULL AND input_rate_per_million_tokens IS NOT NULL "
            "AND output_rate_per_million_tokens IS NOT NULL AND gpu_hourly_rate IS NULL) "
            "OR (cost_type = 'private_llm' AND provider IS NULL AND model_name IS NULL "
            "AND resource_type IS NOT NULL AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND gpu_hours IS NOT NULL AND input_rate_per_million_tokens IS NULL "
            "AND output_rate_per_million_tokens IS NULL AND gpu_hourly_rate IS NOT NULL)",
            name="ck_financial_cost_records_shape",
        ),
        Index(
            "uq_financial_cost_records_source_event",
            "cost_type",
            "source_event_id",
            unique=True,
            postgresql_where="source_event_id IS NOT NULL",
        ),
        Index("ix_financial_cost_records_period", "period_start", "period_end"),
        Index(
            "ix_financial_cost_records_tier_period",
            "tier",
            "period_start",
            "period_end",
        ),
        Index(
            "ix_financial_cost_records_app_period",
            "application_key",
            "period_start",
            "period_end",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    application_key = Column(Text, nullable=False)
    tier = Column(Integer, nullable=False)
    cost_type = Column(String, nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    rate_id = Column(
        BigInteger,
        ForeignKey("financial_rates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    resource_type = Column(String, nullable=True)
    input_tokens = Column(BigInteger, nullable=True)
    output_tokens = Column(BigInteger, nullable=True)
    gpu_hours = Column(Numeric(20, 8), nullable=True)
    input_rate_per_million_tokens = Column(Numeric(20, 8), nullable=True)
    output_rate_per_million_tokens = Column(Numeric(20, 8), nullable=True)
    gpu_hourly_rate = Column(Numeric(20, 8), nullable=True)
    calculated_cost = Column(Numeric(20, 8), nullable=False)
    currency = Column(String(3), nullable=False, default="USD", server_default="USD")
    source_event_id = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LlmGatewayTokenCheckpoint(Base):
    """Singleton cursor for completed Grafana LLM-token query intervals."""

    __tablename__ = "llm_gateway_token_checkpoint"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_llm_gateway_token_checkpoint_singleton"),
    )

    id = Column(Integer, primary_key=True, default=1)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    counters = Column(JSON, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
