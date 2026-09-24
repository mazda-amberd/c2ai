"""Tests for projecting registered deployments onto the pipeline status contract."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from c2ai.services import registered_pipeline_status as module
from c2ai.services.registered_pipeline_status import (
    list_active_registered_pipeline_statuses,
)


def _instance(**overrides):
    created_at = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    values = {
        "id": "cccccccc-0000-0000-0000-000000000003",
        "instance_name": "ada-tier-1",
        "tier": 1,
        "status": "deploying",
        "current_step": "waiting_for_rollout",
        "failure_reason": None,
        "completed_at": None,
        "terminated_at": None,
        "subdomain": "amberd-acme-ada",
        "triggered_by": "test-user",
        "configuration": {"version": "v1.4.0", "parameters": {}},
        "dispatch_reference": {
            "repo_owner": "amberd-ai",
            "repo_name": "devops",
            "workflow_id": "ada-deploy.yaml",
            "operation": "deploy",
            "run_id": 891,
        },
        "events": [],
        "created_at": created_at,
        "updated_at": created_at,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _clear_cache():
    module._status_cache.clear()
    yield
    module._status_cache.clear()


def _patch_instances(instances):
    return patch.object(
        module,
        "_load_visible_instances",
        new_callable=AsyncMock,
        return_value=instances,
    )


def _patch_progress(result):
    return patch.object(
        module,
        "get_registered_deployment_workflow_progress",
        new_callable=AsyncMock,
        return_value=result,
    )


@pytest.mark.asyncio
async def test_live_github_job_and_step_become_status_fields():
    progress = {
        "run_id": 891,
        "status": "in_progress",
        "conclusion": None,
        "html_url": "https://github.com/amberd-ai/devops/actions/runs/891",
        "created_at": "2026-09-03T12:00:10Z",
        "updated_at": "2026-09-03T12:02:00Z",
        "jobs": [
            {
                "name": "deploy",
                "status": "in_progress",
                "steps": [
                    {"name": "Checkout", "status": "completed"},
                    {"name": "Helm upgrade", "status": "in_progress"},
                ],
            }
        ],
    }

    with _patch_instances([_instance()]), _patch_progress((progress, None)):
        statuses = await list_active_registered_pipeline_statuses(MagicMock())

    assert len(statuses) == 1
    status = statuses[0]
    # The id is the deployment id the deploy modal already holds.
    assert status.id == "cccccccc-0000-0000-0000-000000000003"
    assert status.subdomain == "amberd-acme-ada"
    assert status.operation == "deploy"
    assert status.tier == 1
    assert status.branch == "v1.4.0"
    assert status.run_id == 891
    assert status.gh_status == "in_progress"
    assert status.active_job == "deploy"
    assert status.current_step == "Helm upgrade"
    assert status.ended_at is None


@pytest.mark.asyncio
async def test_falls_back_to_athena_progress_without_github_run():
    """Container pipelines have no GitHub run — the DB record still reports."""
    instance = _instance(
        dispatch_reference={"pipeline": "container-deploy"},
        events=[SimpleNamespace(message="Applying resources to tier1.")],
    )

    with _patch_instances([instance]), _patch_progress((None, None)):
        statuses = await list_active_registered_pipeline_statuses(MagicMock())

    status = statuses[0]
    assert status.gh_status == "in_progress"
    assert status.gh_conclusion is None
    assert status.active_job == "Waiting for rollout"
    assert status.current_step == "Applying resources to tier1."
    assert status.event_type == "container-deploy"


@pytest.mark.asyncio
async def test_finished_deployment_reports_conclusion_and_end():
    completed_at = datetime(2026, 9, 3, 12, 5, 0, tzinfo=timezone.utc)
    instance = _instance(
        status="failed",
        current_step="failed",
        failure_reason="GitHub Actions job 'deploy' failed at step 'Helm upgrade'.",
        completed_at=completed_at,
        updated_at=completed_at,
    )

    with _patch_instances([instance]), _patch_progress((None, None)):
        statuses = await list_active_registered_pipeline_statuses(MagicMock())

    status = statuses[0]
    assert status.gh_status == "completed"
    assert status.gh_conclusion == "failure"
    assert status.current_step == (
        "GitHub Actions job 'deploy' failed at step 'Helm upgrade'."
    )
    assert status.ended_at == completed_at.isoformat()


@pytest.mark.asyncio
async def test_upgrade_and_terminate_map_onto_pipeline_operations():
    upgrade = _instance(status="updating", dispatch_reference={"operation": "upgrade"})
    terminate = _instance(
        id="dddddddd-0000-0000-0000-000000000004",
        status="terminating",
        dispatch_reference={},
    )

    with _patch_instances([upgrade, terminate]), _patch_progress((None, None)):
        statuses = await list_active_registered_pipeline_statuses(MagicMock())

    assert [s.operation for s in statuses] == ["update", "terminate"]


@pytest.mark.asyncio
async def test_active_lookup_is_cached_between_polls():
    instance = _instance()

    with _patch_instances([instance]), _patch_progress((None, None)) as progress:
        await list_active_registered_pipeline_statuses(MagicMock())
        await list_active_registered_pipeline_statuses(MagicMock())

    progress.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_terminal_state_is_removed_from_active_results():
    instance = _instance()

    with _patch_instances([instance]), _patch_progress((None, None)) as progress:
        await list_active_registered_pipeline_statuses(MagicMock())
        instance.status = "running"
        instance.completed_at = instance.created_at + timedelta(minutes=4)
        await list_active_registered_pipeline_statuses(MagicMock())
        statuses = await list_active_registered_pipeline_statuses(MagicMock())

    assert progress.await_count == 3
    assert statuses == []


@pytest.mark.parametrize(
    ("configuration", "expected"),
    [
        ({"container": {"image_tag": "2.1.0"}}, "2.1.0"),
        ({"github": {"version": "v3"}, "parameters": {"branch": "v2"}}, "v3"),
        ({"github": {}, "parameters": {"branch": "release/7"}}, "release/7"),
        ({"parameters": {}}, "main"),  # only the workflow ref is known
    ],
)
def test_version_reads_where_snapshots_store_it(configuration, expected):
    instance = _instance(
        configuration=configuration,
        dispatch_reference={"ref": "main", "operation": "deploy"},
    )
    assert module._version(instance) == expected
