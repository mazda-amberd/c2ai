-- 0005_llm_gateway_checkpoint.sql
-- Persist the end of the last completed Grafana token interval so scheduled
-- ingestion can store each source-namespace time range exactly once.

CREATE TABLE IF NOT EXISTS llm_gateway_token_checkpoint (
    id          SMALLINT    PRIMARY KEY DEFAULT 1,
    observed_at TIMESTAMPTZ NOT NULL,
    counters    JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_llm_gateway_token_checkpoint_singleton CHECK (id = 1)
);
