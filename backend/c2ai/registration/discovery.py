"""What a GitHub connection can see, to pick from while registering.

The wizard lists the repositories the connection's token reaches, then the
chosen repository's branches and tags, then the workflow files on the chosen
branch with what starts each, instead of having them typed in.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any
from uuid import UUID

import httpx
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.constants.registered_application import GitHubTriggerMethod
from c2ai.core.exceptions import ServiceUnavailableError, UnprocessableEntityError
from c2ai.schemas.registered_application import (
    GitHubRefOptions,
    GitHubRepositoryOption,
    GitHubRepositoryOptions,
    GitHubWorkflowOption,
    GitHubWorkflowOptions,
)

logger = logging.getLogger(__name__)

# Workflow files read per listing (each is one GitHub request).
_WORKFLOW_FILES = 50


async def github_client(
    db: AsyncSession,
    connection_id: str,
    repository: str | None,
    *,
    connections: ModuleType,
) -> GitHubActionsClient:
    """A client for a connection: a saved one, or (any other name) the server's own token."""

    runtime = await connections.resolve_github_connection(db, connection_id)
    try:
        UUID(connection_id)
        saved = True
    except ValueError:
        saved = False
    if saved and runtime is None:
        raise UnprocessableEntityError(
            "The selected GitHub connection no longer exists.", code="GitHubConnectionNotFound"
        )
    owner, _, name = (repository or "").partition("/")
    try:
        return GitHubActionsClient(
            repo_owner=owner or None,
            repo_name=name or None,
            github_token=runtime.token if runtime else None,
            api_base_url=runtime.api_base_url if runtime else "https://api.github.com",
        )
    except ValueError as error:
        raise UnprocessableEntityError(
            "This connection uses the server's GitHub token, and GITHUB_PAT is not set.",
            code="GitHubTokenMissing",
        ) from error


@asynccontextmanager
async def _asking_github() -> AsyncIterator[None]:
    """GitHub's refusals as the user's errors, its outages as the server's."""

    try:
        yield
    except httpx.HTTPStatusError as error:
        code = error.response.status_code
        if code == 401:
            raise UnprocessableEntityError(
                "GitHub refused the connection's token.", code="GitHubTokenRefused"
            ) from error
        if code in (403, 404):
            raise UnprocessableEntityError(
                "The connection's token cannot see this on GitHub.", code="GitHubNotVisible"
            ) from error
        raise ServiceUnavailableError(f"GitHub answered {code}.") from error
    except (httpx.RequestError, ValueError) as error:
        logger.warning("GitHub discovery failed: %s", error)
        raise ServiceUnavailableError("GitHub could not be reached.") from error


async def list_repositories(
    db: AsyncSession, connection_id: str, *, connections: ModuleType
) -> GitHubRepositoryOptions:
    client = await github_client(db, connection_id, None, connections=connections)
    async with _asking_github():
        found, truncated = await client.list_accessible_repositories()
    items = [
        GitHubRepositoryOption(
            full_name=repository["full_name"],
            default_branch=repository.get("default_branch"),
            private=bool(repository.get("private")),
        )
        for repository in found
        if isinstance(repository.get("full_name"), str)
    ]
    items.sort(key=lambda option: option.full_name.lower())
    return GitHubRepositoryOptions(items=items, truncated=truncated)


async def list_refs(
    db: AsyncSession, connection_id: str, repository: str, *, connections: ModuleType
) -> GitHubRefOptions:
    client = await github_client(db, connection_id, repository, connections=connections)
    async with _asking_github():
        found = await client.get_repository()
        if found is None:
            raise UnprocessableEntityError(
                f"{repository} was not found, or the connection's token cannot see it.",
                code="GitHubNotVisible",
            )
        branches, tags = await asyncio.gather(
            client.list_repository_branches(limit=200), client.list_repository_tags(limit=200)
        )
    return GitHubRefOptions(default_branch=found.get("default_branch"), branches=branches, tags=tags)


def _summary(text: str | None) -> tuple[str | None, list[str], bool]:
    """(name, triggers C2AI can use, readable) of a workflow file."""

    if text is None:
        return None, [], False
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, [], False
    if not isinstance(document, dict):
        return None, [], False
    # YAML 1.1 reads the key `on` as the boolean true.
    on: Any = document.get("on", document.get(True))
    if isinstance(on, str):
        events = {on}
    elif isinstance(on, (list, dict)):
        events = {str(event) for event in on}
    else:
        return None, [], False
    name = document.get("name")
    triggers = [trigger.value for trigger in GitHubTriggerMethod if trigger.value in events]
    return (str(name) if name else None), triggers, True


async def list_workflows(
    db: AsyncSession, connection_id: str, repository: str, ref: str, *, connections: ModuleType
) -> GitHubWorkflowOptions:
    """The workflow files on ``ref``, each with what starts it."""

    client = await github_client(db, connection_id, repository, connections=connections)
    async with _asking_github():
        paths = (await client.list_workflow_files(ref))[:_WORKFLOW_FILES]
        texts = await asyncio.gather(
            *(client.get_file(path, ref) for path in paths), return_exceptions=True
        )
    items = []
    for path, text in zip(paths, texts, strict=True):
        name, triggers, readable = _summary(text if isinstance(text, str) else None)
        items.append(
            GitHubWorkflowOption(
                path=path,
                name=name,
                triggers=[GitHubTriggerMethod(trigger) for trigger in triggers],
                readable=readable,
            )
        )
    return GitHubWorkflowOptions(items=items)
