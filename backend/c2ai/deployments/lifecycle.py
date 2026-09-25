"""The deployment lifecycle state machine.

Every status change of a deployment instance goes through this module:
``begin`` when an operation is requested, ``settle`` when its pipeline reports
a final outcome (progress callback, GitHub's run conclusion, a cancel, or the
reconciler giving up). Nothing else assigns ``DeploymentInstance.status``.

    operation      may start from        while running    on success
    -------------  --------------------  ---------------  -------------------
    deploy         (new instance)        deploying        running
    upgrade        running, failed       updating         running
    rollback       running, failed       updating*        running
    move_tier      running, failed       updating         running (new tier)
    terminate      running, failed       terminating      terminated

    * a rollback of an instance that was never upgraded redeploys it
      ("deploying").

Any failure settles the instance as ``failed`` (it can be retried, rolled back
or terminated); a cancelled first deployment settles as ``cancelled``.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from c2ai.constants.registered_application import (
    DeploymentInstanceStatus as Status,
    DeploymentStep,
)
from c2ai.core.exceptions import (
    AppException,
    DeploymentRollbackNotAvailable,
    DeploymentTerminationNotAvailable,
    DeploymentUpgradeNotAvailable,
    UnprocessableEntityError,
)


class Operation(str, Enum):
    DEPLOY = "deploy"
    UPGRADE = "upgrade"
    ROLLBACK = "rollback"
    MOVE_TIER = "move_tier"
    TERMINATE = "terminate"


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    # The reconciler closed an operation whose pipeline never reported back.
    ABANDONED = "abandoned"


# The pipeline_runs.operation vocabulary the UI has always used.
LOG_OPERATION = {
    Operation.DEPLOY: "deploy",
    Operation.UPGRADE: "update",
    Operation.ROLLBACK: "update",
    Operation.MOVE_TIER: "migration",
    Operation.TERMINATE: "terminate",
}

IN_PROGRESS = frozenset(
    {Status.PENDING.value, Status.DEPLOYING.value, Status.UPDATING.value, Status.TERMINATING.value}
)
FINISHED = frozenset({Status.TERMINATED.value, Status.CANCELLED.value})
_OPERABLE = frozenset({Status.RUNNING.value, Status.FAILED.value})


def _move_not_available(status: str) -> AppException:
    return UnprocessableEntityError(
        f"The deployment cannot move to another tier while it is '{status}'."
    )


@dataclass(frozen=True)
class _Rule:
    allowed_from: frozenset[str]
    in_progress: str
    not_available: Callable[[str], AppException]


_RULES: dict[Operation, _Rule] = {
    Operation.UPGRADE: _Rule(_OPERABLE, Status.UPDATING.value, DeploymentUpgradeNotAvailable),
    Operation.ROLLBACK: _Rule(_OPERABLE, Status.UPDATING.value, DeploymentRollbackNotAvailable),
    Operation.MOVE_TIER: _Rule(_OPERABLE, Status.UPDATING.value, _move_not_available),
    Operation.TERMINATE: _Rule(
        _OPERABLE, Status.TERMINATING.value, DeploymentTerminationNotAvailable
    ),
}


def ensure_can_begin(instance, operation: Operation) -> None:
    """Raise the operation's "not available" error unless it may start now."""

    rule = _RULES.get(operation)
    if rule is not None and instance.status not in rule.allowed_from:
        raise rule.not_available(instance.status)


def begin(instance, operation: Operation, *, triggered_by: str, redeploy: bool = False) -> None:
    """Move ``instance`` into the in-progress state of ``operation``.

    ``redeploy`` marks a rollback that re-applies the stored configuration.
    """

    if operation is Operation.DEPLOY or (operation is Operation.ROLLBACK and redeploy):
        if operation is not Operation.DEPLOY:
            ensure_can_begin(instance, operation)
        instance.status = (
            Status.PENDING.value if operation is Operation.DEPLOY else Status.DEPLOYING.value
        )
        if instance.subdomain:
            instance.dns_status = "pending"
    else:
        ensure_can_begin(instance, operation)
        instance.status = _RULES[operation].in_progress
    instance.current_step = DeploymentStep.VALIDATING_CONFIGURATION.value
    instance.failure_reason = None
    instance.completed_at = None
    if operation is Operation.TERMINATE:
        instance.terminated_at = None
    instance.triggered_by = triggered_by


def mark_dispatched(instance) -> None:
    """A first deployment's pipeline was accepted: pending → deploying."""

    if instance.status == Status.PENDING.value:
        instance.status = Status.DEPLOYING.value


def settled_status(operation: Operation, outcome: Outcome) -> str:
    if outcome is Outcome.SUCCESS:
        return Status.TERMINATED.value if operation is Operation.TERMINATE else Status.RUNNING.value
    if outcome is Outcome.CANCELLED and operation is Operation.DEPLOY:
        return Status.CANCELLED.value
    return Status.FAILED.value


def settle(
    instance,
    operation: Operation,
    outcome: Outcome,
    *,
    at: datetime | None = None,
    failure_reason: str | None = None,
    target_tier: int | None = None,
) -> str:
    """Apply an operation's final outcome to ``instance``; return the new status."""

    at = at or datetime.now(UTC)
    success = outcome is Outcome.SUCCESS
    status = settled_status(operation, outcome)
    instance.status = status
    instance.current_step = (
        DeploymentStep.COMPLETED.value if success else DeploymentStep.FAILED.value
    )
    instance.failure_reason = None if success else failure_reason
    instance.completed_at = at
    if status == Status.TERMINATED.value:
        instance.terminated_at = at
    if success and operation is Operation.MOVE_TIER and target_tier is not None:
        instance.tier = target_tier
    if instance.subdomain and operation in (Operation.DEPLOY, Operation.TERMINATE):
        if not success:
            instance.dns_status = "failed"
        else:
            instance.dns_status = "deleted" if operation is Operation.TERMINATE else "active"
    return status


_SNAPSHOT_FIELDS = (
    "status",
    "configuration",
    "previous_configuration",
    "tier",
    "rollback_count",
    "current_step",
    "failure_reason",
    "completed_at",
    "terminated_at",
    "dns_status",
    "triggered_by",
    "dispatch_reference",
)


def snapshot(instance) -> dict:
    """The instance fields an operation may change, JSON-serialisable."""

    values = {}
    for field in _SNAPSHOT_FIELDS:
        value = getattr(instance, field)
        values[field] = value.isoformat() if isinstance(value, datetime) else deepcopy(value)
    return values


def restore(instance, state: dict) -> None:
    """Undo ``begin``: put back the fields ``snapshot`` recorded."""

    for field in _SNAPSHOT_FIELDS:
        value = state.get(field)
        if field in ("completed_at", "terminated_at") and isinstance(value, str):
            value = datetime.fromisoformat(value)
        setattr(instance, field, value)


def operation_of(instance) -> Operation:
    """The operation an instance's latest dispatch performs."""

    declared = (instance.dispatch_reference or {}).get("operation")
    try:
        return Operation(declared)
    except ValueError:
        pass
    if instance.status == Status.TERMINATING.value:
        return Operation.TERMINATE
    if instance.status == Status.UPDATING.value:
        return Operation.UPGRADE
    return Operation.DEPLOY
