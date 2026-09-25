"""Checks made while registering, so a mistake shows up before the first deploy.

``inspect_workflow`` reads a GitHub workflow through the chosen connection:
what starts it, the inputs it declares (the wizard imports them as
parameters), and whether C2AI will be able to start it. ``check_container_image``
looks the image and tag up in their registry with the credentials entered.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import Any, TypeVar

import httpx
import yaml
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.clients.container_registry import ContainerRegistryClient, build_image_reference
from c2ai.constants.registered_application import GitHubTriggerMethod, ParameterType
from c2ai.core.exceptions import AppException
from c2ai.registration.discovery import github_client
from c2ai.schemas.registered_application import (
    ContainerImageCheck,
    ContainerImageCheckRequest,
    GitHubWorkflowInspection,
    GitHubWorkflowInspectRequest,
    RegisteredParameterDefinition,
    RegistrationCheck,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# GitHub's workflow_dispatch input types, as C2AI parameter types.
_INPUT_TYPES = {
    "string": ParameterType.TEXT,
    "environment": ParameterType.TEXT,
    "number": ParameterType.NUMBER,
    "boolean": ParameterType.BOOLEAN,
    "choice": ParameterType.SELECT,
}
_TRIGGERS = [trigger.value for trigger in GitHubTriggerMethod]


class _Stop(Exception):
    """A GitHub call failed; the check that made it has been recorded."""


# ---------------------------------------------------------------------------
# Reading a workflow file
# ---------------------------------------------------------------------------


def _coerce(parameter_type: ParameterType, value: Any, options: list[str]) -> Any:
    """A workflow input's default as the value a C2AI parameter holds."""

    if value is None or value == "":
        return None
    if parameter_type == ParameterType.BOOLEAN:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return {"true": True, "false": False}.get(text)
    if parameter_type == ParameterType.NUMBER:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        try:
            number = float(str(value))
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    text = str(value)
    if parameter_type == ParameterType.SELECT and text not in options:
        return None
    return text


def _workflow_input(key: Any, spec: Any) -> RegisteredParameterDefinition | None:
    spec = spec if isinstance(spec, dict) else {}
    parameter_type = _INPUT_TYPES.get(str(spec.get("type") or "string").lower(), ParameterType.TEXT)
    options = (
        [str(option) for option in spec.get("options") or []]
        if parameter_type == ParameterType.SELECT
        else []
    )
    description = str(spec.get("description") or "").strip()[:1000]
    try:
        return RegisteredParameterDefinition(
            key=str(key),
            type=parameter_type,
            description=description or None,
            required=bool(spec.get("required", False)),
            default=_coerce(parameter_type, spec.get("default"), options),
            options=options,
        )
    except ValidationError:
        # A name C2AI cannot hold, or choices it cannot: left for the user.
        logger.info("Workflow input %r not importable", key)
        return None


def parse_workflow(text: str) -> tuple[list[str], list[RegisteredParameterDefinition], list[str]]:
    """(triggers, importable inputs, input names not importable) of a workflow file.

    Raises ValueError when the text is not a workflow.
    """

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError("it is not valid YAML") from error
    if not isinstance(document, dict):
        raise ValueError("it is not a workflow")
    # YAML 1.1 reads the key `on` as the boolean true.
    on = document.get("on", document.get(True))
    if isinstance(on, str):
        events: dict[str, Any] = {on: None}
    elif isinstance(on, list):
        events = {str(event): None for event in on}
    elif isinstance(on, dict):
        events = {str(event): spec for event, spec in on.items()}
    else:
        raise ValueError("it has no `on:` section")

    triggers = [trigger for trigger in _TRIGGERS if trigger in events]
    dispatch = events.get("workflow_dispatch")
    declared = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    inputs: list[RegisteredParameterDefinition] = []
    skipped: list[str] = []
    for key, spec in (declared or {}).items() if isinstance(declared, dict) else []:
        parameter = _workflow_input(key, spec)
        if parameter is None:
            skipped.append(str(key))
        else:
            inputs.append(parameter)
    return triggers, inputs, skipped


async def inspect_workflow(
    db: AsyncSession,
    request: GitHubWorkflowInspectRequest,
    *,
    connections: ModuleType,
) -> GitHubWorkflowInspection:
    """Read the workflow with the chosen connection and report each check."""

    checks: list[RegistrationCheck] = []
    triggers: list[str] = []
    inputs: list[RegisteredParameterDefinition] = []

    def check(name: str, ok: bool | None, detail: str) -> None:
        checks.append(RegistrationCheck(name=name, ok=ok, detail=detail))

    def done() -> GitHubWorkflowInspection:
        return GitHubWorkflowInspection(
            checks=checks,
            triggers=[GitHubTriggerMethod(trigger) for trigger in triggers],
            inputs=inputs,
        )

    async def ask(name: str, call: Callable[[], Awaitable[T]]) -> T:
        try:
            return await call()
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            check(
                name,
                False,
                "GitHub refused the connection's token."
                if code == 401
                else f"GitHub answered {code}.",
            )
        except (httpx.RequestError, ValueError) as error:
            logger.warning("Workflow check %s failed: %s", name, error)
            check(name, False, "GitHub could not be reached.")
        raise _Stop

    try:
        client = await github_client(
            db, request.github_connection_id, request.repository, connections=connections
        )
    except AppException as error:
        check("Connection", False, str(error.detail))
        return done()

    path, ref = request.workflow_file_path, request.ref
    try:
        repository = await ask("Repository", client.get_repository)
        if repository is None:
            check(
                "Repository",
                False,
                f"{request.repository} was not found, or the connection's token cannot see it.",
            )
            return done()
        check("Repository", True, f"{request.repository} is reachable with this connection.")

        if not await ask("Branch / Ref", lambda: client.ref_exists(ref)):
            check("Branch / Ref", False, f"{request.repository} has no branch or tag '{ref}'.")
            return done()
        check("Branch / Ref", True, f"'{ref}' exists.")

        text = await ask("Workflow file", lambda: client.get_file(path, ref))
        if text is None:
            check("Workflow file", False, f"There is no file {path} on '{ref}'.")
            return done()
        try:
            triggers, inputs, skipped = parse_workflow(text)
        except ValueError as error:
            check("Workflow file", False, f"{path} is not a workflow GitHub can run: {error}.")
            return done()
        declared = f"{len(inputs)} input{'' if len(inputs) == 1 else 's'}"
        if skipped:
            declared += f" (not importable: {', '.join(skipped)})"
        check("Workflow file", True, f"Found on '{ref}', declaring {declared}.")

        if not triggers:
            check(
                "Trigger",
                False,
                "C2AI cannot start it: its `on:` section has neither workflow_dispatch "
                "nor repository_dispatch.",
            )
        elif request.trigger_method and request.trigger_method.value not in triggers:
            check(
                "Trigger",
                False,
                f"It is started by {' or '.join(triggers)}, not {request.trigger_method.value}.",
            )
        else:
            check("Trigger", True, f"Started by {' or '.join(triggers)}.")

        workflow = await ask("GitHub Actions", lambda: client.get_workflow(path))
        default_branch = repository.get("default_branch") or "the default branch"
        if workflow is None:
            check(
                "GitHub Actions",
                False,
                "GitHub Actions does not list it. A workflow must also be on the "
                f"repository's default branch ({default_branch}) before it can be started.",
            )
        elif workflow.get("state") != "active":
            check("GitHub Actions", False, f"It is disabled in GitHub Actions ({workflow.get('state')}).")
        else:
            check("GitHub Actions", True, "Enabled in GitHub Actions.")
    except _Stop:
        return done()

    permissions = repository.get("permissions")
    if not isinstance(permissions, dict):
        check("Permission", None, "GitHub did not say what the connection may do; the first deploy will tell.")
    elif any(permissions.get(level) for level in ("admin", "maintain", "push")):
        check(
            "Permission",
            True,
            "The connection can write to the repository, as starting a workflow needs "
            "(a fine-grained token also needs Actions: write).",
        )
    else:
        check(
            "Permission",
            False,
            "The connection can only read this repository; starting a workflow needs write access.",
        )
    return done()


# ---------------------------------------------------------------------------
# Looking an image up in its registry
# ---------------------------------------------------------------------------


async def check_container_image(
    db: AsyncSession,
    request: ContainerImageCheckRequest,
    *,
    registry_client: ContainerRegistryClient,
    applications: ModuleType,
    credentials: ModuleType,
) -> ContainerImageCheck:
    """Find the tag with the credentials entered (or stored, for the same username)."""

    username = request.registry_username
    password = request.registry_password.get_secret_value() if request.registry_password else None
    if username and password is None and request.application_id is not None:
        version = await applications.get_current_registered_application_version(
            db, request.application_id
        )
        configuration = version.container_configuration if version else None
        if configuration is not None and configuration.registry_username == username:
            stored = await credentials.resolve_container_registry_credentials(db, version.id)
            password = stored.password if stored else None
    if username and not password:
        return ContainerImageCheck(
            ok=False,
            detail="Enter the registry password or token to look up a private image.",
        )

    reference = build_image_reference(request.registry, request.image_registry, request.tag)
    try:
        found = await registry_client.get_tag(
            registry=request.registry,
            repository=request.image_registry,
            tag=request.tag,
            credential_id=None,
            username=username,
            password=password,
        )
    except AppException as error:
        return ContainerImageCheck(ok=False, detail=str(error.detail), image_reference=reference)
    return ContainerImageCheck(
        ok=True,
        detail=f"Found {reference}.",
        image_reference=reference,
        digest=found.digest,
    )
