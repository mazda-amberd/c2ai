-- Deployment-specific configuration for instances created from registered
-- application templates. Values stored here are non-secret; only opaque secret
-- reference identifiers are persisted in the JSON configuration.

ALTER TABLE deployment_instances
    ADD COLUMN IF NOT EXISTS configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS triggered_by VARCHAR(255) NOT NULL DEFAULT 'system',
    ADD COLUMN IF NOT EXISTS dispatch_reference JSONB NULL;

ALTER TABLE deployment_instances
    ALTER COLUMN triggered_by DROP DEFAULT;
