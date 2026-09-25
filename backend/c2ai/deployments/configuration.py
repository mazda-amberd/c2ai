"""Deployment snapshots: validated parameters and configuration for one instance."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from c2ai.config import get_settings
from c2ai.constants.registered_application import (
    CUSTOMER_NAME_PARAMETER,
    DEPLOYMENT_IDENTITY_PARAMETERS,
    ApplicationType,
)
from c2ai.core.exceptions import UnprocessableEntityError
from c2ai.models.registered_application import RegisteredApplicationVersion
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


# Workflow input every amberd-ai pipeline declares to attribute the run to a
# person. Athena fills it from the triggering user so neither registration nor
# deployment has to ask for it.
SLACK_USER_PARAMETER = "slack_user"
_INSTANCE_NAME_SLUG_RE = re.compile(r"[^a-z0-9]+")


def configured_version(configuration: dict[str, Any], application_type: str) -> str | None:
    """The version a deployment snapshot runs, or ``None`` when it records none.

    Containers run ``container.image_tag``. GitHub workflows record an upgrade
    target as ``github.version``; an initial deployment's selected ref is the
    workflow's ``branch`` input.
    """

    if application_type == ApplicationType.CONTAINERIZED.value:
        container = configuration.get("container")
        tag = container.get("image_tag") if isinstance(container, dict) else None
        return tag if isinstance(tag, str) and tag else None
    github = configuration.get("github")
    version = github.get("version") if isinstance(github, dict) else None
    if isinstance(version, str) and version:
        return version
    parameters = configuration.get("parameters")
    branch = parameters.get("branch") if isinstance(parameters, dict) else None
    return branch if isinstance(branch, str) and branch else None


def managed_secret_references(secrets: Iterable[Any]) -> list[dict[str, str]]:
    """Reference-only view of managed secrets for a deployment snapshot.

    Values never leave the secret provider; the pipeline resolves each
    ``reference`` into a Kubernetes Secret exposed as ``environment_variable``.
    """

    return [
        {
            "id": str(secret.id),
            "name": secret.name,
            "environment_variable": secret.environment_variable,
            "reference": secret.secret_reference,
        }
        for secret in secrets
        if secret.secret_reference
    ]


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
    managed_secrets: Iterable[Any] = (),
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
        stringify_inputs(
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
        **({CUSTOMER_NAME_PARAMETER: payload.customer_name} if payload.customer_name else {}),
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
        "managed_secrets": managed_secret_references(managed_secrets),
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

        # A value for this tier replaces the default.
        tier_value = (definition.tier_defaults or {}).get(str(tier))
        if key in supplied_parameters:
            value = supplied_parameters[key]
        elif tier_value is not None:
            value = tier_value
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


def _undeclared_identity_values(
    version: RegisteredApplicationVersion,
    supplied_parameters: dict[str, Any],
) -> dict[str, str]:
    """Customer and environment values sent for a workflow that does not declare them.

    They are recorded on the deployment (``customer_name`` names who it is for)
    but kept out of the workflow inputs and out of the host label, since the
    workflow never receives them.
    """

    declared = {definition.key for definition in version.parameters}
    values: dict[str, str] = {}
    for key in DEPLOYMENT_IDENTITY_PARAMETERS - declared:
        if key not in supplied_parameters:
            continue
        value = supplied_parameters[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise UnprocessableEntityError(
                f"Deployment parameter '{key}' must be text of at most 200 characters."
            )
        values[key] = value.strip()
    return values


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

    github = version.github_configuration
    if github is None:
        raise RuntimeError("GitHub registered application has no workflow configuration")
    # The code branch is what deploys unless another version is chosen.
    chosen_version = payload.version if payload.version is not None else github.code_ref

    resolved_instance_name = instance_name or payload.instance_name or ""
    supplied_parameters = dict(payload.parameters)
    identity = _undeclared_identity_values(version, supplied_parameters)
    for key in identity:
        del supplied_parameters[key]
    if chosen_version is not None and any(p.key == "branch" for p in version.parameters):
        supplied_parameters["branch"] = chosen_version
    resolved_parameters = _resolve_deployment_parameters(
        version,
        supplied_parameters,
        tier=payload.tier,
        instance_name=resolved_instance_name,
        triggered_by=triggered_by,
    )
    if chosen_version is not None:
        resolved_parameters["branch"] = chosen_version

    llm = _runtime_llm_configuration(version, payload.tier)
    customer_name = resolved_parameters.get(CUSTOMER_NAME_PARAMETER)
    if not isinstance(customer_name, str) or not customer_name.strip():
        customer_name = identity.get(CUSTOMER_NAME_PARAMETER)
    return {
        "parameters": resolved_parameters,
        **({CUSTOMER_NAME_PARAMETER: customer_name} if customer_name else {}),
        **({"llm": llm} if llm is not None else {}),
        "github": {
            "connection": github.github_connection_id,
            "code_repository": github.code_repository,
            "code_ref": github.code_ref,
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


def workflow_accepts_default_slack_user(repository: str) -> bool:
    """Whether this workflow repository's owner declares the slack_user input."""

    owner = repository.split("/", 1)[0].strip().lower()
    return bool(owner) and owner in get_settings().slack_user_default_owner_set


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


def stringify_inputs(values: dict[str, Any]) -> dict[str, str]:
    """Workflow inputs are strings; structured values travel as compact JSON."""

    return {
        key: value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        for key, value in values.items()
    }
