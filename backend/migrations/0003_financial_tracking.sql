-- 0003_financial_tracking.sql
-- Historical financial rates and calculated application cost records.
--
-- Rates are effective-dated so changing a rate means closing the current row
-- and inserting a new one. Cost records also snapshot the applied rate and
-- calculated amount, preserving the value originally shown for past periods.

CREATE TABLE IF NOT EXISTS financial_rates (
    id                                BIGSERIAL      PRIMARY KEY,
    cost_type                         TEXT           NOT NULL,
    provider                          TEXT,
    model_name                        TEXT,
    resource_type                     TEXT,
    input_rate_per_million_tokens     NUMERIC(20, 8),
    output_rate_per_million_tokens    NUMERIC(20, 8),
    gpu_hourly_rate                   NUMERIC(20, 8),
    currency                          CHAR(3)        NOT NULL DEFAULT 'USD',
    effective_from                    TIMESTAMPTZ    NOT NULL DEFAULT now(),
    effective_to                      TIMESTAMPTZ,
    created_at                        TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at                        TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT ck_financial_rates_cost_type
        CHECK (cost_type IN ('public_api', 'private_llm')),
    CONSTRAINT ck_financial_rates_effective_period
        CHECK (effective_to IS NULL OR effective_to > effective_from),
    CONSTRAINT ck_financial_rates_currency
        CHECK (currency = upper(currency) AND char_length(currency) = 3),
    CONSTRAINT ck_financial_rates_non_negative
        CHECK (
            (input_rate_per_million_tokens IS NULL OR input_rate_per_million_tokens >= 0)
            AND (output_rate_per_million_tokens IS NULL OR output_rate_per_million_tokens >= 0)
            AND (gpu_hourly_rate IS NULL OR gpu_hourly_rate >= 0)
        ),
    CONSTRAINT ck_financial_rates_shape
        CHECK (
            (
                cost_type = 'public_api'
                AND provider IS NOT NULL
                AND model_name IS NOT NULL
                AND resource_type IS NULL
                AND input_rate_per_million_tokens IS NOT NULL
                AND output_rate_per_million_tokens IS NOT NULL
                AND gpu_hourly_rate IS NULL
            )
            OR
            (
                cost_type = 'private_llm'
                AND provider IS NULL
                AND model_name IS NULL
                AND resource_type IS NOT NULL
                AND input_rate_per_million_tokens IS NULL
                AND output_rate_per_million_tokens IS NULL
                AND gpu_hourly_rate IS NOT NULL
            )
        )
);

-- Only one current rate is allowed for a public provider/model pair or a
-- private GPU/instance type. Historical rows remain available after closure.
CREATE UNIQUE INDEX IF NOT EXISTS uq_financial_rates_current_public
    ON financial_rates (provider, model_name)
    WHERE cost_type = 'public_api' AND effective_to IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_financial_rates_current_private
    ON financial_rates (resource_type)
    WHERE cost_type = 'private_llm' AND effective_to IS NULL;

CREATE INDEX IF NOT EXISTS ix_financial_rates_effective_period
    ON financial_rates (cost_type, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS financial_cost_records (
    id                                BIGSERIAL      PRIMARY KEY,
    application_key                   TEXT           NOT NULL,
    tier                              INTEGER        NOT NULL,
    cost_type                         TEXT           NOT NULL,
    period_start                      TIMESTAMPTZ    NOT NULL,
    period_end                        TIMESTAMPTZ    NOT NULL,
    rate_id                           BIGINT         NOT NULL,
    provider                          TEXT,
    model_name                        TEXT,
    resource_type                     TEXT,
    input_tokens                      BIGINT,
    output_tokens                     BIGINT,
    gpu_hours                         NUMERIC(20, 8),
    input_rate_per_million_tokens     NUMERIC(20, 8),
    output_rate_per_million_tokens    NUMERIC(20, 8),
    gpu_hourly_rate                   NUMERIC(20, 8),
    calculated_cost                   NUMERIC(20, 8) NOT NULL,
    currency                          CHAR(3)        NOT NULL DEFAULT 'USD',
    source_event_id                   TEXT,
    created_at                        TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT fk_financial_cost_records_rate
        FOREIGN KEY (rate_id) REFERENCES financial_rates(id) ON DELETE RESTRICT,
    CONSTRAINT ck_financial_cost_records_cost_type
        CHECK (cost_type IN ('public_api', 'private_llm')),
    CONSTRAINT ck_financial_cost_records_tier
        CHECK (tier BETWEEN 1 AND 4),
    CONSTRAINT ck_financial_cost_records_period
        CHECK (period_end > period_start),
    CONSTRAINT ck_financial_cost_records_currency
        CHECK (currency = upper(currency) AND char_length(currency) = 3),
    CONSTRAINT ck_financial_cost_records_non_negative
        CHECK (
            calculated_cost >= 0
            AND (input_tokens IS NULL OR input_tokens >= 0)
            AND (output_tokens IS NULL OR output_tokens >= 0)
            AND (gpu_hours IS NULL OR gpu_hours >= 0)
            AND (input_rate_per_million_tokens IS NULL OR input_rate_per_million_tokens >= 0)
            AND (output_rate_per_million_tokens IS NULL OR output_rate_per_million_tokens >= 0)
            AND (gpu_hourly_rate IS NULL OR gpu_hourly_rate >= 0)
        ),
    CONSTRAINT ck_financial_cost_records_shape
        CHECK (
            (
                cost_type = 'public_api'
                AND provider IS NOT NULL
                AND model_name IS NOT NULL
                AND resource_type IS NULL
                AND input_tokens IS NOT NULL
                AND output_tokens IS NOT NULL
                AND gpu_hours IS NULL
                AND input_rate_per_million_tokens IS NOT NULL
                AND output_rate_per_million_tokens IS NOT NULL
                AND gpu_hourly_rate IS NULL
            )
            OR
            (
                cost_type = 'private_llm'
                AND provider IS NULL
                AND model_name IS NULL
                AND resource_type IS NOT NULL
                AND input_tokens IS NULL
                AND output_tokens IS NULL
                AND gpu_hours IS NOT NULL
                AND input_rate_per_million_tokens IS NULL
                AND output_rate_per_million_tokens IS NULL
                AND gpu_hourly_rate IS NOT NULL
            )
        )
);

-- Optional upstream event IDs make ingestion idempotent without forcing a
-- particular event format on Grafana or the LLM gateway.
CREATE UNIQUE INDEX IF NOT EXISTS uq_financial_cost_records_source_event
    ON financial_cost_records (cost_type, source_event_id)
    WHERE source_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_financial_cost_records_period
    ON financial_cost_records (period_start, period_end);

CREATE INDEX IF NOT EXISTS ix_financial_cost_records_tier_period
    ON financial_cost_records (tier, period_start, period_end);

CREATE INDEX IF NOT EXISTS ix_financial_cost_records_app_period
    ON financial_cost_records (application_key, period_start, period_end);
