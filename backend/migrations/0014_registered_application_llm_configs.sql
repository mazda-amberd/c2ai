-- LLM configuration owned by one immutable registered-application version.
-- API tokens are encrypted with PostgreSQL pgcrypto and never returned by APIs.

CREATE TABLE IF NOT EXISTS registered_application_llm_configs (
    application_version_id UUID          PRIMARY KEY
        REFERENCES registered_application_versions(id) ON DELETE CASCADE,
    endpoint               VARCHAR(2048) NOT NULL,
    api_token_encrypted    BYTEA         NOT NULL,
    model_name             VARCHAR(255)  NOT NULL
);
