-- Reusable GitHub connections. Access tokens are encrypted with PostgreSQL
-- pgcrypto and are never returned by the public API.

CREATE TABLE IF NOT EXISTS github_connections (
    id                     UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    display_name           VARCHAR(200)  NOT NULL,
    connection_url         VARCHAR(2048) NOT NULL,
    access_token_encrypted BYTEA         NOT NULL,
    created_by             VARCHAR(255)  NOT NULL,
    updated_by             VARCHAR(255)  NOT NULL,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    deleted_at             TIMESTAMPTZ   NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_github_connections_url_active
    ON github_connections (lower(connection_url))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_github_connections_created_active
    ON github_connections (created_at, id)
    WHERE deleted_at IS NULL;
