"""Validation and pipeline dispatch for registered application deployments."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from c2ai.models.registered_application import RegisteredApplicationVersion
from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.clients.github import (
    GITHUB_REPO_NAME,
    GITHUB_REPO_OWNER,
    GITHUB_WORKFLOW_TERMINATE,
    GITHUB_WORKFLOW_UPDATE,
    dispatch_github_terminate_workflow,
    dispatch_github_update_workflow,
    get_devops_branch,
)
from c2ai.constants.registered_application import ApplicationType
from c2ai.core.exceptions import UnprocessableEntityError
from c2ai.schemas.registered_application import (
    ContainerRegisteredApplicationDeploymentCreate,
    RegisteredApplicationDeploymentCreate,
)
from c2ai.utils.host_labels import workflow_prepare_subdomain

_AUTOMATIC_PARAMETER_VALUES = {
    "tier": lambda tier, _name, definition: (
        f"Tier {tier}"
        if definition.parameter_type == "select"
        and f"Tier {tier}" in (definition.options or [])
        else tier
    ),
    "target_tier": lambda tier, _name, definition: (
        f"Tier {tier}"
        if definition.parameter_type == "select"
        and f"Tier {tier}" in (definition.options or [])
        else tier
    ),
    "namespace": lambda tier, _name, _definition: f"tier{tier}",
    "instance_name": lambda _tier, name, _definition: name,
}
# The container pipeline creates one namespace per deployed instance, so its
# namespace carries the instance name the pipeline also uses as app_name
# instead of the Tier the instance runs in.
_CONTAINER_DERIVED_PARAMETER_VALUES = {
    **_AUTOMATIC_PARAMETER_VALUES,
    "namespace": lambda _tier, name, _definition: name,
}
_DEPLOYMENT_DOMAIN = "amberd.ai"
# Labels the container pipeline records as the registry provider; the Helm chart
# derives the actual registry server from the image repository.
_CONTAINER_REGISTRY_LABELS = {
    "docker.io": "Docker Hub",
    "ghcr.io": "GitHub Container Registry",
    "ecr": "Amazon ECR",
    "private": "Private Registry",
}
# Workflow input every amberd-ai pipeline declares to attribute the run to a
# person. Athena fills it from the triggering user so neither registration nor
# deployment has to ask for it.
SLACK_USER_PARAMETER = "slack_user"
_DEFAULT_SLACK_USER_REPO_OWNERS = "amberd-ai"
_INSTANCE_NAME_SLUG_RE = re.compile(r"[^a-z0-9]+")


def resolve_tier_llm_endpoint(endpoint: str, tier: int) -> str:
    """
    Point a registered in-cluster LLM endpoint at the gateway of one Tier.

    ``https://amberd-llm-gateway:8010`` deployed into Tier 1 becomes
    ``https://amberd-llm-gateway.tier1.svc:8010``: the service keeps its name,
    scheme, port, and path while its namespace follows the Tier. An external
    host such as ``https://api.openai.com/v1`` has no Tier namespace, so it is
    sent exactly as registered.
    """

    raw = endpoint.strip()
    has_scheme = "://" in raw
    try:
        parts = urlsplit(raw if has_scheme else f"//{raw}")
        port = parts.port
    except ValueError:
        return raw
    host = parts.hostname
    if not host:
        return raw
    labels = host.split(".")
    # A bare service name, or <service>.<namespace>.svc[.cluster.local].
    in_cluster = len(labels) == 1 or (len(labels) >= 3 and labels[2] == "svc")
    if not in_cluster:
        return raw

    tier_host = ".".join([labels[0], f"tier{tier}", "svc", *labels[3:]])
    netloc = f"{tier_host}:{port}" if port is not None else tier_host
    rebuilt = urlunsplit(
        (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
    )
    return rebuilt if has_scheme else rebuilt.removeprefix("//")


def _runtime_llm_configuration(
    version: RegisteredApplicationVersion,
    tier: int,
) -> dict[str, str] | None:
    """Return the registered LLM values with the endpoint routed to this Tier."""

    llm = version.llm_configuration
    if llm is None:
        return None
    return {
        "endpoint": resolve_tier_llm_endpoint(llm.endpoint, tier),
        "model_name": llm.model_name,
    }


def _stored_replica_count(scaling: str | None) -> int:
    """Use a simple numeric scaling value as replicas, otherwise keep one replica."""

    if scaling is None:
        return 1
    try:
        replica_count = int(scaling)
    except ValueError:
        return 1
    return replica_count if 1 <= replica_count <= 1000 else 1


def build_container_deployment_configuration(
    version: RegisteredApplicationVersion,
    payload: ContainerRegisteredApplicationDeploymentCreate,
    *,
    tier: int,
) -> dict[str, Any]:
    """Build a safe container deployment snapshot from its registered template."""

    if version.application.application_type != ApplicationType.CONTAINERIZED.value:
        raise UnprocessableEntityError(
            "Container deployment is only supported for containerized applications."
        )

    template = version.container_configuration
    if template is None:
        raise RuntimeError("Container registered application has no image configuration")
    if template.container_port is None:
        raise UnprocessableEntityError(
            "The registered container template has no container port."
        )

    environment_variables: dict[str, str] = {}
    for variable in template.environment_variables or []:
        if not isinstance(variable, dict):
            raise RuntimeError(
                "Container template has an invalid environment variable configuration"
            )
        key = variable.get("key")
        value = variable.get("value")
        if not isinstance(key, str) or not isinstance(value, str):
            raise RuntimeError(
                "Container template has an invalid environment variable configuration"
            )
        environment_variables[key] = value

    # Container parameter values are fixed at registration; the deployment
    # request carries none.
    resolved_parameters = _resolve_deployment_parameters(
        version,
        {},
        tier=tier,
        instance_name=payload.instance_name,
        derived_values=_CONTAINER_DERIVED_PARAMETER_VALUES,
        # A namespace fixed at registration would collide across instances of
        # the same application, so the instance name always wins over it.
        always_derived_keys=frozenset({"namespace"}),
    )
    # Athena-derived values are pipeline inputs of their own; only the
    # parameters defined at registration reach the container as env vars.
    environment_variables.update(
        _stringify_inputs(
            {
                key: value
                for key, value in resolved_parameters.items()
                if key not in _CONTAINER_DERIVED_PARAMETER_VALUES
            }
        )
    )
    llm = _runtime_llm_configuration(version, tier)
    if llm is not None:
        environment_variables.update(
            {
                "llm_endpoint": llm["endpoint"],
                "llm_model_name": llm["model_name"],
            }
        )

    hostname = f"{payload.instance_name}.{_DEPLOYMENT_DOMAIN}"
    service_type = "Ingress" if template.expose_public_service else "ClusterIP"
    return {
        "parameters": resolved_parameters,
        **({"llm": llm} if llm is not None else {}),
        "container": {
            "registry": template.registry,
            "image_repository": template.image_repository,
            "image_tag": payload.version,
            "image_pull_policy": template.image_pull_policy,
            "container_port": template.container_port,
            "gpu_request": template.gpu_request,
            "cpu_request": template.cpu_request,
            "memory_request": template.memory_request,
            "scaling": template.scaling,
            "replica_count": _stored_replica_count(template.scaling),
            "storage": template.storage,
            "persistent_volume_size": template.storage,
            "environment_variables": environment_variables,
            "service_type": service_type,
            "host": hostname if service_type == "Ingress" else None,
            "image_pull_secret": template.registry_credential_id,
            "registry_credentials_configured": bool(
                template.registry_password_encrypted
                or template.registry_credential_id
            ),
        },
        "dns": {
            "subdomain": payload.instance_name,
            "hostname": hostname,
            "managed_by": "athena",
        },
    }


def _is_valid_parameter_value(parameter_type: str, value: Any) -> bool:
    if parameter_type == "text":
        return isinstance(value, str)
    if parameter_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if parameter_type == "boolean":
        return isinstance(value, bool)
    if parameter_type == "select":
        return isinstance(value, str)
    if parameter_type == "key_value":
        return isinstance(value, dict) and all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        )
    return False


def _resolve_deployment_parameters(
    version: RegisteredApplicationVersion,
    supplied_parameters: dict[str, Any],
    *,
    tier: int,
    instance_name: str,
    triggered_by: str | None = None,
    derived_values: dict[str, Any] | None = None,
    always_derived_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Validate values against the typed definitions stored at registration."""

    automatic_values = dict(
        _AUTOMATIC_PARAMETER_VALUES if derived_values is None else derived_values
    )
    if triggered_by:
        automatic_values[SLACK_USER_PARAMETER] = (
            lambda _tier, _name, _definition: triggered_by
        )
    definitions = {definition.key: definition for definition in version.parameters}
    automatic_keys = {
        key
        for key, definition in definitions.items()
        if key in automatic_values
        and (definition.default_value is None or key in always_derived_keys)
    }
    supplied_keys = set(supplied_parameters)
    unknown_keys = supplied_keys - set(definitions)
    forbidden_automatic_keys = supplied_keys.intersection(automatic_keys)
    if unknown_keys:
        raise UnprocessableEntityError(
            f"Unknown deployment parameters: {', '.join(sorted(unknown_keys))}."
        )
    if forbidden_automatic_keys:
        raise UnprocessableEntityError(
            "Athena-derived deployment parameters cannot be supplied manually: "
            f"{', '.join(sorted(forbidden_automatic_keys))}."
        )

    resolved_parameters: dict[str, Any] = {}
    for key, definition in definitions.items():
        if key in automatic_keys:
            resolved_parameters[key] = automatic_values[key](
                tier,
                instance_name,
                definition,
            )
            continue

        if key in supplied_parameters:
            value = supplied_parameters[key]
        elif definition.default_value is not None:
            value = definition.default_value
        elif definition.required:
            raise UnprocessableEntityError(
                f"Required deployment parameter '{key}' is missing."
            )
        else:
            continue

        if not _is_valid_parameter_value(definition.parameter_type, value):
            raise UnprocessableEntityError(
                f"Deployment parameter '{key}' must be {definition.parameter_type}."
            )
        if (
            definition.parameter_type == "select"
            and value not in (definition.options or [])
        ):
            raise UnprocessableEntityError(
                f"Deployment parameter '{key}' must match a configured option."
            )
        resolved_parameters[key] = value
    return resolved_parameters


def build_deployment_configuration(
    version: RegisteredApplicationVersion,
    payload: RegisteredApplicationDeploymentCreate,
    *,
    instance_name: str | None = None,
    triggered_by: str | None = None,
) -> dict[str, Any]:
    """Validate GitHub workflow inputs and build a safe deployment snapshot."""

    if version.application.application_type != ApplicationType.GITHUB_WORKFLOW.value:
        raise UnprocessableEntityError(
            "Use the Tier-scoped deployment contract for containerized applications."
        )

    resolved_instance_name = instance_name or payload.instance_name or ""
    supplied_parameters = dict(payload.parameters)
    if payload.version is not None and any(p.key == "branch" for p in version.parameters):
        supplied_parameters["branch"] = payload.version
    resolved_parameters = _resolve_deployment_parameters(
        version,
        supplied_parameters,
        tier=payload.tier,
        instance_name=resolved_instance_name,
        triggered_by=triggered_by,
    )
    if payload.version is not None:
        resolved_parameters["branch"] = payload.version

    github = version.github_configuration
    if github is None:
        raise RuntimeError("GitHub registered application has no workflow configuration")
    llm = _runtime_llm_configuration(version, payload.tier)
    return {
        "parameters": resolved_parameters,
        **({"llm": llm} if llm is not None else {}),
        "github": {
            "connection": github.github_connection_id,
            "code_repository": github.code_repository,
            "trigger_method": github.trigger_method,
            "repository": github.repository,
            "workflow_file_path": github.workflow_file_path,
            "ref": github.ref,
        },
    }


def default_github_instance_name(application_name: str, tier: int) -> str:
    """Fallback record name when the parameters do not derive a host label."""

    suffix = f"-tier-{tier}"
    base = _INSTANCE_NAME_SLUG_RE.sub("-", application_name.strip().lower()).strip("-")
    return f"{(base or 'application')[: 63 - len(suffix)]}{suffix}"


def resolve_github_deployment_instance_name(
    configuration: dict[str, Any],
    *,
    application_name: str,
    tier: int,
    supplied_instance_name: str | None = None,
) -> str:
    """
    Name a GitHub deployment after the instance its workflow creates.

    A GitHub workflow derives its own release name from the deployment
    parameters, so an administrator never names the instance. Recording that
    same name keeps Athena's history addressable by what actually runs.
    """

    fallback = supplied_instance_name or default_github_instance_name(
        application_name,
        tier,
    )
    return resolve_github_workflow_subdomain(configuration, instance_name=fallback)


def _slack_user_repo_owners() -> set[str]:
    raw = os.getenv("SLACK_USER_DEFAULT_REPO_OWNERS", _DEFAULT_SLACK_USER_REPO_OWNERS)
    return {owner.strip().lower() for owner in raw.split(",") if owner.strip()}


def workflow_accepts_default_slack_user(repository: str) -> bool:
    """Whether this workflow repository's owner declares the slack_user input."""

    owner = repository.split("/", 1)[0].strip().lower()
    return bool(owner) and owner in _slack_user_repo_owners()


def resolve_github_workflow_subdomain(
    configuration: dict[str, Any],
    *,
    instance_name: str,
) -> str:
    """
    Return the host label the deploy workflow created for this instance.

    The ada workflows address a deployment by the subdomain their ``prepare``
    step derives from the deployment parameters (``amberd-{customer}-{env}``),
    which is unrelated to the Athena instance name an administrator typed.
    Update and terminate must reuse that label or Helm cannot find the release.
    """

    parameters = configuration.get("parameters")
    if isinstance(parameters, dict):
        subdomain = parameters.get("subdomain")
        if isinstance(subdomain, str) and subdomain.strip():
            return subdomain.strip()
        customer_name = parameters.get("customer_name")
        env_instance = parameters.get("env_instance")
        if (
            isinstance(customer_name, str)
            and customer_name.strip()
            and isinstance(env_instance, str)
            and env_instance.strip()
        ):
            return workflow_prepare_subdomain(customer_name, env_instance)
    return instance_name


def _stringify_inputs(values: dict[str, Any]) -> dict[str, str]:
    return {
        key: value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        for key, value in values.items()
    }


def _callback_base_url() -> str:
    """Athena's public base URL, which the pipeline posts its status back to."""

    return os.getenv("ATHENA_CALLBACK_BASE_URL", "https://athena.amberd.ai").rstrip("/")


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
        default_inputs: dict[str, Any] = {}
        if (
            workflow_accepts_default_slack_user(github.repository)
            and SLACK_USER_PARAMETER not in configuration["parameters"]
        ):
            # These workflows always declare slack_user; sending the triggering
            # user spares every registration from restating it.
            default_inputs[SLACK_USER_PARAMETER] = triggered_by
        repository_dispatch_payload = {
            **system_values,
            **default_inputs,
            **configuration["parameters"],
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
                **configuration["parameters"],
                "provider": f"tier{tier}",
                # **llm_inputs,
                "deployment_id": str(deployment_id),
            }
            reference = await client.trigger_workflow(
                github.workflow_file_path,
                github.ref,
                _stringify_inputs(workflow_inputs),
            )
        return {
            **reference,
            "connection": github.github_connection_id,
            "operation": "deploy",
            "workflow_id": github.workflow_file_path,
            "ref": github.ref,
        }

    owner_repository = os.getenv(
        "CONTAINER_DEPLOYMENT_REPOSITORY",
        "amberd-ai/devops",
    )
    owner, repository = owner_repository.split("/", 1)
    workflow = os.getenv(
        "CONTAINER_DEPLOYMENT_WORKFLOW",
        "containerized-app-deploy.yaml",
    )
    ref = os.getenv("DEVOPS_BRANCH", "main")
    event_type = os.getenv(
        "CONTAINER_DEPLOYMENT_EVENT_TYPE",
        "containerized-deploy",
    )
    client = GitHubActionsClient(repo_owner=owner, repo_name=repository)
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
) -> dict[str, Any]:
    """Dispatch the type-specific predefined in-place update workflow."""

    application_type = version.application.application_type
    if application_type == ApplicationType.GITHUB_WORKFLOW.value:
        dispatched_at = datetime.now(timezone.utc).isoformat()
        subdomain = resolve_github_workflow_subdomain(
            configuration,
            instance_name=instance_name,
        )
        await dispatch_github_update_workflow(
            correlation_id=str(deployment_id),
            branch=target_version,
            subdomain=subdomain,
            triggered_by=triggered_by,
        )
        return {
            "trigger_method": "workflow_dispatch",
            "subdomain": subdomain,
            "workflow_id": GITHUB_WORKFLOW_UPDATE,
            "repo_owner": GITHUB_REPO_OWNER,
            "repo_name": GITHUB_REPO_NAME,
            "ref": get_devops_branch(),
            "api_base_url": "https://api.github.com",
            "dispatched_at": dispatched_at,
            "version": target_version,
            "pipeline": "github-upgrade",
            "operation": "upgrade",
        }

    if application_type != ApplicationType.CONTAINERIZED.value:
        raise UnprocessableEntityError(
            "Upgrade dispatch does not support this application type."
        )

    owner_repository = os.getenv(
        "CONTAINER_UPGRADE_REPOSITORY",
        "amberd-ai/devops",
    )
    owner, repository = owner_repository.split("/", 1)
    workflow = os.getenv(
        "CONTAINER_UPGRADE_WORKFLOW",
        "containerized-app-update.yaml",
    )
    ref = os.getenv("DEVOPS_BRANCH", "main")
    client = GitHubActionsClient(repo_owner=owner, repo_name=repository)
    reference = await client.trigger_workflow(
        workflow,
        ref,
        _stringify_inputs(
            {
                "triggered_by": triggered_by,
                "deployment_id": str(deployment_id),
                "app_name": instance_name,
                "callback_base_url": _callback_base_url(),
                "default_image_tag": target_version,
            }
        ),
    )
    return {
        **reference,
        "pipeline": "container-upgrade",
        "operation": "upgrade",
    }


async def dispatch_registered_application_termination(
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    tier: int,
    configuration: dict[str, Any],
    triggered_by: str,
) -> dict[str, Any]:
    """Dispatch the type-specific predefined termination workflow."""

    application_type = version.application.application_type
    if application_type == ApplicationType.GITHUB_WORKFLOW.value:
        dispatched_at = datetime.now(timezone.utc).isoformat()
        subdomain = resolve_github_workflow_subdomain(
            configuration,
            instance_name=instance_name,
        )
        await dispatch_github_terminate_workflow(
            subdomain,
            correlation_id=str(deployment_id),
            triggered_by=triggered_by,
        )
        return {
            "trigger_method": "workflow_dispatch",
            "subdomain": subdomain,
            "workflow_id": GITHUB_WORKFLOW_TERMINATE,
            "repo_owner": GITHUB_REPO_OWNER,
            "repo_name": GITHUB_REPO_NAME,
            "ref": get_devops_branch(),
            "api_base_url": "https://api.github.com",
            "dispatched_at": dispatched_at,
            "pipeline": "github-termination",
            "operation": "terminate",
        }

    if application_type != ApplicationType.CONTAINERIZED.value:
        raise UnprocessableEntityError(
            "Termination dispatch does not support this application type."
        )

    owner_repository = os.getenv(
        "CONTAINER_TERMINATION_REPOSITORY",
        "amberd-ai/devops",
    )
    owner, repository = owner_repository.split("/", 1)
    workflow = os.getenv(
        "CONTAINER_TERMINATION_WORKFLOW",
        "containerized-app-terminate.yaml",
    )
    ref = os.getenv("DEVOPS_BRANCH", "main")
    client = GitHubActionsClient(repo_owner=owner, repo_name=repository)
    reference = await client.trigger_workflow(
        workflow,
        ref,
        _stringify_inputs(
            {
                "triggered_by": triggered_by,
                "deployment_id": str(deployment_id),
                # The pipeline resolves the Helm release and the namespace it
                # deletes from app_name, so it carries the deployed instance
                # name. workflow_dispatch rejects any other input.
                "app_name": instance_name,
                "callback_base_url": _callback_base_url(),
            }
        ),
    )
    return {
        **reference,
        "pipeline": "container-termination",
        "operation": "terminate",
    }
