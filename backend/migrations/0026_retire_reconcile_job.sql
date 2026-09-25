-- 0026_retire_reconcile_job.sql
-- deployments.reconcile was replaced by deployments.track (0025). A worker
-- only claims kinds it has a handler for, so an unfinished reconcile job
-- left by the previous version would stay queued forever; retire it.

DELETE FROM jobs
WHERE kind = 'deployments.reconcile'
  AND status IN ('queued', 'running');
