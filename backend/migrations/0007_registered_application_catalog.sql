-- Catalog deletion/audit support and generic deployment instances.
-- Registered applications are soft-deleted so completed deployment history keeps
-- valid template relationships. Only non-terminal instances block deletion.

ALTER TABLE registered_applications
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;

CREATE TABLE IF NOT EXISTS deployment_instances (
    id                     UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_id         UUID         NOT NULL REFERENCES registered_applications(id) ON DELETE RESTRICT,
    application_version_id UUID         NOT NULL REFERENCES registered_application_versions(id) ON DELETE RESTRICT,
    instance_name          VARCHAR(200) NOT NULL,
    tier                   INTEGER      NOT NULL,
    status                 VARCHAR(32)  NOT NULL DEFAULT 'pending',
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    terminated_at          TIMESTAMPTZ  NULL,
    CONSTRAINT uq_deployment_instances_tier_instance_name
        UNIQUE (tier, instance_name),
    CONSTRAINT ck_deployment_instances_tier CHECK (tier BETWEEN 1 AND 4),
    CONSTRAINT ck_deployment_instances_status
        CHECK (status IN (
            'pending', 'deploying', 'running', 'updating',
            'failed', 'terminating', 'terminated', 'cancelled'
        ))
);

CREATE INDEX IF NOT EXISTS ix_deployment_instances_application_status
    ON deployment_instances (application_id, status);

CREATE INDEX IF NOT EXISTS ix_deployment_instances_tier_status
    ON deployment_instances (tier, status);
