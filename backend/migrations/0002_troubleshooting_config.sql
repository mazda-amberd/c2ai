-- 0002_troubleshooting_config.sql
-- Singleton runtime configuration for application troubleshooting.

CREATE TABLE IF NOT EXISTS troubleshooting_config (
    id             SMALLINT    PRIMARY KEY DEFAULT 1,
    lookback_hours INTEGER     NOT NULL DEFAULT 4,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_troubleshooting_config_singleton CHECK (id = 1),
    CONSTRAINT ck_troubleshooting_lookback_hours CHECK (
        lookback_hours BETWEEN 1 AND 168
    )
);

INSERT INTO troubleshooting_config (id, lookback_hours)
VALUES (1, 4)
ON CONFLICT (id) DO NOTHING;
