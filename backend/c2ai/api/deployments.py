"""The tier pages' deployment API: ADA operations and pipeline status.

Endpoints
---------
POST /api/deploy              — Deploy an ADA instance
POST /api/deploy/update       — Update an instance in place (ada-update)
POST /api/deploy/move-tier    — Move an instance to another tier (ada-move-to-tier)
POST /api/deploy/terminate    — Terminate an instance (ada-terminate)
POST /api/pipeline/cancel     — Cancel an operation's GitHub run (requester only)
GET  /api/pipeline/active     — Every in-flight operation, any application type
GET  /api/pipeline/status     — Latest operation on a subdomain, with live progress
GET  /api/pipeline/history    — Operations on a subdomain (no GitHub calls)
GET  /api/github/branches|tags — Refs of an ADA source repository

All of them operate on the one deployment model (``c2ai.deployments``): ADA
is a registered GitHub Workflow application and every operation is a row of
the operation log, whichever API started it.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.core.exceptions import UnprocessableEntityError
from c2ai.db.session import get_db_session as db_session
from c2ai.deployments import ada, operations
from c2ai.deployments.status import latest_status_for_subdomain, list_active_statuses
from c2ai.jobs import JobStore, get_job_store
from c2ai.schemas.deployment import (
    SUBDOMAIN_RE,
    DeployRequest,
    MoveTierRequest,
    PipelineCancelRequest,
    PipelineRunOut,
    PipelineStatusOut,
    TerminateRequest,
)
from c2ai.utils.host_labels import workflow_prepare_subdomain

router = APIRouter(tags=["Deployments"])

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
        description="GitHub repository name within the configured organisation",
    ),
]


def _out(run) -> PipelineRunOut:
    return PipelineRunOut(**run.to_dict())


@router.post(
    "/api/deploy",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Deploy an ADA instance",
)
async def trigger_deployment(
    body: DeployRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
    store: JobStore = Depends(get_job_store),
) -> PipelineRunOut:
    expected = workflow_prepare_subdomain(body.customer_name, body.env_instance)
    if body.subdomain != expected:
        raise UnprocessableEntityError(
            f"subdomain '{body.subdomain}' does not match the expected host label "
            f"'{expected}' derived from customer_name '{body.customer_name}' and "
            f"env_instance '{body.env_instance}'. "
            "For new deployments the subdomain must equal amberd-{sanitised_customer}-{sanitised_env}."
        )
    return _out(await ada.deploy(db, store, body, current_user))


@router.post(
    "/api/deploy/update",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Update an instance in place (ada-update)",
)
async def trigger_deployment_update(
    body: DeployRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
    store: JobStore = Depends(get_job_store),
) -> PipelineRunOut:
    return _out(await ada.update(db, store, body, current_user))


@router.post(
    "/api/deploy/move-tier",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Move an instance to another tier (ada-move-to-tier)",
)
async def move_deployment_to_tier(
    body: MoveTierRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
    store: JobStore = Depends(get_job_store),
) -> PipelineRunOut:
    return _out(await ada.move_tier(db, store, body.subdomain, body.tier, current_user))


@router.post(
    "/api/deploy/terminate",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Terminate an instance (ada-terminate)",
)
async def terminate_deployment(
    body: TerminateRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
    store: JobStore = Depends(get_job_store),
) -> PipelineRunOut:
    return _out(await ada.terminate(db, store, body.subdomain, current_user))


@router.post(
    "/api/pipeline/cancel",
    response_model=PipelineRunOut,
    status_code=status.HTTP_200_OK,
    summary="Cancel the GitHub Actions run of an operation",
)
async def cancel_pipeline(
    body: PipelineCancelRequest,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> PipelineRunOut:
    """Only the user who started the operation may cancel it."""

    return _out(await ada.cancel(db, body.pipeline_run_id, current_user))


@router.get(
    "/api/pipeline/active",
    response_model=list[PipelineStatusOut],
    status_code=status.HTTP_200_OK,
    summary="All in-flight operations across all instances",
)
async def list_active_pipelines(
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> list[PipelineStatusOut]:
    return await list_active_statuses(db)


@router.get(
    "/api/pipeline/status",
    response_model=PipelineStatusOut | None,
    status_code=status.HTTP_200_OK,
    summary="Live status of the latest operation on a subdomain",
)
async def get_pipeline_status(
    subdomain: _SUBDOMAIN_QUERY,
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> PipelineStatusOut | None:
    return await latest_status_for_subdomain(db, subdomain)


@router.get(
    "/api/pipeline/history",
    response_model=list[PipelineRunOut],
    status_code=status.HTTP_200_OK,
    summary="Operation history for a subdomain (no GitHub calls)",
)
async def get_pipeline_history(
    subdomain: _SUBDOMAIN_QUERY,
    limit: int = Query(20, ge=1, le=100),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> list[PipelineRunOut]:
    runs = await operations.operations_for_subdomain(db, subdomain, limit=limit)
    return [_out(run) for run in runs]


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
    return await ada.list_source_refs(repo, "branches")


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
    return await ada.list_source_refs(repo, "tags")
