-- Durable lifecycle tracking for registered-application deployment instances.
-- Progress callbacks append immutable events while the instance row retains the
-- latest status for efficient Tier dashboards and polling.

ALTER TABLE deployment_instances
    ADD COLUMN IF NOT EXISTS current_step VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS failure_reason TEXT NULL,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS rollback_count INTEGER NOT NULL DEFAULT 0;

UPDATE deployment_instances
SET current_step = CASE
    WHEN status IN ('running', 'terminated', 'cancelled') THEN 'completed'
    WHEN status = 'failed' THEN 'failed'
    ELSE 'validating_configuration'
END
WHERE current_step IS NULL;

UPDATE deployment_instances
SET completed_at = updated_at
WHERE completed_at IS NULL
  AND status IN ('running', 'failed', 'terminated', 'cancelled');

ALTER TABLE deployment_instances
    ALTER COLUMN current_step SET NOT NULL;

ALTER TABLE deployment_instances
    ADD CONSTRAINT ck_deployment_instances_current_step
        CHECK (current_step IN (
            'validating_configuration',
            'creating_namespace',
            'applying_resources',
            'waiting_for_rollout',
            'verifying_deployment',
            'completed',
            'failed'
        )),
    ADD CONSTRAINT ck_deployment_instances_rollback_count
        CHECK (rollback_count >= 0);

CREATE TABLE IF NOT EXISTS deployment_instance_events (
    id                     UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_instance_id UUID         NOT NULL REFERENCES deployment_instances(id) ON DELETE CASCADE,
    step                   VARCHAR(64)  NOT NULL,
    status                 VARCHAR(32)  NOT NULL,
    message                TEXT         NULL,
    failure_reason         TEXT         NULL,
    created_by             VARCHAR(255) NOT NULL,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_deployment_instance_events_step
        CHECK (step IN (
            'validating_configuration',
            'creating_namespace',
            'applying_resources',
            'waiting_for_rollout',
            'verifying_deployment',
            'completed',
            'failed'
        )),
    CONSTRAINT ck_deployment_instance_events_status
        CHECK (status IN (
            'pending', 'deploying', 'running', 'updating',
            'failed', 'terminating', 'terminated', 'cancelled'
        ))
);

CREATE INDEX IF NOT EXISTS ix_deployment_instance_events_instance_created
    ON deployment_instance_events (deployment_instance_id, created_at);

INSERT INTO deployment_instance_events (
    deployment_instance_id,
    step,
    status,
    message,
    failure_reason,
    created_by,
    created_at
)
SELECT
    instance.id,
    instance.current_step,
    instance.status,
    'Existing deployment lifecycle state imported.',
    instance.failure_reason,
    'system-migration',
    instance.updated_at
FROM deployment_instances AS instance
WHERE NOT EXISTS (
    SELECT 1
    FROM deployment_instance_events AS event
    WHERE event.deployment_instance_id = instance.id
);
