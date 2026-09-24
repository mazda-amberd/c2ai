-- Represent DNS record removal during container deployment termination while
-- preserving the original subdomain and hostname for lifecycle history.

ALTER TABLE deployment_instances
    DROP CONSTRAINT IF EXISTS ck_deployment_instances_dns_status,
    ADD CONSTRAINT ck_deployment_instances_dns_status
        CHECK (dns_status IS NULL OR dns_status IN (
            'pending', 'configuring', 'active', 'deleting', 'deleted', 'failed'
        ));

UPDATE deployment_instances
SET dns_status = 'deleted'
WHERE status = 'terminated'
  AND dns_status IS NOT NULL;
