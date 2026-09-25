"""GitHub Actions progress: the tracker's fetch and apply steps."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from c2ai.deployments import tracking
from tests.helpers import mock_db


class _Run:
    """The operation-log row the tracker updates."""

    def __init__(self, kind):
        self.kind = kind
        self.tier = None
        self.ended_at = None
        self.conclusion = None
        self.progress = None
        self.progress_updated_at = None
        self.run_id = None


async def _track(db, instance, run, client):
    """plan (DB) → fetch (GitHub) → apply (DB), as the deployments.track job does."""

    with (
        patch("c2ai.deployments.tracking.GitHubActionsClient", return_value=client),
        patch("c2ai.deployments.repository.active_operation", AsyncMock(return_value=run)),
    ):
        plan = await tracking.plan_fetch(db, instance)
        progress, error = await tracking.fetch(plan)
        if progress is not None:
            await tracking.apply_progress(db, run, instance, progress)
    return progress, error


@pytest.mark.asyncio
async def test_completed_termination_syncs_status_and_exposes_nested_steps():
    progress = {
        "run_id": 891,
        "run_number": 113,
        "name": "Ada Terminate",
        "display_title": "ada-terminate | example",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/amberd-ai/devops/actions/runs/891",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "created_at": "2026-09-03T12:00:00Z",
        "updated_at": "2026-09-03T12:04:00Z",
        "jobs": [
            {
                "id": 50,
                "name": "terminate",
                "status": "completed",
                "conclusion": "success",
                "steps": [
                    {
                        "number": 1,
                        "name": "Remove deployment",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            }
        ],
    }
    instance = SimpleNamespace(
        id="deployment-1",
        instance_name="example",
        status="terminating",
        current_step="validating_configuration",
        failure_reason=None,
        completed_at=None,
        terminated_at=None,
        subdomain="example",
        dns_status="deleting",
        events=[],
        configuration={"parameters": {"customer_name": "test", "env_instance": "deploy"}},
        dispatch_reference={
            "repo_owner": "amberd-ai",
            "repo_name": "devops",
            "workflow_id": "ada-terminate.yaml",
            "operation": "terminate",
            "run_id": 891,
        },
    )
    db = mock_db()
    client = MagicMock()
    client.get_workflow_progress = AsyncMock(return_value=progress)

    run = _Run("terminate")
    returned_progress, error = await _track(db, instance, run, client)

    assert error is None
    assert returned_progress["jobs"][0]["steps"][0]["name"] == "Remove deployment"
    assert instance.status == "terminated"
    assert instance.current_step == "completed"
    assert instance.dns_status == "deleted"
    assert instance.events[-1].created_by == "github-actions"
    assert (run.conclusion, run.progress["run_id"]) == ("success", 891)
    db.commit.assert_not_awaited()  # the tracker job commits


@pytest.mark.asyncio
async def test_failed_job_sets_failed_state_and_specific_reason():
    progress = {
        "run_id": 900,
        "run_number": 251,
        "status": "completed",
        "conclusion": "failure",
        "updated_at": "2026-09-03T12:04:00Z",
        "jobs": [
            {
                "id": 51,
                "name": "deploy",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {
                        "number": 2,
                        "name": "Apply manifests",
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ],
            }
        ],
    }
    instance = SimpleNamespace(
        id="deployment-2",
        instance_name="example",
        status="deploying",
        current_step="validating_configuration",
        failure_reason=None,
        completed_at=None,
        terminated_at=None,
        subdomain=None,
        dns_status=None,
        events=[],
        configuration={"parameters": {"customer_name": "test", "env_instance": "deploy"}},
        dispatch_reference={
            "repo_owner": "amberd-ai",
            "repo_name": "devops",
            "workflow_id": "ada-deploy.yaml",
            "operation": "deploy",
            "run_id": 900,
        },
    )
    db = mock_db()
    client = MagicMock()
    client.get_workflow_progress = AsyncMock(return_value=progress)

    run = _Run("deploy")
    _, error = await _track(db, instance, run, client)

    assert error is None
    # Runs are titled with the workflow's derived host label, not the record name.
    assert client.get_workflow_progress.await_args.kwargs["instance_name"] == (
        "amberd-test-deploy"
    )
    assert instance.status == "failed"
    assert instance.current_step == "failed"
    assert instance.failure_reason == (
        "GitHub Actions job 'deploy' failed at step 'Apply manifests'."
    )
    assert run.conclusion == "failure"

