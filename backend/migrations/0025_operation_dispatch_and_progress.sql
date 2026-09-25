-- 0025_operation_dispatch_and_progress.sql
-- The operation log also carries what the dispatch outbox and the tracker
-- need, so no request holds a transaction open while talking to GitHub and
-- status reads never call GitHub.
--
--   kind            lifecycle operation (deploy, upgrade, rollback, move_tier,
--                   terminate); pipeline_runs.operation keeps the UI's words
--   dispatch_state  pending (saved, not yet sent) | dispatched | failed
--   restore_state   the instance as it was before the operation, restored if
--                   the dispatch never reaches the pipeline
--   progress        latest GitHub Actions run snapshot (status, jobs, steps),
--                   written by the deployments.track job
--   progress_updated_at  when that snapshot was taken

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS kind TEXT NULL,
    ADD COLUMN IF NOT EXISTS dispatch_state TEXT NOT NULL DEFAULT 'dispatched',
    ADD COLUMN IF NOT EXISTS restore_state JSONB NULL,
    ADD COLUMN IF NOT EXISTS progress JSONB NULL,
    ADD COLUMN IF NOT EXISTS progress_updated_at TIMESTAMPTZ NULL;

UPDATE pipeline_runs SET kind = CASE operation
    WHEN 'update' THEN 'upgrade'
    WHEN 'migration' THEN 'move_tier'
    ELSE operation
END
WHERE kind IS NULL;

ALTER TABLE pipeline_runs
    ADD CONSTRAINT ck_pipeline_runs_dispatch_state
        CHECK (dispatch_state IN ('pending', 'dispatched', 'failed')),
    ADD CONSTRAINT ck_pipeline_runs_kind
        CHECK (kind IS NULL OR kind IN ('deploy', 'upgrade', 'rollback', 'move_tier', 'terminate'));

CREATE INDEX IF NOT EXISTS ix_pipeline_runs_open
    ON pipeline_runs (dispatched_at)
    WHERE ended_at IS NULL;
