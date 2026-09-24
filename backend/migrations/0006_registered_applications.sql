-- Reusable registered application templates and immutable template versions.
-- Credentials and secret values are deliberately excluded: only opaque IDs and
-- secret references are stored by Athena.

CREATE TABLE IF NOT EXISTS registered_applications (
    id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             VARCHAR(200) NOT NULL,
    application_type VARCHAR(32)  NOT NULL,
    status           VARCHAR(32)  NOT NULL DEFAULT 'active',
    current_version  INTEGER      NOT NULL DEFAULT 1,
    created_by       TEXT         NOT NULL,
    updated_by       TEXT         NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_registered_applications_type
        CHECK (application_type IN ('github_workflow', 'containerized')),
    CONSTRAINT ck_registered_applications_status
        CHECK (status IN ('active', 'draft', 'deprecated')),
    CONSTRAINT ck_registered_applications_current_version
        CHECK (current_version > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_registered_applications_name_ci
    ON registered_applications (lower(name));

CREATE TABLE IF NOT EXISTS registered_application_versions (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_id UUID        NOT NULL REFERENCES registered_applications(id) ON DELETE CASCADE,
    version        INTEGER     NOT NULL,
    description    TEXT        NULL,
    created_by     TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_registered_application_versions_application_version
        UNIQUE (application_id, version),
    CONSTRAINT ck_registered_application_versions_version CHECK (version > 0)
);

CREATE INDEX IF NOT EXISTS ix_registered_application_versions_application_id
    ON registered_application_versions (application_id);

CREATE TABLE IF NOT EXISTS registered_application_github_configs (
    application_version_id UUID         PRIMARY KEY REFERENCES registered_application_versions(id) ON DELETE CASCADE,
    github_connection_id   VARCHAR(200) NOT NULL,
    trigger_method         VARCHAR(32)  NOT NULL,
    repository             VARCHAR(255) NOT NULL,
    workflow_file_path     VARCHAR(512) NOT NULL,
    ref                    VARCHAR(255) NOT NULL DEFAULT 'main',
    CONSTRAINT ck_registered_application_github_configs_trigger_method
        CHECK (trigger_method IN ('workflow_dispatch', 'repository_dispatch'))
);

CREATE TABLE IF NOT EXISTS registered_application_container_configs (
    application_version_id  UUID         PRIMARY KEY REFERENCES registered_application_versions(id) ON DELETE CASCADE,
    registry                VARCHAR(200) NOT NULL,
    registry_credential_id  VARCHAR(200) NULL,
    image_repository        VARCHAR(512) NOT NULL,
    default_image_tag       VARCHAR(255) NULL,
    image_pull_policy       VARCHAR(32)  NOT NULL,
    container_port          INTEGER      NULL,
    expose_public_service   BOOLEAN      NOT NULL,
    CONSTRAINT ck_registered_application_container_configs_pull_policy
        CHECK (image_pull_policy IN ('IfNotPresent', 'Always', 'Never')),
    CONSTRAINT ck_registered_application_container_configs_port
        CHECK (container_port IS NULL OR container_port BETWEEN 1 AND 65535)
);

CREATE TABLE IF NOT EXISTS registered_application_parameters (
    id                     UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_version_id UUID         NOT NULL REFERENCES registered_application_versions(id) ON DELETE CASCADE,
    position               INTEGER      NOT NULL,
    label                  VARCHAR(200) NOT NULL,
    key                    VARCHAR(128) NOT NULL,
    parameter_type         VARCHAR(32)  NOT NULL,
    required               BOOLEAN      NOT NULL DEFAULT false,
    default_value          JSONB        NULL,
    options                JSONB        NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT uq_registered_application_parameters_version_key
        UNIQUE (application_version_id, key),
    CONSTRAINT uq_registered_application_parameters_version_position
        UNIQUE (application_version_id, position),
    CONSTRAINT ck_registered_application_parameters_position CHECK (position >= 0),
    CONSTRAINT ck_registered_application_parameters_type
        CHECK (parameter_type IN ('text', 'number', 'boolean', 'select', 'key_value'))
);

CREATE INDEX IF NOT EXISTS ix_registered_application_parameters_version_id
    ON registered_application_parameters (application_version_id);

CREATE TABLE IF NOT EXISTS registered_application_secret_references (
    id                     UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_version_id UUID         NOT NULL REFERENCES registered_application_versions(id) ON DELETE CASCADE,
    position               INTEGER      NOT NULL,
    label                  VARCHAR(200) NOT NULL,
    key                    VARCHAR(128) NOT NULL,
    required               BOOLEAN      NOT NULL DEFAULT false,
    secret_reference       VARCHAR(512) NULL,
    CONSTRAINT uq_registered_application_secrets_version_key
        UNIQUE (application_version_id, key),
    CONSTRAINT uq_registered_application_secrets_version_position
        UNIQUE (application_version_id, position),
    CONSTRAINT ck_registered_application_secrets_position CHECK (position >= 0)
);

CREATE INDEX IF NOT EXISTS ix_registered_application_secrets_version_id
    ON registered_application_secret_references (application_version_id);
