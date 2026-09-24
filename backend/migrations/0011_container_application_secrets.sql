-- Managed secrets for containerized registered applications. Secret values are
-- written to an external secret broker and are never persisted by Athena.

CREATE TABLE IF NOT EXISTS container_application_secrets (
    id                   UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_id       UUID         NOT NULL REFERENCES registered_applications(id) ON DELETE CASCADE,
    name                 VARCHAR(63)  NOT NULL,
    environment_variable VARCHAR(128) NOT NULL,
    secret_reference     VARCHAR(512) NULL,
    created_by           VARCHAR(255) NOT NULL,
    updated_by           VARCHAR(255) NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ  NULL,
    CONSTRAINT ck_container_application_secrets_name
        CHECK (name ~ '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$'),
    CONSTRAINT ck_container_application_secrets_environment_variable
        CHECK (environment_variable ~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_container_application_secrets_name_active
    ON container_application_secrets (application_id, name)
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_container_application_secrets_env_active
    ON container_application_secrets (application_id, environment_variable)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_container_application_secrets_application_created
    ON container_application_secrets (application_id, created_at)
    WHERE deleted_at IS NULL;
