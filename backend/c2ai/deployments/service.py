"""Deployment operations: the transaction boundary and the dispatch outbox.

Every lifecycle operation runs in the same three phases:

1. **Stage** — in one transaction: lock the instance, move it into the
   operation's in-progress state, open the operation-log row (with a restore
   snapshot), and write a ``deployments.dispatch`` job. Commit. From here on
   the operation is durable and no lock is held.
2. **Dispatch** — read the credentials, end that read, call the pipeline
   (GitHub) with no transaction open.
3. **Record** — in a short transaction: lock the instance, record the
   dispatch reference (or, if the pipeline never received it, put the
   instance back and close the operation as failed). Commit.

The request that staged the operation runs phases 2–3 itself so the caller
still gets an immediate answer. If that request dies half way, the job is
still in the queue and the worker (or the tracker's cleanup) finishes it —
the database never holds an operation that nobody will ever complete.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.config import get_settings
from c2ai.constants.registered_application import (
    ApplicationType,
    DeploymentInstanceStatus,
)
from c2ai.core.exceptions import (
    AppException,
    DeploymentInstanceNotFound,
    ServiceUnavailableError,
)
from c2ai.crud import github_connection as crud_github_connection
from c2ai.deployments import callbacks, operations, pipelines, repository as instances
from c2ai.deployments.configuration import configured_version
from c2ai.jobs.store import FINISHED, SUCCEEDED, JobRecord, JobStore
from c2ai.models.registered_application import (
    DeploymentInstance,
    RegisteredApplicationVersion,
)
from c2ai.registration import credentials

logger = logging.getLogger(__name__)

DISPATCH_JOB = "deployments.dispatch"
DISPATCH_LEASE = timedelta(minutes=2)
# How long a request waits for a dispatch another worker picked up first.
_WAIT_FOR_WORKER = timedelta(seconds=30)
_INLINE_WORKER_ID = f"api:{os.getpid()}"

# Words for "The <label> pipeline could not be triggered."
_LABELS = {
    "deploy": "deployment",
    "redeploy": "rollback",
    "rollback": "rollback",
    "upgrade": "upgrade",
    "move_tier": "move-to-tier",
    "terminate": "termination",
}


class DispatchFailed(Exception):
    """The pipeline never received the operation; ``error`` is what the API returns."""

    def __init__(self, error: AppException):
        super().__init__(str(error.detail))
        self.error = error


@dataclass(frozen=True)
class DispatchRequest:
    """What phase 2 must send, stored as the dispatch job's payload."""

    kind: str
    event_message: str
    triggered_by: str
    target_version: str | None = None
    target_tier: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def payload(self, instance: DeploymentInstance, operation_id: str) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "instance_id": str(instance.id),
            "kind": self.kind,
            "event_message": self.event_message,
            "triggered_by": self.triggered_by,
            "target_version": self.target_version,
            "target_tier": self.target_tier,
            **self.extra,
        }


# ---------------------------------------------------------------------------
# Phase 1 + inline phases 2–3
# ---------------------------------------------------------------------------


async def _stage_and_dispatch(
    db: AsyncSession,
    store: JobStore,
    instance: DeploymentInstance,
    request: DispatchRequest,
) -> DeploymentInstance:
    run = await operations.active_operation(db, instance.id)
    if run is None:  # staged by the caller in this transaction
        raise RuntimeError("No open operation to dispatch")
    job = await store.enqueue(
        DISPATCH_JOB,
        request.payload(instance, run.id),
        max_attempts=1,  # a dispatch is never repeated blindly (it is not idempotent)
        session=db,
    )
    await db.commit()
    return await _dispatch_now(db, store, job)


async def _dispatch_now(db: AsyncSession, store: JobStore, job: JobRecord) -> DeploymentInstance:
    instance_id = UUID(job.payload["instance_id"])
    claimed = await store.claim_job(job.id, _INLINE_WORKER_ID, DISPATCH_LEASE)
    if claimed is None:
        return await _wait_for_worker(db, store, job.id, instance_id)
    try:
        instance = await dispatch_operation(db, claimed.payload)
    except DispatchFailed as failure:
        error = failure.error
        await store.fail(job.id, _INLINE_WORKER_ID, code=error.code, message=str(error.detail))
        raise error from failure
    except BaseException:
        await store.fail(
            job.id,
            _INLINE_WORKER_ID,
            code="DispatchInterrupted",
            message="The request dispatching this operation stopped.",
        )
        raise
    await store.succeed(job.id, _INLINE_WORKER_ID, {"dispatched": True})
    return instance


async def _wait_for_worker(
    db: AsyncSession, store: JobStore, job_id: UUID, instance_id: UUID
) -> DeploymentInstance:
    deadline = asyncio.get_running_loop().time() + _WAIT_FOR_WORKER.total_seconds()
    job = await store.get(job_id)
    while job is not None and job.status not in FINISHED:
        if asyncio.get_running_loop().time() > deadline:
            break
        await asyncio.sleep(0.2)
        job = await store.get(job_id)
    if job is not None and job.status in FINISHED and job.status != SUCCEEDED:
        raise ServiceUnavailableError(
            job.error_message or "The pipeline could not be triggered.",
            code=job.error_code,
        )
    db.expire_all()
    instance = await instances.get_registered_application_deployment(db, instance_id)
    if instance is None:
        raise DeploymentInstanceNotFound(instance_id)
    return instance


# ---------------------------------------------------------------------------
# Phases 2–3 (inline, or from the deployments.dispatch job)
# ---------------------------------------------------------------------------


async def _deployment_credentials(
    db: AsyncSession, version: RegisteredApplicationVersion
) -> dict[str, Any]:
    """The registry, LLM and GitHub credentials a deploy dispatch sends."""

    options: dict[str, Any] = {}
    if version.application.application_type == ApplicationType.GITHUB_WORKFLOW.value:
        github = version.github_configuration
        if github is not None:
            runtime = await crud_github_connection.resolve_github_connection(
                db, github.github_connection_id
            )
            if runtime is not None:
                options["github_token"] = runtime.token
                options["github_api_base_url"] = runtime.api_base_url
        return options
    template = version.container_configuration
    if template is not None and template.registry_password_encrypted is not None:
        registry = await credentials.resolve_container_registry_credentials(db, version.id)
        if registry is not None:
            options["registry_username"] = registry.username
            options["registry_token"] = registry.password
    options["llm_api_token"] = await credentials.resolve_llm_api_token(db, version.id)
    return options


def _callback_token(instance_id: UUID, operation_id: str) -> str | None:
    if not get_settings().deployment_callback_token:
        return None
    return callbacks.callback_token(instance_id, operation_id)


async def _send(
    instance: DeploymentInstance,
    payload: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Call the pipeline for one staged operation (no database access)."""

    version = instance.application_version
    common = {
        "deployment_id": instance.id,
        "instance_name": instance.instance_name,
        "configuration": instance.configuration,
        "triggered_by": payload["triggered_by"],
    }
    token = _callback_token(instance.id, payload["operation_id"])
    kind = payload["kind"]
    if kind in ("deploy", "redeploy"):
        return await pipelines.dispatch_registered_application_deployment(
            version, tier=instance.tier, callback_token=token, **common, **options
        )
    if kind in ("upgrade", "rollback"):
        return await pipelines.dispatch_registered_application_upgrade(
            version,
            tier=instance.tier,
            target_version=payload["target_version"],
            rollback=kind == "rollback",
            callback_token=token,
            **common,
        )
    if kind == "move_tier":
        return await pipelines.dispatch_registered_application_move_tier(
            version, target_tier=int(payload["target_tier"]), **common
        )
    if kind == "terminate":
        return await pipelines.dispatch_registered_application_termination(
            version, tier=instance.tier, callback_token=token, **common
        )
    raise ValueError(f"Unknown dispatch kind '{kind}'")


async def dispatch_operation(db: AsyncSession, payload: dict[str, Any]) -> DeploymentInstance:
    """Send one staged operation to its pipeline and record the outcome.

    Idempotent: an operation that is no longer pending is left alone. The
    caller must hold no open transaction; this function commits its writes.
    """

    instance_id = UUID(payload["instance_id"])
    instance = await instances.get_registered_application_deployment(db, instance_id)
    run = await operations.get_operation(db, payload["operation_id"])
    if instance is None or run is None:
        await db.commit()
        raise DeploymentInstanceNotFound(instance_id)
    if run.dispatch_state != "pending" or run.ended_at is not None:
        await db.commit()
        return instance
    options = (
        await _deployment_credentials(db, instance.application_version)
        if payload["kind"] in ("deploy", "redeploy")
        else {}
    )
    # End the read transaction before calling GitHub. (commit, not rollback:
    # a rollback would expire the loaded instance.)
    await db.commit()

    label = _LABELS.get(payload["kind"], payload["kind"])
    try:
        reference = await _send(instance, payload, options)
    except AppException as error:
        await _compensate(db, instance_id, str(error.detail))
        raise DispatchFailed(error) from error
    except Exception as error:
        logger.exception("Dispatch failed operation=%s kind=%s", run.id, payload["kind"])
        await _compensate(db, instance_id, f"{type(error).__name__}: {error}")
        raise DispatchFailed(
            ServiceUnavailableError(f"The {label} pipeline could not be triggered.")
        ) from error

    instance = await instances.get_registered_application_deployment(
        db, instance_id, for_update=True
    )
    run = await operations.get_operation(db, payload["operation_id"])
    if run is None or run.dispatch_state != "pending":
        # Cleaned up while GitHub was answering (e.g. the tracker gave up).
        logger.warning("Operation %s changed during dispatch; keeping its state", payload)
        await db.commit()
        return instance
    instance = await instances.record_dispatch(
        db, instance, reference, event_message=payload["event_message"]
    )
    await db.commit()
    logger.info("Dispatched %s for deployment %s", payload["kind"], instance_id)
    return instance


async def _compensate(db: AsyncSession, instance_id: UUID, reason: str) -> None:
    instance = await instances.get_registered_application_deployment(
        db, instance_id, for_update=True
    )
    if instance is not None:
        await instances.fail_dispatch(db, instance, reason)
    await db.commit()


# ---------------------------------------------------------------------------
# Operations (called by the API and the ADA layer)
# ---------------------------------------------------------------------------


async def deploy(
    db: AsyncSession,
    store: JobStore,
    version: RegisteredApplicationVersion,
    *,
    instance_name: str,
    tier: int,
    configuration: dict,
    triggered_by: str,
    dispatch_user: str | None = None,
) -> DeploymentInstance:
    instance = await instances.create_registered_application_deployment(
        db,
        version,
        instance_name=instance_name,
        tier=tier,
        configuration=configuration,
        triggered_by=triggered_by,
    )
    return await _stage_and_dispatch(
        db,
        store,
        instance,
        DispatchRequest(
            kind="deploy",
            event_message="Deployment pipeline dispatched.",
            triggered_by=dispatch_user or triggered_by,
        ),
    )


async def upgrade(
    db: AsyncSession,
    store: JobStore,
    deployment_id: UUID,
    *,
    target_version: str,
    triggered_by: str,
    allow_same_version: bool = False,
) -> DeploymentInstance:
    instance = await instances.prepare_registered_application_upgrade(
        db,
        deployment_id,
        target_version=target_version,
        triggered_by=triggered_by,
        allow_same_version=allow_same_version,
    )
    return await _stage_and_dispatch(
        db,
        store,
        instance,
        DispatchRequest(
            kind="upgrade",
            event_message=(
                f"Application upgrade pipeline dispatched for version '{target_version}'."
            ),
            triggered_by=triggered_by,
            target_version=target_version,
        ),
    )


async def rollback(
    db: AsyncSession, store: JobStore, deployment_id: UUID, *, triggered_by: str
) -> DeploymentInstance:
    """Return to the previous version, or redeploy an instance never upgraded."""

    instance = await instances.prepare_registered_application_rollback(
        db, deployment_id, triggered_by=triggered_by
    )
    count = instance.rollback_count
    if instance.status == DeploymentInstanceStatus.UPDATING.value:
        version = configured_version(
            instance.configuration, instance.application.application_type
        )
        request = DispatchRequest(
            kind="rollback",
            event_message=f"Rollback #{count} pipeline dispatched for version '{version}'.",
            triggered_by=triggered_by,
            target_version=version,
        )
    else:
        request = DispatchRequest(
            kind="redeploy",
            event_message=f"Rollback #{count} pipeline dispatched.",
            triggered_by=triggered_by,
        )
    return await _stage_and_dispatch(db, store, instance, request)


async def move_tier(
    db: AsyncSession,
    store: JobStore,
    deployment_id: UUID,
    *,
    target_tier: int,
    triggered_by: str,
    dispatch_user: str | None = None,
) -> DeploymentInstance:
    instance = await instances.prepare_registered_application_move_tier(
        db, deployment_id, target_tier=target_tier, triggered_by=triggered_by
    )
    return await _stage_and_dispatch(
        db,
        store,
        instance,
        DispatchRequest(
            kind="move_tier",
            event_message=f"Move to Tier {target_tier} pipeline dispatched.",
            triggered_by=dispatch_user or triggered_by,
            target_tier=target_tier,
        ),
    )


async def terminate(
    db: AsyncSession, store: JobStore, deployment_id: UUID, *, triggered_by: str
) -> DeploymentInstance:
    instance = await instances.prepare_registered_application_termination(
        db, deployment_id, triggered_by=triggered_by
    )
    return await _stage_and_dispatch(
        db,
        store,
        instance,
        DispatchRequest(
            kind="terminate",
            event_message="Application termination pipeline dispatched.",
            triggered_by=triggered_by,
        ),
    )
