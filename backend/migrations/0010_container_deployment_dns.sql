-- Container-only DNS intent and result tracking. Athena owns hostnames under
-- amberd.ai; the deployment pipeline performs the provider-specific DNS work.

ALTER TABLE deployment_instances
    ADD COLUMN IF NOT EXISTS subdomain VARCHAR(63) NULL,
    ADD COLUMN IF NOT EXISTS hostname VARCHAR(253) NULL,
    ADD COLUMN IF NOT EXISTS dns_status VARCHAR(32) NULL;

-- Preserve compatible hostnames from pre-DNS container deployment snapshots.
UPDATE deployment_instances AS instance
SET
    subdomain = split_part(instance.configuration->'container'->>'host', '.', 1),
    hostname = instance.configuration->'container'->>'host',
    dns_status = CASE
        WHEN instance.status = 'running' THEN 'active'
        WHEN instance.status = 'failed' THEN 'failed'
        ELSE 'pending'
    END
FROM registered_applications AS application
WHERE application.id = instance.application_id
  AND application.application_type = 'containerized'
  AND instance.subdomain IS NULL
  AND instance.configuration->'container'->>'host'
      ~ '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.amberd\.ai$';

ALTER TABLE deployment_instances
    ADD CONSTRAINT ck_deployment_instances_dns_fields
        CHECK (
            (subdomain IS NULL AND hostname IS NULL AND dns_status IS NULL)
            OR
            (subdomain IS NOT NULL AND hostname IS NOT NULL AND dns_status IS NOT NULL)
        ),
    ADD CONSTRAINT ck_deployment_instances_dns_status
        CHECK (dns_status IS NULL OR dns_status IN (
            'pending', 'configuring', 'active', 'failed'
        ));

CREATE UNIQUE INDEX IF NOT EXISTS uq_deployment_instances_dns_subdomain_active
    ON deployment_instances (subdomain)
    WHERE subdomain IS NOT NULL
      AND status NOT IN ('terminated', 'cancelled');

ALTER TABLE deployment_instances
    DROP CONSTRAINT ck_deployment_instances_current_step,
    ADD CONSTRAINT ck_deployment_instances_current_step
        CHECK (current_step IN (
            'validating_configuration',
            'creating_namespace',
            'applying_resources',
            'waiting_for_rollout',
            'verifying_deployment',
            'configuring_dns',
            'completed',
            'failed'
        ));

ALTER TABLE deployment_instance_events
    DROP CONSTRAINT ck_deployment_instance_events_step,
    ADD CONSTRAINT ck_deployment_instance_events_step
        CHECK (step IN (
            'validating_configuration',
            'creating_namespace',
            'applying_resources',
            'waiting_for_rollout',
            'verifying_deployment',
            'configuring_dns',
            'completed',
            'failed'
        ));
