-- Published per-token pricing for every public model Athena supports.
--
-- One row per (provider, model_name) exactly as the gateway labels its token
-- counters. gpt-4o is seeded for both public OpenAI and Azure OpenAI because
-- the gateway registry can route the same model name through either; the
-- WHERE NOT EXISTS guard leaves the rows migration 0017 already created.
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
        ('anthropic',    'claude-opus-5',              5.00000000, 25.00000000),
        ('anthropic',    'claude-sonnet-5',            2.00000000, 10.00000000),
        ('anthropic',    'claude-haiku-4-5-20251001',  1.00000000,  5.00000000),
        ('openai',       'gpt-4.1',                    2.00000000,  8.00000000),
        ('openai',       'gpt-4.1-mini',               0.40000000,  1.60000000),
        ('openai',       'gpt-4.1-nano',               0.10000000,  0.40000000),
        ('openai',       'gpt-4o',                     2.50000000, 10.00000000),
        ('openai',       'gpt-4o-mini',                0.15000000,  0.60000000),
        ('openai',       'o3',                         2.00000000,  8.00000000),
        ('openai',       'o3-mini',                    1.10000000,  4.40000000),
        ('openai',       'o4-mini',                    1.10000000,  4.40000000),
        ('azure-openai', 'gpt-4.1',                    2.00000000,  8.00000000),
        ('azure-openai', 'gpt-4.1-mini',               0.40000000,  1.60000000),
        ('azure-openai', 'gpt-4.1-nano',               0.10000000,  0.40000000),
        ('azure-openai', 'gpt-4o',                     2.50000000, 10.00000000),
        ('azure-openai', 'gpt-4o-mini',                0.15000000,  0.60000000),
        ('azure-openai', 'o3',                         2.00000000,  8.00000000),
        ('azure-openai', 'o3-mini',                    1.10000000,  4.40000000),
        ('azure-openai', 'o4-mini',                    1.10000000,  4.40000000),
        ('gemini',       'gemini-2.5-pro',             1.25000000, 10.00000000),
        ('gemini',       'gemini-2.5-flash',           0.30000000,  2.50000000),
        ('gemini',       'gemini-2.5-flash-lite',      0.10000000,  0.40000000),
        ('gemini',       'gemini-2.0-flash',           0.10000000,  0.40000000),
        ('gemini',       'gemini-2.0-flash-lite',      0.07500000,  0.30000000)
) AS seed (provider, model_name, input_rate, output_rate)
WHERE NOT EXISTS (
    SELECT 1
    FROM financial_rates existing
    WHERE existing.cost_type = 'public_api'
      AND existing.provider = seed.provider
      AND existing.model_name = seed.model_name
      AND existing.effective_to IS NULL
);
