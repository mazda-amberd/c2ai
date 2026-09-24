-- 0004_default_gpu_rate.sql
-- Initial configurable private-LLM rate selected for v1.
--
-- The epoch start allows historical GPU usage to be calculated immediately.
-- Future changes must close this row and insert a new effective-dated row so
-- previously calculated costs retain their original rate snapshot.

INSERT INTO financial_rates (
    cost_type,
    resource_type,
    gpu_hourly_rate,
    currency,
    effective_from
)
SELECT
    'private_llm',
    'default_gpu',
    2.50000000,
    'USD',
    TIMESTAMPTZ '1970-01-01 00:00:00+00'
WHERE NOT EXISTS (
    SELECT 1
    FROM financial_rates
    WHERE cost_type = 'private_llm'
      AND resource_type = 'default_gpu'
      AND effective_to IS NULL
);
