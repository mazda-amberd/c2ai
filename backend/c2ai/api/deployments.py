# pylint: disable=import-error
"""
API routes for pipeline operations (deploy / move-tier / update / terminate) and status.

Endpoints
---------
POST /api/deploy              — Trigger ada-deploy; returns PipelineRunOut
POST /api/deploy/move-tier    — Trigger ada-move-to-tier; returns PipelineRunOut
POST /api/deploy/update       — Trigger ada-update; returns PipelineRunOut
POST /api/deploy/terminate    — Trigger ada-terminate; returns PipelineRunOut
GET  /api/pipeline/active     — All active operations across all instances
                                (direct pipeline runs + registered-application
                                deployments)
GET  /api/pipeline/status     — Live status for the latest run on a subdomain
GET  /api/pipeline/history    — Past runs for a subdomain (DB only, no GH calls)
POST /api/pipeline/cancel     — Cancel a linked GitHub Actions run (owner-only)
GET  /api/github/branches     — List branches in an Inferaim GitHub repo

Removed (no longer needed):
  POST /api/deploy/webhook/{id}  — GH now polled instead of pushed to
  GET  /api/deployments           — Replaced by /api/pipeline/active
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.application_instance import ApplicationInstance
from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.clients.github import (
    GITHUB_REPO_OWNER,
    GITHUB_WORKFLOW_DEPLOY,
    GITHUB_WORKFLOW_MOVE_TIER,
    GITHUB_WORKFLOW_TERMINATE,
    GITHUB_WORKFLOW_UPDATE,
    cancel_workflow_run,
    check_repo_branch_exists,
    check_repo_tag_exists,
    dispatch_github_terminate_workflow,
    dispatch_github_move_tier_workflow,
    dispatch_github_update_workflow,
    dispatch_github_workflow,
    get_deploy_source_repo,
    get_run_status_cached,
    list_repo_branches,
    list_repo_tags,
    resolve_run_id,
)
from c2ai.crud import pipeline_run as crud_pipeline
from c2ai.services.registered_pipeline_status import (
    list_active_registered_pipeline_statuses,
)
from c2ai.db.session import AsyncSessionLocal
from c2ai.db.session import get_db_session as db_session
from c2ai.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from c2ai.utils.host_labels import workflow_prepare_subdomain
from c2ai.schemas.deployment import (
    SUBDOMAIN_RE,
    DeployRequest,
    MoveTierRequest,
    PipelineCancelRequest,
    PipelineRunOut,
    PipelineStatusOut,
    TerminateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Deployments"])

# Grafana cache staleness threshold — if older, instance checks become advisory.
_GRAFANA_CACHE_STALE = timedelta(minutes=10)

# Validated Query types reused across GET endpoints.
_SUBDOMAIN_QUERY = Annotated[
    str,
    Query(
        ...,
        pattern=SUBDOMAIN_RE.pattern,
        max_length=63,
        description="Instance subdomain (e.g. amberd-acme-ada)",
    ),
]
_REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,100}$")
_REPO_QUERY = Annotated[
    str,
    Query(
        ...,
        pattern=_REPO_NAME_RE.pattern,
        max_length=100,
        description="GitHub repository name within the Inferaim organisation",
    ),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pipeline_run_out(run) -> PipelineRunOut:
    d = run.to_dict()
    return PipelineRunOut(**d)


async def _pipeline_status_out(run) -> PipelineStatusOut:
    base = run.to_dict()
    if run.run_id:
        gh = await get_run_status_cached(run.run_id)
        base.update(gh)
    return PipelineStatusOut(**base)


def _is_successfully_completed(status_out: PipelineStatusOut) -> bool:
    """Successful actions no longer belong in the active-pipeline response."""

    return (
        status_out.gh_status == "completed"
        and status_out.gh_conclusion == "success"
    )


async def _get_instance(db: AsyncSession, subdomain: str) -> Optional[ApplicationInstance]:
    # ApplicationInstance.nodename stores the kubernetes namespace, which matches
    # the subdomain used by the devops workflows (e.g. "amberd-alex-test-ada").
    # ApplicationInstance.name stores the kubernetes resource owner name (e.g. "ada"),
    # which is NOT the same value and must not be used for subdomain lookups.
    result = await db.execute(
        select(ApplicationInstance).where(ApplicationInstance.nodename == subdomain)
    )
    return result.scalar_one_or_none()


def _grafana_cache_is_fresh(instance: ApplicationInstance) -> bool:
    if instance.updated_at is None:
        return False
    updated = instance.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return datetime.now(tz=timezone.utc) - updated < _GRAFANA_CACHE_STALE


async def _guard_no_active_run(db: AsyncSession, subdomain: str) -> None:
    active = await crud_pipeline.get_active_run_for_subdomain(db, subdomain)
    if active:
        raise ConflictError(
            f"Operation '{active.operation}' is already in progress for '{subdomain}'. "
            "Wait for it to complete before starting a new one."
        )


async def _guard_branch_exists(branch: str) -> None:
    owner, repo = get_deploy_source_repo()
    if not await check_repo_branch_exists(owner, repo, branch):
        if not await check_repo_tag_exists(owner, repo, branch):
            raise UnprocessableEntityError(
                f"Branch or tag '{branch}' does not exist in {owner}/{repo}."
            )


async def _guard_instance_not_running(db: AsyncSession, subdomain: str) -> None:
    """For deploy: 409 if the instance already exists in Grafana (fresh cache only)."""
    instance = await _get_instance(db, subdomain)
    if instance and _grafana_cache_is_fresh(instance):
        raise ConflictError(
            f"Instance '{subdomain}' is already running. "
            "Use the update operation to redeploy it."
        )
    if instance:
        logger.warning(
            "deploy guard: instance %r exists but Grafana cache is stale — allowing",
            subdomain,
        )


async def _guard_instance_exists(db: AsyncSession, subdomain: str, operation: str) -> None:
    """For update/terminate: 422 if the instance is not in Grafana (fresh cache only)."""
    instance = await _get_instance(db, subdomain)
    if instance:
        return
    # Check if cache itself is stale — if so, be advisory-only
    result = await db.execute(select(ApplicationInstance).limit(1))
    any_instance = result.scalar_one_or_none()
    if any_instance and _grafana_cache_is_fresh(any_instance):
        raise UnprocessableEntityError(
            f"Instance '{subdomain}' was not found in the instance inventory. "
            f"Cannot {operation} a non-existent instance."
        )
    logger.warning(
        "%s guard: instance %r not found but Grafana cache may be stale — allowing",
        operation,
        subdomain,
    )


def _schedule_resolve(coro) -> None:
    """Schedule a background coroutine. Thin wrapper for test patching."""
    asyncio.create_task(coro)


async def _background_resolve(
    pipeline_run_id: str,
    workflow_file: str,
    subdomain: str,
    dispatched_at: datetime,
    customer_name: Optional[str] = None,
    env_instance: Optional[str] = None,
) -> None:
    """
    Background task: poll GH until the run_id is found, then persist it.
    Retries every 5 seconds for up to 60 seconds (initial 8s delay for GH queue time).
    """
    await asyncio.sleep(8)
    for attempt in range(12):
        run_id = await resolve_run_id(
            workflow_file,
            subdomain,
            dispatched_at,
            customer_name=customer_name,
            env_instance=env_instance,
        )
        if run_id:
            async with AsyncSessionLocal() as db:
                await crud_pipeline.set_run_id(db, pipeline_run_id, run_id)
            return
        if attempt < 11:
            await asyncio.sleep(5)

    logger.warning(
        "Could not resolve run_id for pipeline_run=%s after 60 s", pipeline_run_id
    )


async def _writeback_ended_if_complete(db: AsyncSession, run) -> None:
    """If GH says completed and we haven't marked it yet, do so now."""
    if run.run_id and run.ended_at is None:
        gh = await get_run_status_cached(run.run_id)
        if gh.get("gh_status") == "completed":
            await crud_pipeline.mark_run_ended(db, run.id)


# ---------------------------------------------------------------------------
# Trigger endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/api/deploy",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger a new deployment (ada-deploy)",
)
async def trigger_deployment(
    body: DeployRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> PipelineRunOut:
    subdomain = body.subdomain.strip()

    expected = workflow_prepare_subdomain(body.customer_name, body.env_instance)
    if subdomain != expected:
        raise UnprocessableEntityError(
            f"subdomain '{subdomain}' does not match the expected host label "
            f"'{expected}' derived from customer_name '{body.customer_name}' and "
            f"env_instance '{body.env_instance}'. "
            "For new deployments the subdomain must equal amberd-{sanitised_customer}-{sanitised_env}."
        )

    await _guard_no_active_run(db, subdomain)
    await _guard_instance_not_running(db, subdomain)
    await _guard_branch_exists(body.branch)

    correlation_id = str(uuid.uuid4())
    dispatched_at = datetime.now(tz=timezone.utc)

    run = await crud_pipeline.create_pipeline_run(
        db,
        id=correlation_id,
        subdomain=subdomain,
        operation="deploy",
        event_type=GITHUB_WORKFLOW_DEPLOY,
        triggered_by=current_user.identifier,
        tier=body.tier,
        branch=body.branch,
    )

    await dispatch_github_workflow(
        correlation_id=correlation_id,
        branch=body.branch,
        customer_name=body.customer_name,
        subdomain=subdomain,
        domain=body.domain,
        env_instance=body.env_instance,
        tier=body.tier,
        triggered_by=current_user.identifier,
        slack_user=current_user.metadata.get("slack_username") or None,
    )

    _schedule_resolve(
        _background_resolve(
            correlation_id,
            GITHUB_WORKFLOW_DEPLOY,
            subdomain,
            dispatched_at,
            customer_name=body.customer_name,
            env_instance=body.env_instance,
        )
    )

    return _pipeline_run_out(run)


@router.post(
    "/api/deploy/move-tier",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Move a live deployment to another tier (ada-move-to-tier)",
)
async def move_deployment_to_tier(
    body: MoveTierRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> PipelineRunOut:
    """
    Trigger a DevOps move-to-tier workflow for an existing deployment.

    Args:
        body (MoveTierRequest): Move-tier request payload from Athena.
        current_user (AthenaTokenUser): Authenticated Athena user triggering the action.
        db (AsyncSession): Active database session.

    Returns:
        PipelineRunOut: Created pipeline run metadata returned immediately after dispatch.

    Raises:
        ConflictError: If another pipeline operation is already active for the instance.
        UnprocessableEntityError: If the target instance does not exist.
        ServiceUnavailableError: If GitHub integration is unavailable during dispatch.
        BadRequestError: If GitHub rejects the workflow dispatch request.
    """
    subdomain = body.subdomain.strip()

    await _guard_no_active_run(db, subdomain)
    await _guard_instance_exists(db, subdomain, "move-tier")

    correlation_id = str(uuid.uuid4())
    dispatched_at = datetime.now(tz=timezone.utc)

    run = await crud_pipeline.create_pipeline_run(
        db,
        id=correlation_id,
        subdomain=subdomain,
        operation="migration",
        event_type=GITHUB_WORKFLOW_MOVE_TIER,
        triggered_by=current_user.identifier,
        tier=body.tier,
    )

    await dispatch_github_move_tier_workflow(
        correlation_id=correlation_id,
        subdomain=subdomain,
        tier=body.tier,
        triggered_by=current_user.metadata.get("slack_username")
        or current_user.identifier,
    )

    _schedule_resolve(
        _background_resolve(
            correlation_id,
            GITHUB_WORKFLOW_MOVE_TIER,
            subdomain,
            dispatched_at,
        )
    )

    return _pipeline_run_out(run)


@router.post(
    "/api/deploy/update",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger an in-place update (ada-update)",
)
async def trigger_deployment_update(
    body: DeployRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> PipelineRunOut:
    subdomain = body.subdomain.strip()

    await _guard_no_active_run(db, subdomain)
    await _guard_instance_exists(db, subdomain, "update")
    await _guard_branch_exists(body.branch)

    correlation_id = str(uuid.uuid4())
    dispatched_at = datetime.now(tz=timezone.utc)

    run = await crud_pipeline.create_pipeline_run(
        db,
        id=correlation_id,
        subdomain=subdomain,
        operation="update",
        event_type=GITHUB_WORKFLOW_UPDATE,
        triggered_by=current_user.identifier,
        tier=body.tier,
        branch=body.branch,
    )

    await dispatch_github_update_workflow(
        correlation_id=correlation_id,
        branch=body.branch,
        subdomain=subdomain,
        triggered_by=current_user.identifier,
    )

    _schedule_resolve(
        _background_resolve(
            correlation_id,
            GITHUB_WORKFLOW_UPDATE,
            subdomain,
            dispatched_at,
        )
    )

    return _pipeline_run_out(run)


@router.post(
    "/api/deploy/terminate",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Terminate a live deployment (ada-terminate)",
)
async def terminate_deployment(
    body: TerminateRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> PipelineRunOut:
    subdomain = body.subdomain.strip()

    await _guard_no_active_run(db, subdomain)
    await _guard_instance_exists(db, subdomain, "terminate")

    correlation_id = str(uuid.uuid4())
    dispatched_at = datetime.now(tz=timezone.utc)

    run = await crud_pipeline.create_pipeline_run(
        db,
        id=correlation_id,
        subdomain=subdomain,
        operation="terminate",
        event_type=GITHUB_WORKFLOW_TERMINATE,
        triggered_by=current_user.identifier,
    )

    await dispatch_github_terminate_workflow(
        subdomain,
        correlation_id=correlation_id,
        triggered_by=current_user.identifier,
    )

    _schedule_resolve(
        _background_resolve(
            correlation_id,
            GITHUB_WORKFLOW_TERMINATE,
            subdomain,
            dispatched_at,
        )
    )

    return _pipeline_run_out(run)


@router.post(
    "/api/pipeline/cancel",
    response_model=PipelineRunOut,
    status_code=status.HTTP_200_OK,
    summary="Cancel a GitHub Actions run linked to a pipeline run",
)
async def cancel_pipeline(
    body: PipelineCancelRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> PipelineRunOut:
    """
    Calls GitHub ``POST .../actions/runs/{run_id}/cancel`` then marks the row ended.

    Only the user who triggered the run (``triggered_by``) may cancel it.
    Requires ``run_id`` to be resolved first (usually within a few seconds of dispatch).
    """
    run = await crud_pipeline.get_pipeline_run_by_id(db, body.pipeline_run_id)
    if not run:
        raise NotFoundError(f"Pipeline run '{body.pipeline_run_id}' not found.")
    if run.ended_at is not None:
        raise ConflictError("This pipeline run has already finished.")
    if run.triggered_by != current_user.identifier:
        raise ForbiddenError("You can only cancel runs that you started.")
    if run.run_id is None:
        raise ConflictError(
            "Cannot cancel yet: the GitHub Actions run is not linked. "
            "Wait a few seconds and try again."
        )

    await cancel_workflow_run(int(run.run_id))
    await crud_pipeline.mark_run_ended(db, run.id)
    updated = await crud_pipeline.get_pipeline_run_by_id(db, body.pipeline_run_id)
    if not updated:
        raise NotFoundError("Pipeline run disappeared after cancel.")
    return _pipeline_run_out(updated)


# ---------------------------------------------------------------------------
# Status / history endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/api/pipeline/active",
    response_model=list[PipelineStatusOut],
    status_code=status.HTTP_200_OK,
    summary="All active pipeline operations across all instances",
)
async def list_active_pipelines(
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> list[PipelineStatusOut]:
    """
    Returns live status for every non-ended pipeline_run, plus registered
    application deployments projected onto the same shape (they are tracked in
    ``deployment_instances``, not ``pipeline_runs``) so one poll covers every
    in-flight operation.
    Uses the in-process GH status cache — safe for frequent polling by many clients.
    Also writes back ``ended_at`` when a run transitions to completed.
    """
    runs = await crud_pipeline.get_all_active_runs(db)
    results = []
    for run in runs:
        await _writeback_ended_if_complete(db, run)
        status_out = await _pipeline_status_out(run)
        if not _is_successfully_completed(status_out):
            results.append(status_out)
    registered_statuses = await list_active_registered_pipeline_statuses(db)
    results.extend(
        status_out
        for status_out in registered_statuses
        if not _is_successfully_completed(status_out)
    )
    return results


@router.get(
    "/api/pipeline/status",
    response_model=Optional[PipelineStatusOut],
    status_code=status.HTTP_200_OK,
    summary="Live status for the latest pipeline run on a subdomain",
)
async def get_pipeline_status(
    subdomain: _SUBDOMAIN_QUERY,
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> Optional[PipelineStatusOut]:
    run = await crud_pipeline.get_latest_run_for_subdomain(db, subdomain)
    if not run:
        return None
    await _writeback_ended_if_complete(db, run)
    return await _pipeline_status_out(run)


@router.get(
    "/api/pipeline/history",
    response_model=list[PipelineRunOut],
    status_code=status.HTTP_200_OK,
    summary="Deployment history for a subdomain (DB only, no GH API calls)",
)
async def get_pipeline_history(
    subdomain: _SUBDOMAIN_QUERY,
    limit: int = Query(20, ge=1, le=100),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> list[PipelineRunOut]:
    runs = await crud_pipeline.get_runs_for_subdomain(db, subdomain, limit=limit)
    return [_pipeline_run_out(r) for r in runs]


# ---------------------------------------------------------------------------
# GitHub branches / tags helpers
# ---------------------------------------------------------------------------

@router.get(
    "/api/github/branches",
    response_model=list[str],
    status_code=status.HTTP_200_OK,
    summary="List branches for a GitHub repository",
)
async def get_repo_branches(
    repo: _REPO_QUERY,
    _user: AthenaTokenUser = Depends(get_current_user_token),
) -> list[str]:
    return await list_repo_branches(GITHUB_REPO_OWNER, repo)


@router.get(
    "/api/github/tags",
    response_model=list[str],
    status_code=status.HTTP_200_OK,
    summary="List tags for a GitHub repository",
)
async def get_repo_tags(
    repo: _REPO_QUERY,
    _user: AthenaTokenUser = Depends(get_current_user_token),
) -> list[str]:
    return await list_repo_tags(GITHUB_REPO_OWNER, repo)
