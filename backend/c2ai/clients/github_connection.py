"""GitHub repository credential validation without persisting plaintext tokens."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx

from c2ai.crud.github_connection import github_api_base_url
from c2ai.core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitHubConnectionValidationResult:
    """Internal result of checking a token against an owner or repository."""

    valid: bool
    message: str
    repository: str | None = None


def _connection_coordinates(repository_url: str) -> tuple[str, str | None]:
    parsed = urlsplit(repository_url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    owner = segments[0]
    repository = segments[1] if len(segments) == 2 else None
    if repository and repository.endswith(".git"):
        repository = repository[:-4]
    return owner, repository


async def validate_github_repository_connection(
    *,
    repository_url: str,
    access_token: str,
    client: httpx.AsyncClient | None = None,
) -> GitHubConnectionValidationResult:
    """Check that a PAT authenticates against a GitHub owner or repository."""

    owner, repository = _connection_coordinates(repository_url)
    target_name = f"{owner}/{repository}" if repository else owner
    api_base_url = github_api_base_url(repository_url)
    url = (
        f"{api_base_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        if repository
        else f"{api_base_url}/users/{quote(owner, safe='')}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    owns_client = client is None
    github_client = client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    try:
        response = await github_client.get(url, headers=headers)
    except httpx.RequestError as error:
        logger.warning(
            "GitHub connection validation request failed for target=%s: %s",
            target_name,
            error.__class__.__name__,
        )
        raise ServiceUnavailableError(
            "GitHub could not be reached to validate the connection. Try again shortly."
        ) from error
    finally:
        if owns_client:
            await github_client.aclose()

    if response.status_code == 200:
        try:
            response_body = response.json()
        except ValueError:
            response_body = {}
        response_name = response_body.get("full_name" if repository else "login")
        validated_name = (
            response_name
            if isinstance(response_name, str) and response_name
            else target_name
        )
        return GitHubConnectionValidationResult(
            valid=True,
            message=f"Connection validated successfully for {validated_name}.",
            repository=validated_name if repository else None,
        )
    if response.status_code == 401:
        message = "GitHub rejected the personal access token."
    elif response.status_code == 403:
        message = (
            "The token is not authorized to access this GitHub repository."
            if repository
            else "The token is not authorized to access this GitHub owner."
        )
    elif response.status_code == 404:
        message = (
            "The repository was not found or the token does not have access to it."
            if repository
            else "The GitHub owner was not found or the token does not have access to it."
        )
    elif response.status_code == 429 or response.status_code >= 500:
        raise ServiceUnavailableError(
            "GitHub could not validate the connection. Try again shortly."
        )
    else:
        message = f"GitHub connection validation failed (status {response.status_code})."

    return GitHubConnectionValidationResult(
        valid=False,
        message=message,
        repository=target_name if repository else None,
    )
