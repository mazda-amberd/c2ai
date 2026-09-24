-- 0017_public_api_token_rates.sql
-- Published per-token pricing for the public models the LLM gateway routes to.
--
-- The gateway reports token counters labelled with its own provider and model
-- names, so a rate row exists per (provider, model_name) pair exactly as those
-- labels appear. gpt-4o is seeded for both public OpenAI and Azure OpenAI
-- because the gateway registry can route the same model name through either.
--
-- Rates are effective-dated from the epoch so historical gateway usage can be
-- priced immediately. Changing a price must close the current row and insert a
-- new effective-dated row, leaving already-calculated costs untouched.

INSERT INTO financial_rates (
    cost_type,
    provider,
    model_name,
    input_rate_per_million_tokens,
    output_rate_per_million_tokens,
    currency,
    effective_from
)
SELECT
    'public_api',
    seed.provider,
    seed.model_name,
    seed.input_rate,
    seed.output_rate,
    'USD',
    TIMESTAMPTZ '1970-01-01 00:00:00+00'
FROM (
    VALUES
        ('openai',       'gpt-4o',             2.50000000, 10.00000000),
        ('azure-openai', 'gpt-4o',             2.50000000, 10.00000000),
        ('anthropic',    'claude-sonnet-4-6',  3.00000000, 15.00000000)
) AS seed (provider, model_name, input_rate, output_rate)
WHERE NOT EXISTS (
    SELECT 1
    FROM financial_rates existing
    WHERE existing.cost_type = 'public_api'
      AND existing.provider = seed.provider
      AND existing.model_name = seed.model_name
      AND existing.effective_to IS NULL
);
