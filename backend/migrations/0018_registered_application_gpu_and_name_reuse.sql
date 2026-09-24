-- Optional GPU request stored alongside the other container resource defaults.

ALTER TABLE registered_application_container_configs
    ADD COLUMN IF NOT EXISTS gpu_request VARCHAR(64) NULL;

-- Soft-deleted templates must not hold their name. The catalog only shows rows
-- with deleted_at IS NULL, so uniqueness applies to those rows only.

DROP INDEX IF EXISTS uq_registered_applications_name_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_registered_applications_name_ci
    ON registered_applications (lower(name))
    WHERE deleted_at IS NULL;
