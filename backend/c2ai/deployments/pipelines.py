"""Pipeline dispatch for deployments, upgrades, tier moves, and terminations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.config import get_settings
from c2ai.constants.registered_application import ApplicationType
from c2ai.core.exceptions import UnprocessableEntityError
from c2ai.deployments.configuration import (
    SLACK_USER_PARAMETER,
    resolve_github_workflow_subdomain,
    stringify_inputs,
    workflow_accepts_default_slack_user,
)
from c2ai.models.registered_application import RegisteredApplicationVersion

# Labels the container pipeline records as the registry provider; the Helm chart
# derives the actual registry server from the image repository.
_CONTAINER_REGISTRY_LABELS = {
    "docker.io": "Docker Hub",
    "ghcr.io": "GitHub Container Registry",
    "ecr": "Amazon ECR",
    "private": "Private Registry",
}


# Amberd's predefined in-place workflows for GitHub Workflow applications.
GITHUB_WORKFLOW_UPDATE = "ada-update.yaml"
GITHUB_WORKFLOW_MOVE_TIER = "ada-move-to-tier.yaml"
GITHUB_WORKFLOW_TERMINATE = "ada-terminate.yaml"


def _callback_base_url() -> str:
    """Athena's public base URL, which the pipeline posts its status back to."""

    return get_settings().callback_base_url.rstrip("/")


def _workflow_callback_token(callback_token: str | None) -> dict[str, str]:
    """callback_token as a workflow_dispatch input, once the workflows declare it."""

    if callback_token and get_settings().container_workflows_accept_callback_token:
        return {"callback_token": callback_token}
    return {}


def _pipeline_client(owner_repository: str) -> GitHubActionsClient:
    """Client for one of Amberd's pipeline repositories (``owner/name``)."""

    owner, repository = owner_repository.split("/", 1)
    return GitHubActionsClient(repo_owner=owner, repo_name=repository)


async def dispatch_devops_workflow(workflow: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """``workflow_dispatch`` one of the ada-* workflows in the devops repository."""

    settings = get_settings()
    client = GitHubActionsClient(
        repo_owner=settings.github_repo_owner, repo_name=settings.github_repo_name
    )
    return await client.trigger_workflow(
        workflow, settings.devops_branch, stringify_inputs(inputs)
    )


def _json_number(value: Any, *, default: int) -> str:
    """Render a value the pipeline feeds to ``jq --argjson`` as valid JSON."""

    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return str(default)


def build_container_pipeline_payload(
    configuration: dict[str, Any],
    *,
    deployment_id: UUID,
    instance_name: str,
    tier: int,
    triggered_by: str,
    registry_username: str | None,
    registry_token: str | None,
    llm_api_token: str | None,
    callback_token: str | None = None,
) -> dict[str, Any]:
    """Flatten one stored configuration into the container pipeline's contract."""

    container = configuration["container"]
    llm = configuration.get("llm") or {}
    registry = str(container.get("registry") or "")
    llm_endpoint = str(llm.get("endpoint") or "")
    llm_model_name = str(llm.get("model_name") or "")
    env_vars = dict(container.get("environment_variables") or {})
    if llm:
        # The container reads its LLM settings from the environment as well,
        # whether or not any parameters were registered. The token joins only
        # here, at dispatch, so the stored configuration snapshot (returned by
        # the API) never holds it. Snapshots stored before the keys were
        # renamed carry uppercase copies, which a rollback would resend.
        for legacy_key in ("LLM_ENDPOINT", "LLM_MODEL_NAME"):
            env_vars.pop(legacy_key, None)
        env_vars.update(
            {
                "llm_endpoint": llm_endpoint,
                "llm_api_token": llm_api_token or "",
                "llm_model_name": llm_model_name,
            }
        )
    return {
        "triggered_by": triggered_by,
        "deployment_id": str(deployment_id),
        # The pipeline builds both the Helm release and the ingress host from
        # app_name, so it carries the instance name Athena generated the
        # hostname from.
        "app_name": instance_name,
        "callback_base_url": _callback_base_url(),
        **({"callback_token": callback_token} if callback_token else {}),
        "tier": f"tier{tier}",
        "container_registry": _CONTAINER_REGISTRY_LABELS.get(
            registry.strip().lower(),
            registry,
        ),
        "image_registry": str(container.get("image_repository") or ""),
        "registry_username": registry_username or "",
        "registry_token": registry_token or "",
        "default_image_tag": str(container.get("image_tag") or ""),
        "container_port": _json_number(container.get("container_port"), default=8080),
        "image_pull_policy": str(container.get("image_pull_policy") or "IfNotPresent"),
        "expose_public_service": container.get("service_type") == "Ingress",
        "gpu_request": _json_number(container.get("gpu_request"), default=0),
        "cpu_request": str(container.get("cpu_request") or ""),
        "memory_request": str(container.get("memory_request") or ""),
        "replica_count": _json_number(container.get("replica_count"), default=1),
        # The chart mounts an existing claim, so storage carries a PVC name.
        "persistent_volume": str(container.get("storage") or ""),
        # The pipeline creates a namespace per instance, so it carries the same
        # value as app_name rather than the Tier the instance runs in.
        "namespace": instance_name,
        "env_vars": env_vars,
        # Provider references only; the pipeline materialises Kubernetes
        # Secrets from them and injects each as its environment variable.
        "managed_secrets": [
            {
                "name": secret["name"],
                "environment_variable": secret["environment_variable"],
                "reference": secret["reference"],
            }
            for secret in configuration.get("managed_secrets") or []
        ],
        "llm_endpoint": llm_endpoint,
        "llm_api_token": llm_api_token or "",
        "llm_model_name": llm_model_name,
    }


async def dispatch_registered_application_deployment(
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    tier: int,
    configuration: dict[str, Any],
    triggered_by: str,
    github_token: str | None = None,
    github_api_base_url: str | None = None,
    registry_username: str | None = None,
    registry_token: str | None = None,
    llm_api_token: str | None = None,
    callback_token: str | None = None,
) -> dict[str, Any]:
    """Dispatch a registered GitHub workflow or the configured container pipeline."""

    system_values = {
        "deployment_id": str(deployment_id),
        "instance_name": instance_name,
        "tier": tier,
        "triggered_by": triggered_by,
    }
    application_type = version.application.application_type
    if application_type == ApplicationType.GITHUB_WORKFLOW.value:
        github = version.github_configuration
        if github is None:
            raise RuntimeError("GitHub registered application has no workflow configuration")
        owner, repository = github.repository.split("/", 1)
        client_options: dict[str, Any] = {
            "repo_owner": owner,
            "repo_name": repository,
        }
        if github_token:
            client_options["github_token"] = github_token
        if github_api_base_url:
            client_options["api_base_url"] = github_api_base_url
        client = GitHubActionsClient(**client_options)
        # Temporarily disabled until DevOps adds these inputs to the workflows.
        # Restore this block and both **llm_inputs lines below when ready.
        # llm = configuration.get("llm")
        # llm_inputs = (
        #     {
        #         "llm_endpoint": llm["endpoint"],
        #         "llm_model_name": llm["model_name"],
        #     }
        #     if llm is not None
        #     else {}
        # )
        # Only declared parameters are workflow inputs; snapshots may carry
        # Athena-side keys (e.g. the adopted instance's subdomain).
        declared = {definition.key for definition in version.parameters}
        parameters = {
            key: value
            for key, value in configuration["parameters"].items()
            if key in declared
        }
        default_inputs: dict[str, Any] = {}
        if (
            workflow_accepts_default_slack_user(github.repository)
            and SLACK_USER_PARAMETER not in parameters
        ):
            # These workflows always declare slack_user; sending the triggering
            # user spares every registration from restating it.
            default_inputs[SLACK_USER_PARAMETER] = triggered_by
        repository_dispatch_payload = {
            **system_values,
            **default_inputs,
            **parameters,
            "provider": f"tier{tier}",
            # **llm_inputs,
        }
        if github.trigger_method == "repository_dispatch":
            reference = await client.trigger_repository_dispatch(
                "athena-deploy",
                {
                    **repository_dispatch_payload,
                    "ref": github.ref,
                    "workflow_file_path": github.workflow_file_path,
                },
            )
        else:
            # workflow_dispatch rejects any input not declared by the workflow.
            # Send the registered parameter values and Athena's generated
            # deployment ID and Tier-derived provider, but keep internal metadata out of the
            # workflow input object. repository_dispatch remains free-form.
            workflow_inputs = {
                **default_inputs,
                **parameters,
                "provider": f"tier{tier}",
                # **llm_inputs,
                "deployment_id": str(deployment_id),
            }
            reference = await client.trigger_workflow(
                github.workflow_file_path,
                github.ref,
                stringify_inputs(workflow_inputs),
            )
        return {
            **reference,
            "connection": github.github_connection_id,
            "operation": "deploy",
            "workflow_id": github.workflow_file_path,
            "ref": github.ref,
        }

    settings = get_settings()
    workflow = settings.container_deployment_workflow
    ref = settings.devops_branch
    event_type = settings.container_deployment_event_type
    client = _pipeline_client(settings.container_deployment_repository)
    # The pipeline accepts both triggers, but workflow_dispatch rejects an empty
    # value for any input it declares as required — an application with no
    # persistent volume has one. repository_dispatch carries the same fields
    # without that validation.
    reference = await client.trigger_repository_dispatch(
        event_type,
        {
            "deployment": build_container_pipeline_payload(
                configuration,
                deployment_id=deployment_id,
                instance_name=instance_name,
                tier=tier,
                triggered_by=triggered_by,
                registry_username=registry_username,
                registry_token=registry_token,
                llm_api_token=llm_api_token,
                callback_token=callback_token,
            )
        },
    )
    return {
        **reference,
        # repository_dispatch returns no run, so progress correlation needs the
        # workflow the event starts.
        "workflow_id": workflow,
        "ref": ref,
        "pipeline": "container",
        "operation": "deploy",
    }


async def dispatch_registered_application_upgrade(
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    tier: int,
    target_version: str,
    configuration: dict[str, Any],
    triggered_by: str,
    rollback: bool = False,
    callback_token: str | None = None,
) -> dict[str, Any]:
    """Dispatch the type-specific predefined in-place update workflow."""

    reference = await _dispatch_upgrade(
        version,
        deployment_id=deployment_id,
        instance_name=instance_name,
        target_version=target_version,
        configuration=configuration,
        triggered_by=triggered_by,
        callback_token=callback_token,
    )
    return {**reference, "rollback": True} if rollback else reference


async def _dispatch_upgrade(
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    target_version: str,
    configuration: dict[str, Any],
    triggered_by: str,
    callback_token: str | None = None,
) -> dict[str, Any]:

    application_type = version.application.application_type
    if application_type == ApplicationType.GITHUB_WORKFLOW.value:
        subdomain = resolve_github_workflow_subdomain(
            configuration,
            instance_name=instance_name,
        )
        reference = await dispatch_devops_workflow(
            GITHUB_WORKFLOW_UPDATE,
            {
                "slack_user": triggered_by,
                "subdomain": subdomain,
                "branch": target_version,
                "deployment_id": str(deployment_id),
            },
        )
        return {
            **reference,
            "subdomain": subdomain,
            "version": target_version,
            "pipeline": "github-upgrade",
            "operation": "upgrade",
        }

    if application_type != ApplicationType.CONTAINERIZED.value:
        raise UnprocessableEntityError(
            "Upgrade dispatch does not support this application type."
        )

    settings = get_settings()
    client = _pipeline_client(settings.container_upgrade_repository)
    reference = await client.trigger_workflow(
        settings.container_upgrade_workflow,
        settings.devops_branch,
        stringify_inputs(
            {
                "triggered_by": triggered_by,
                "deployment_id": str(deployment_id),
                "app_name": instance_name,
                "callback_base_url": _callback_base_url(),
                "default_image_tag": target_version,
                **_workflow_callback_token(callback_token),
            }
        ),
    )
    return {
        **reference,
        "pipeline": "container-upgrade",
        "operation": "upgrade",
    }


async def dispatch_registered_application_move_tier(
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    target_tier: int,
    configuration: dict[str, Any],
    triggered_by: str,
) -> dict[str, Any]:
    """Dispatch Amberd's ada-move-to-tier workflow for a GitHub Workflow instance."""

    if version.application.application_type != ApplicationType.GITHUB_WORKFLOW.value:
        raise UnprocessableEntityError("Only GitHub Workflow deployments can move tiers.")
    subdomain = resolve_github_workflow_subdomain(configuration, instance_name=instance_name)
    reference = await dispatch_devops_workflow(
        GITHUB_WORKFLOW_MOVE_TIER,
        {
            "slack_user": triggered_by,
            "subdomain": subdomain,
            "tier": f"tier{target_tier}",
            "deployment_id": str(deployment_id),
        },
    )
    return {
        **reference,
        "subdomain": subdomain,
        "target_tier": target_tier,
        "pipeline": "github-move-tier",
        "operation": "move_tier",
    }


async def dispatch_registered_application_termination(
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    tier: int,
    configuration: dict[str, Any],
    triggered_by: str,
    callback_token: str | None = None,
) -> dict[str, Any]:
    """Dispatch the type-specific predefined termination workflow."""

    application_type = version.application.application_type
    if application_type == ApplicationType.GITHUB_WORKFLOW.value:
        subdomain = resolve_github_workflow_subdomain(
            configuration,
            instance_name=instance_name,
        )
        reference = await dispatch_devops_workflow(
            GITHUB_WORKFLOW_TERMINATE,
            {
                "slack_user": triggered_by,
                "subdomain": subdomain,
                "deployment_id": str(deployment_id),
            },
        )
        return {
            **reference,
            "subdomain": subdomain,
            "pipeline": "github-termination",
            "operation": "terminate",
        }

    if application_type != ApplicationType.CONTAINERIZED.value:
        raise UnprocessableEntityError(
            "Termination dispatch does not support this application type."
        )

    settings = get_settings()
    client = _pipeline_client(settings.container_termination_repository)
    reference = await client.trigger_workflow(
        settings.container_termination_workflow,
        settings.devops_branch,
        stringify_inputs(
            {
                "triggered_by": triggered_by,
                "deployment_id": str(deployment_id),
                # The pipeline resolves the Helm release and the namespace it
                # deletes from app_name, so it carries the deployed instance
                # name. workflow_dispatch rejects any other input.
                "app_name": instance_name,
                **_workflow_callback_token(callback_token),
                "callback_base_url": _callback_base_url(),
            }
        ),
    )
    return {
        **reference,
        "pipeline": "container-termination",
        "operation": "terminate",
    }
