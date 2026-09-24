-- 0022_jobs.sql
-- Durable background jobs. Replaces work that lived only in one API process's
-- memory (troubleshooting reports, the cost-ingestion timer, GitHub run-id
-- lookups), so it survives restarts and any replica can serve its status.
--
-- A worker claims a queued job with SELECT ... FOR UPDATE SKIP LOCKED and
-- holds a lease (locked_until) that it renews while running; a job whose
-- lease expires (the worker died) is queued again or failed once its attempts
-- are used up. dedupe_key keeps one unfinished job per key (periodic jobs).

CREATE TABLE IF NOT EXISTS jobs (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind          TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    stage         TEXT        NULL,
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    result        JSONB       NULL,
    owner         TEXT        NULL,
    dedupe_key    TEXT        NULL,
    attempts      INTEGER     NOT NULL DEFAULT 0,
    max_attempts  INTEGER     NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
    run_after     TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by     TEXT        NULL,
    locked_until  TIMESTAMPTZ NULL,
    error_code    TEXT        NULL,
    error_message TEXT        NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ NULL,
    -- How long a finished job stays readable; expires_at = finished_at + this.
    retention_seconds INTEGER NOT NULL DEFAULT 86400,
    expires_at    TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_ready ON jobs (run_after) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS ix_jobs_leases ON jobs (locked_until) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS ix_jobs_expiry ON jobs (expires_at) WHERE expires_at IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_unfinished_dedupe
    ON jobs (dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');
