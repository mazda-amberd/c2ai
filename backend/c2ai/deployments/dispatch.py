"""Dispatch a deployment pipeline with the credentials its application needs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.constants.registered_application import ApplicationType
from c2ai.crud import (
    github_connection as crud_github_connection,
)
from c2ai.deployments.pipelines import (
    dispatch_registered_application_deployment,
)
from c2ai.models.registered_application import RegisteredApplicationVersion
from c2ai.registration import credentials


async def dispatch_deployment(
    db: AsyncSession,
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    tier: int,
    configuration: dict,
    triggered_by: str,
) -> dict:
    """Dispatch a deploy pipeline with the credentials its application type needs.

    Used for both first deployments and rollbacks, so a redeploy always sends
    the same registry, LLM, and GitHub credentials the original did.
    """

    options: dict = {}
    if version.application.application_type == ApplicationType.GITHUB_WORKFLOW.value:
        github = version.github_configuration
        if github is not None:
            runtime = await crud_github_connection.resolve_github_connection(
                db, github.github_connection_id
            )
            if runtime is not None:
                options["github_token"] = runtime.token
                options["github_api_base_url"] = runtime.api_base_url
    else:
        template = version.container_configuration
        if template is not None and template.registry_password_encrypted is not None:
            registry = await credentials.resolve_container_registry_credentials(
                db, version.id
            )
            if registry is not None:
                options["registry_username"] = registry.username
                options["registry_token"] = registry.password
        options["llm_api_token"] = await credentials.resolve_llm_api_token(
            db, version.id
        )
    return await dispatch_registered_application_deployment(
        version,
        deployment_id=deployment_id,
        instance_name=instance_name,
        tier=tier,
        configuration=configuration,
        triggered_by=triggered_by,
        **options,
    )
