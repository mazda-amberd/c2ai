-- Revised container registration contract.
-- Existing image columns remain in place for backward-compatible deployment
-- reads. New registry passwords are encrypted with pgcrypto before storage.

ALTER TABLE registered_application_container_configs
    ADD COLUMN IF NOT EXISTS registry_username VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS registry_password_encrypted BYTEA NULL,
    ADD COLUMN IF NOT EXISTS cpu_request VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS memory_request VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS scaling VARCHAR(512) NULL,
    ADD COLUMN IF NOT EXISTS storage VARCHAR(512) NULL,
    ADD COLUMN IF NOT EXISTS environment_variables JSONB NOT NULL DEFAULT '[]'::jsonb;
