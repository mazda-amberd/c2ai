-- 0023_unified_deployments.sql
-- One deployment model for every application.
--
-- Before: the ADA pipeline (/api/deploy*) tracked operations only in
-- pipeline_runs (+ who-for labels in deployments); registered applications
-- tracked instances in deployment_instances. Each had its own concurrency
-- guard and status tracking, and /api/pipeline/active stitched them together.
--
-- After:
--   * ADA is a registered GitHub Workflow application (seeded below with a
--     fixed id; the API keeps its repository/ref in sync with settings).
--   * deployment_instances is the only record of what runs where.
--   * pipeline_runs is the operation log of every instance (deploy, update,
--     migration, terminate), linked to it, with the outcome in `conclusion`.
--   * At most one unfinished operation per subdomain, enforced by an index.
--   * Instance names can be reused once the previous instance is gone.

-- --------------------------------------------------------------------------
-- 1. The ADA application
-- --------------------------------------------------------------------------
DO $$
DECLARE
    app_name TEXT := 'ADA';
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM registered_applications
        WHERE id = 'ada00000-0000-4000-8000-000000000001'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM registered_applications
            WHERE lower(name) = lower(app_name) AND deleted_at IS NULL
        ) THEN
            app_name := 'ADA (Amberd)';
        END IF;

        INSERT INTO registered_applications
            (id, name, application_type, status, current_version, created_by)
        VALUES
            ('ada00000-0000-4000-8000-000000000001', app_name, 'github_workflow',
             'active', 1, 'system');

        INSERT INTO registered_application_versions
            (id, application_id, version, description, created_by)
        VALUES
            ('ada00000-0000-4000-8000-000000000002',
             'ada00000-0000-4000-8000-000000000001', 1,
             'Amberd ADA, deployed with the devops ada-* workflows.', 'system');

        INSERT INTO registered_application_github_configs
            (application_version_id, github_connection_id, trigger_method,
             repository, code_repository, workflow_file_path, ref)
        VALUES
            ('ada00000-0000-4000-8000-000000000002', 'athena-environment',
             'workflow_dispatch', 'amberd-ai/devops', 'amberd-ai/dealership_new',
             '.github/workflows/ada-deploy.yaml', 'main');

        INSERT INTO registered_application_parameters
            (application_version_id, position, label, key, parameter_type, required)
        VALUES
            ('ada00000-0000-4000-8000-000000000002', 0, 'Customer name', 'customer_name', 'text', true),
            ('ada00000-0000-4000-8000-000000000002', 1, 'Environment instance', 'env_instance', 'text', true),
            ('ada00000-0000-4000-8000-000000000002', 2, 'Branch', 'branch', 'text', true),
            ('ada00000-0000-4000-8000-000000000002', 3, 'Slack user', 'slack_user', 'text', false);
    END IF;
END $$;

-- --------------------------------------------------------------------------
-- 2. Instance names are unique among live instances only
-- --------------------------------------------------------------------------
ALTER TABLE deployment_instances
    DROP CONSTRAINT IF EXISTS uq_deployment_instances_tier_instance_name;
CREATE UNIQUE INDEX IF NOT EXISTS uq_deployment_instances_tier_instance_name
    ON deployment_instances (tier, instance_name)
    WHERE status NOT IN ('terminated', 'cancelled');

-- --------------------------------------------------------------------------
-- 3. pipeline_runs becomes the operation log
-- --------------------------------------------------------------------------
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS deployment_instance_id UUID NULL
        REFERENCES deployment_instances (id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS conclusion TEXT NULL;

-- 3a. Every legacy ADA subdomain becomes one ADA deployment instance.
WITH runs AS (
    SELECT r.*,
           row_number() OVER (PARTITION BY subdomain ORDER BY dispatched_at DESC) AS newest,
           row_number() OVER (PARTITION BY subdomain ORDER BY dispatched_at ASC) AS oldest
    FROM pipeline_runs r
    WHERE r.deployment_instance_id IS NULL
),
latest AS (SELECT * FROM runs WHERE newest = 1),
first_run AS (SELECT * FROM runs WHERE oldest = 1),
labels AS (
    SELECT DISTINCT ON (subdomain) subdomain, customer_name, env_instance, branch
    FROM deployments
    ORDER BY subdomain, created_at DESC, id DESC
),
last_branch AS (
    SELECT DISTINCT ON (subdomain) subdomain, branch
    FROM pipeline_runs
    WHERE branch IS NOT NULL
    ORDER BY subdomain, dispatched_at DESC
)
INSERT INTO deployment_instances
    (application_id, application_version_id, instance_name, tier, status,
     configuration, triggered_by, dispatch_reference, current_step,
     completed_at, terminated_at, created_at, updated_at)
SELECT
    'ada00000-0000-4000-8000-000000000001',
    'ada00000-0000-4000-8000-000000000002',
    latest.subdomain,
    COALESCE(latest.tier, first_run.tier, 1),
    CASE
        WHEN latest.ended_at IS NULL AND latest.operation = 'deploy' THEN 'deploying'
        WHEN latest.ended_at IS NULL AND latest.operation = 'terminate' THEN 'terminating'
        WHEN latest.ended_at IS NULL THEN 'updating'
        WHEN latest.operation = 'terminate' THEN 'terminated'
        ELSE 'running'
    END,
    jsonb_build_object(
        'parameters', jsonb_strip_nulls(jsonb_build_object(
            'subdomain', latest.subdomain,
            'customer_name', labels.customer_name,
            'env_instance', labels.env_instance,
            'branch', COALESCE(last_branch.branch, labels.branch)
        )),
        'github', jsonb_strip_nulls(jsonb_build_object(
            'repository', 'amberd-ai/devops',
            'workflow_file_path', '.github/workflows/ada-deploy.yaml',
            'version', COALESCE(last_branch.branch, labels.branch)
        ))
    ),
    latest.triggered_by,
    jsonb_strip_nulls(jsonb_build_object(
        'trigger_method', 'workflow_dispatch',
        'workflow_id', latest.event_type,
        'repo_owner', 'amberd-ai',
        'repo_name', 'devops',
        'ref', 'main',
        'subdomain', latest.subdomain,
        'run_id', latest.run_id,
        'dispatched_at', latest.dispatched_at,
        'operation', CASE latest.operation
            WHEN 'update' THEN 'upgrade'
            WHEN 'migration' THEN 'move_tier'
            ELSE latest.operation END
    )),
    CASE WHEN latest.ended_at IS NULL THEN 'validating_configuration' ELSE 'completed' END,
    latest.ended_at,
    CASE WHEN latest.operation = 'terminate' THEN latest.ended_at END,
    first_run.dispatched_at,
    COALESCE(latest.ended_at, latest.dispatched_at)
FROM latest
JOIN first_run ON first_run.subdomain = latest.subdomain
LEFT JOIN labels ON labels.subdomain = latest.subdomain
LEFT JOIN last_branch ON last_branch.subdomain = latest.subdomain
-- A live registered instance already owns this name; leave its runs unlinked.
WHERE NOT EXISTS (
    SELECT 1 FROM deployment_instances existing
    WHERE existing.instance_name = latest.subdomain
      AND existing.status NOT IN ('terminated', 'cancelled')
);

UPDATE pipeline_runs AS r
SET deployment_instance_id = i.id
FROM deployment_instances AS i
WHERE r.deployment_instance_id IS NULL
  AND i.application_id = 'ada00000-0000-4000-8000-000000000001'
  AND i.instance_name = r.subdomain;

-- Legacy runs never recorded how they ended; completed ones count as success.
UPDATE pipeline_runs SET conclusion = 'success'
WHERE ended_at IS NOT NULL AND conclusion IS NULL;

-- 3b. Registered deployments get their latest operation in the log.
INSERT INTO pipeline_runs
    (id, deployment_instance_id, subdomain, operation, event_type, triggered_by,
     run_id, tier, branch, dispatched_at, ended_at, conclusion)
SELECT
    i.id::text,
    i.id,
    COALESCE(i.subdomain, i.instance_name),
    CASE
        WHEN i.dispatch_reference ->> 'operation' = 'upgrade' OR i.status = 'updating'
            THEN 'update'
        WHEN i.dispatch_reference ->> 'operation' = 'terminate'
          OR i.status IN ('terminating', 'terminated') THEN 'terminate'
        ELSE 'deploy'
    END,
    COALESCE(
        i.dispatch_reference ->> 'workflow_id',
        i.dispatch_reference ->> 'event_type',
        'registered-application'
    ),
    i.triggered_by,
    (i.dispatch_reference ->> 'run_id')::bigint,
    i.tier,
    NULL,
    i.updated_at,
    CASE WHEN i.status IN ('pending', 'deploying', 'updating', 'terminating') THEN NULL
         ELSE COALESCE(i.completed_at, i.terminated_at, i.updated_at) END,
    CASE i.status
        WHEN 'running' THEN 'success'
        WHEN 'terminated' THEN 'success'
        WHEN 'failed' THEN 'failure'
        WHEN 'cancelled' THEN 'cancelled'
    END
FROM deployment_instances i
WHERE NOT EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.deployment_instance_id = i.id);

-- 3c. Close duplicate unfinished operations (the legacy guard ignored
-- orphans older than ten minutes instead of closing them).
UPDATE pipeline_runs AS r
SET ended_at = now(), conclusion = 'abandoned'
WHERE r.ended_at IS NULL
  AND EXISTS (
      SELECT 1 FROM pipeline_runs newer
      WHERE newer.subdomain = r.subdomain
        AND newer.ended_at IS NULL
        AND (newer.dispatched_at, newer.id) > (r.dispatched_at, r.id)
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_runs_active_subdomain
    ON pipeline_runs (subdomain)
    WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_instance
    ON pipeline_runs (deployment_instance_id, dispatched_at DESC);
ALTER TABLE pipeline_runs
    ADD CONSTRAINT ck_pipeline_runs_conclusion
        CHECK (conclusion IS NULL OR conclusion IN (
            'success', 'failure', 'cancelled', 'abandoned'
        ));

-- --------------------------------------------------------------------------
-- 4. deployments (legacy who-for labels) now live in the instance parameters
-- --------------------------------------------------------------------------
DROP TABLE IF EXISTS deployments;
