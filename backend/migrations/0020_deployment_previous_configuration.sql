-- 0020_deployment_previous_configuration.sql
-- Keep the configuration an instance ran before its latest upgrade so that
-- rollback can return to it. Previously the upgrade overwrote the only stored
-- configuration, and "rollback" re-dispatched the new version.

ALTER TABLE deployment_instances
    ADD COLUMN IF NOT EXISTS previous_configuration JSONB NULL;
