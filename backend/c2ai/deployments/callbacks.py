"""Pipeline progress callbacks are authenticated per operation.

Each dispatch carries ``callback_token = HMAC-SHA256(DEPLOYMENT_CALLBACK_TOKEN,
"<instance id>:<operation id>")``. A pipeline can therefore report progress
only for the operation it was started for, and only while that operation is
the instance's open one; a leaked token is useless for any other deployment
or any later operation.

``C2AI_CALLBACK_ACCEPT_SHARED_TOKEN`` (default true) still accepts the shared
``DEPLOYMENT_CALLBACK_TOKEN`` itself, for pipelines that have not yet been
updated to send the per-operation token; turn it off once they have.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.config import get_settings
from c2ai.core.exceptions import ServiceUnavailableError, UnauthorizedError
from c2ai.deployments import operations

logger = logging.getLogger(__name__)


def _secret() -> str:
    secret = get_settings().deployment_callback_token
    if not secret:
        raise ServiceUnavailableError("Deployment progress callbacks are not configured.")
    return secret


def callback_token(instance_id: UUID | str, operation_id: str) -> str:
    message = f"{instance_id}:{operation_id}".encode()
    return hmac.new(_secret().encode(), message, hashlib.sha256).hexdigest()


def _invalid() -> UnauthorizedError:
    return UnauthorizedError(
        "Invalid deployment callback token.", code="InvalidDeploymentCallbackToken"
    )


async def verify_callback(db: AsyncSession, instance_id: UUID, presented: str | None) -> None:
    secret = _secret()
    if not presented:
        raise _invalid()
    if hmac.compare_digest(presented, secret):
        if not get_settings().callback_accept_shared_token:
            raise _invalid()
        logger.warning(
            "Deployment %s reported progress with the shared callback token; "
            "update the pipeline to send its per-operation callback_token.",
            instance_id,
        )
        return
    run = await operations.active_operation(db, instance_id)
    if run is None or not hmac.compare_digest(presented, callback_token(instance_id, run.id)):
        raise _invalid()
