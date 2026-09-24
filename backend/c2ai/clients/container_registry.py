"""Container registry adapter used to discover deployable image tags."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from c2ai.clients.http import http_client
from c2ai.config import get_settings
from c2ai.core.exceptions import (
    ContainerImageTagNotFound,
    ContainerRegistryNotSupported,
    InvalidContainerImageRepository,
    ServiceUnavailableError,
)

_DOCKER_HUB_ALIASES = {
    "docker hub",
    "dockerhub",
    "docker.io",
    "hub.docker.com",
    "index.docker.io",
    "registry-1.docker.io",
}
_DOCKER_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_ECR_ALIASES = {
    "ecr",
    "aws ecr",
    "amazon ecr",
    "elastic container registry",
    "amazon elastic container registry",
}
_GHCR_ALIASES = {"ghcr", "ghcr.io", "github container registry"}
_PRIVATE_ALIASES = {"private", "private registry", "custom", "custom registry"}
_REGISTRY_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
_ECR_HOST_RE = re.compile(
    r"^[0-9]{12}\.dkr\.ecr(?:-fips)?\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?$"
)
_REGISTRY_PATH_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
_ECR_FORMAT_HINT = (
    "Amazon ECR. Use the "
    "<account-id>.dkr.ecr.<region>.amazonaws.com/<repository> format."
)
_V2_FORMAT_HINT = "this registry. Use the <registry-host>/<repository> format."
_V2_CREDENTIAL_REJECTED = (
    "The container registry rejected the registered credentials. Amazon ECR "
    "authorization tokens expire after 12 hours, so a stored token has to be "
    "refreshed."
)
_V2_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
    )
)


@dataclass(frozen=True)
class ContainerRegistryTag:
    """Safe tag metadata returned by a registry adapter."""

    name: str
    digest: str | None
    last_updated: datetime | None


@dataclass(frozen=True)
class ContainerRegistryTagPage:
    """Limited tag page plus the registry's total available count."""

    items: list[ContainerRegistryTag]
    total: int


@dataclass(frozen=True)
class _RegistryV2Target:
    """One repository addressed through the Docker Registry HTTP API v2."""

    host: str
    path: str


def _resolve_registry_v2_target(
    registry: str,
    repository: str,
) -> _RegistryV2Target | None:
    """Split a non-Docker-Hub repository into its registry host and path."""

    normalized = registry.strip().lower()
    if normalized in _DOCKER_HUB_ALIASES:
        return None

    cleaned = repository.strip().strip("/")
    host, _, path = cleaned.partition("/")

    if normalized in _ECR_ALIASES:
        if not _ECR_HOST_RE.fullmatch(host) or not path:
            raise InvalidContainerImageRepository(repository, _ECR_FORMAT_HINT)
        return _validated_v2_target(repository, host, path, _ECR_FORMAT_HINT)

    if normalized in _GHCR_ALIASES:
        ghcr_path = path if host == "ghcr.io" else cleaned
        return _validated_v2_target(repository, "ghcr.io", ghcr_path, _V2_FORMAT_HINT)

    if normalized in _PRIVATE_ALIASES:
        return _validated_v2_target(repository, host, path, _V2_FORMAT_HINT)

    # An ECR registry recorded as its own host rather than by name.
    if _ECR_HOST_RE.fullmatch(normalized):
        registry_path = path if host == normalized else cleaned
        return _validated_v2_target(repository, normalized, registry_path, _ECR_FORMAT_HINT)

    raise ContainerRegistryNotSupported(registry)


def _is_registry_host(host: str) -> bool:
    """Recognize a registry host without treating a path segment as one."""

    return bool(_REGISTRY_HOST_RE.fullmatch(host)) and ("." in host or ":" in host)


def _validated_v2_target(
    repository: str,
    host: str,
    path: str,
    expected_format: str,
) -> _RegistryV2Target:
    """Reject hosts and paths a registry adapter cannot address safely."""

    if not _is_registry_host(host) or not _REGISTRY_PATH_RE.fullmatch(path):
        raise InvalidContainerImageRepository(repository, expected_format)
    return _RegistryV2Target(host=host, path=path)


def _quote_repository_path(path: str) -> str:
    """Percent-encode each repository segment without escaping the separators."""

    return "/".join(quote(segment, safe="") for segment in path.split("/"))


def _basic_credential(identifier: str, secret: str) -> str:
    """Build the Basic credential, accepting a pre-encoded registry token."""

    try:
        decoded = base64.b64decode(secret, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        decoded = ""
    # `aws ecr get-authorization-token` already returns base64("AWS:<password>").
    if decoded.startswith(f"{identifier}:"):
        return secret
    return base64.b64encode(f"{identifier}:{secret}".encode()).decode("ascii")


def build_image_reference(registry: str, repository: str, tag: str) -> str:
    """Fully qualified reference for one registered repository and tag."""

    try:
        target = _resolve_registry_v2_target(registry, repository)
    except (ContainerRegistryNotSupported, InvalidContainerImageRepository):
        return f"{repository}:{tag}"
    if target is None:
        return f"docker.io/{repository}:{tag}"
    return f"{target.host}/{target.path}:{tag}"


class ContainerRegistryClient:
    """Discover Docker Hub tags without exposing registry credentials."""

    def __init__(
        self,
        *,
        docker_hub_api_url: str | None = None,
        credential_provider_url: str | None = None,
        credential_provider_token: str | None = None,
    ) -> None:
        self.docker_hub_api_url = (
            docker_hub_api_url
            or get_settings().docker_hub_api_url
            or "https://hub.docker.com"
        ).rstrip("/")
        self.credential_provider_url = (
            credential_provider_url or get_settings().registry_credential_provider_url
        ).rstrip("/")
        self.credential_provider_token = (
            credential_provider_token or get_settings().registry_credential_provider_token
        )

    async def list_tags(
        self,
        *,
        registry: str,
        repository: str,
        credential_id: str | None,
        limit: int,
        username: str | None = None,
        password: str | None = None,
    ) -> ContainerRegistryTagPage:
        """Return up to ``limit`` normalized tags from the registered registry."""

        target = _resolve_registry_v2_target(registry, repository)
        if target is not None:
            return await self._list_registry_v2_tags(
                target,
                credential_id=credential_id,
                limit=limit,
                username=username,
                password=password,
            )

        namespace, repository_name = self._docker_hub_repository(
            registry,
            repository,
        )

        try:
            async with http_client(15.0) as client:
                headers = await self._docker_hub_headers(
                    client,
                    credential_id,
                    username=username,
                    password=password,
                )

                return await self._list_docker_hub_tags(
                    client,
                    namespace=namespace,
                    repository=repository_name,
                    headers=headers,
                    limit=limit,
                )
        except (ContainerRegistryNotSupported, InvalidContainerImageRepository):
            raise
        except ServiceUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ServiceUnavailableError(
                "The container registry could not return image tags."
            ) from error

    async def get_tag(
        self,
        *,
        registry: str,
        repository: str,
        tag: str,
        credential_id: str | None,
        username: str | None = None,
        password: str | None = None,
    ) -> ContainerRegistryTag:
        """Validate and return one exact tag from the registered registry."""

        target = _resolve_registry_v2_target(registry, repository)
        if target is not None:
            return await self._get_registry_v2_tag(
                target,
                repository=repository,
                tag=tag,
                credential_id=credential_id,
                username=username,
                password=password,
            )

        namespace, repository_name = self._docker_hub_repository(
            registry,
            repository,
        )
        if not _DOCKER_TAG_RE.fullmatch(tag):
            raise ContainerImageTagNotFound(repository, tag)

        url = (
            f"{self.docker_hub_api_url}/v2/namespaces/"
            f"{quote(namespace, safe='')}/repositories/"
            f"{quote(repository_name, safe='')}/tags/{quote(tag, safe='')}"
        )
        try:
            async with http_client(15.0) as client:
                headers = await self._docker_hub_headers(
                    client,
                    credential_id,
                    username=username,
                    password=password,
                )
                response = await client.get(url, headers=headers)
                if response.status_code == 404:
                    raise ContainerImageTagNotFound(repository, tag)
                response.raise_for_status()
                parsed = self._parse_tag(response.json())
                if parsed is None or parsed.name != tag:
                    raise ServiceUnavailableError(
                        "Docker Hub returned an invalid tag response."
                    )
                return parsed
        except (
            ContainerImageTagNotFound,
            ContainerRegistryNotSupported,
            InvalidContainerImageRepository,
            ServiceUnavailableError,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ServiceUnavailableError(
                "The container registry could not validate the image tag."
            ) from error

    async def _list_registry_v2_tags(
        self,
        target: _RegistryV2Target,
        *,
        credential_id: str | None,
        limit: int,
        username: str | None,
        password: str | None,
    ) -> ContainerRegistryTagPage:
        """Read bounded Registry v2 tag pages using the registry's own cursor."""

        repository = f"{target.host}/{target.path}"
        url = f"https://{target.host}/v2/{_quote_repository_path(target.path)}/tags/list"
        try:
            async with http_client(15.0) as client:
                headers = await self._registry_v2_headers(
                    client,
                    credential_id,
                    username=username,
                    password=password,
                )
                tags: list[ContainerRegistryTag] = []
                seen: set[str] = set()
                cursor: str | None = None

                while len(tags) < limit:
                    page_size = min(100, limit - len(tags))
                    params: dict[str, Any] = {"n": page_size}
                    if cursor is not None:
                        params["last"] = cursor
                    response = await client.get(url, params=params, headers=headers)
                    self._raise_for_registry_v2_status(response, repository)
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ServiceUnavailableError(
                            "The container registry returned an invalid tag response."
                        )
                    # The v2 spec allows a null tag list for an empty repository.
                    page_tags = body.get("tags") or []
                    if not isinstance(page_tags, list):
                        raise ServiceUnavailableError(
                            "The container registry returned an invalid tag response."
                        )

                    for name in page_tags:
                        if not isinstance(name, str) or not _DOCKER_TAG_RE.fullmatch(name):
                            continue
                        if name in seen:
                            continue
                        tags.append(
                            ContainerRegistryTag(
                                name=name,
                                digest=None,
                                last_updated=None,
                            )
                        )
                        seen.add(name)
                        if len(tags) == limit:
                            break

                    if len(page_tags) < page_size:
                        break
                    last_name = page_tags[-1]
                    if not isinstance(last_name, str) or last_name == cursor:
                        break
                    cursor = last_name

                return ContainerRegistryTagPage(items=tags, total=len(tags))
        except (
            ContainerRegistryNotSupported,
            InvalidContainerImageRepository,
            ServiceUnavailableError,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ServiceUnavailableError(
                "The container registry could not return image tags."
            ) from error

    async def _get_registry_v2_tag(
        self,
        target: _RegistryV2Target,
        *,
        repository: str,
        tag: str,
        credential_id: str | None,
        username: str | None,
        password: str | None,
    ) -> ContainerRegistryTag:
        """Validate one exact tag through the registry's manifest endpoint."""

        if not _DOCKER_TAG_RE.fullmatch(tag):
            raise ContainerImageTagNotFound(repository, tag)

        url = (
            f"https://{target.host}/v2/{_quote_repository_path(target.path)}"
            f"/manifests/{quote(tag, safe='')}"
        )
        try:
            async with http_client(15.0) as client:
                headers = await self._registry_v2_headers(
                    client,
                    credential_id,
                    username=username,
                    password=password,
                )
                response = await client.get(
                    url,
                    headers={**headers, "Accept": _V2_MANIFEST_ACCEPT},
                )
                if response.status_code == 404:
                    raise ContainerImageTagNotFound(repository, tag)
                self._raise_for_registry_v2_status(response, repository)
                response.raise_for_status()
                digest = response.headers.get("Docker-Content-Digest")
                return ContainerRegistryTag(
                    name=tag,
                    digest=digest if isinstance(digest, str) and digest else None,
                    last_updated=None,
                )
        except (
            ContainerImageTagNotFound,
            ContainerRegistryNotSupported,
            InvalidContainerImageRepository,
            ServiceUnavailableError,
        ):
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ServiceUnavailableError(
                "The container registry could not validate the image tag."
            ) from error

    async def _registry_v2_headers(
        self,
        client: httpx.AsyncClient,
        credential_id: str | None,
        *,
        username: str | None,
        password: str | None,
    ) -> dict[str, str]:
        """Build Registry v2 headers, resolving credentials only when referenced."""

        headers = {"Accept": "application/json"}
        if username is not None or password is not None:
            if not username or not password:
                raise ServiceUnavailableError(
                    "The registered container registry credential is incomplete."
                )
            identifier, secret = username, password
        elif credential_id is not None:
            identifier, secret = await self._resolve_credential(client, credential_id)
        else:
            return headers
        headers["Authorization"] = f"Basic {_basic_credential(identifier, secret)}"
        return headers

    @staticmethod
    def _raise_for_registry_v2_status(response: Any, repository: str) -> None:
        """Translate the registry's auth and lookup failures into Athena errors."""

        if response.status_code in (401, 403):
            raise ServiceUnavailableError(_V2_CREDENTIAL_REJECTED)
        if response.status_code == 404:
            raise InvalidContainerImageRepository(repository, _V2_FORMAT_HINT)

    @staticmethod
    def _docker_hub_repository(registry: str, repository: str) -> tuple[str, str]:
        """Validate and split one registered Docker Hub repository path."""

        if registry.strip().lower() not in _DOCKER_HUB_ALIASES:
            raise ContainerRegistryNotSupported(registry)
        repository_parts = repository.split("/")
        if len(repository_parts) != 2 or not all(repository_parts):
            raise InvalidContainerImageRepository(repository)
        return repository_parts[0], repository_parts[1]

    async def _docker_hub_headers(
        self,
        client: httpx.AsyncClient,
        credential_id: str | None,
        *,
        username: str | None,
        password: str | None,
    ) -> dict[str, str]:
        """Build safe request headers, resolving credentials only when referenced."""

        headers = {"Accept": "application/json"}
        if username is not None or password is not None:
            if not username or not password:
                raise ServiceUnavailableError(
                    "The registered container registry credential is incomplete."
                )
            access_token = await self._create_docker_hub_access_token(
                client,
                identifier=username,
                secret=password,
            )
            headers["Authorization"] = f"Bearer {access_token}"
            return headers
        if credential_id is None:
            return headers
        identifier, secret = await self._resolve_credential(client, credential_id)
        access_token = await self._create_docker_hub_access_token(
            client,
            identifier=identifier,
            secret=secret,
        )
        headers["Authorization"] = f"Bearer {access_token}"
        return headers

    async def _resolve_credential(
        self,
        client: httpx.AsyncClient,
        credential_id: str,
    ) -> tuple[str, str]:
        """Resolve an opaque credential ID only for the outbound registry call."""

        if not self.credential_provider_url or not self.credential_provider_token:
            raise ServiceUnavailableError(
                "Container registry credential resolution is not configured."
            )
        response = await client.get(
            f"{self.credential_provider_url}/credentials/{quote(credential_id, safe='')}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.credential_provider_token}",
            },
        )
        response.raise_for_status()
        body = response.json()
        identifier = body.get("identifier") if isinstance(body, dict) else None
        secret = body.get("secret") if isinstance(body, dict) else None
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or not isinstance(secret, str)
            or not secret
        ):
            raise ServiceUnavailableError(
                "The registry credential provider returned an invalid credential."
            )
        return identifier.strip(), secret

    async def _create_docker_hub_access_token(
        self,
        client: httpx.AsyncClient,
        *,
        identifier: str,
        secret: str,
    ) -> str:
        """Exchange provider-held credentials for a short-lived Docker Hub token."""

        response = await client.post(
            f"{self.docker_hub_api_url}/v2/auth/token",
            json={"identifier": identifier, "secret": secret},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
        access_token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise ServiceUnavailableError(
                "Docker Hub returned an invalid access token."
            )
        return access_token

    async def _list_docker_hub_tags(
        self,
        client: httpx.AsyncClient,
        *,
        namespace: str,
        repository: str,
        headers: dict[str, str],
        limit: int,
    ) -> ContainerRegistryTagPage:
        """Read bounded Docker Hub pages without following provider-supplied URLs."""

        url = (
            f"{self.docker_hub_api_url}/v2/namespaces/"
            f"{quote(namespace, safe='')}/repositories/"
            f"{quote(repository, safe='')}/tags"
        )
        tags: list[ContainerRegistryTag] = []
        seen: set[str] = set()
        page = 1
        total = 0

        while len(tags) < limit:
            page_size = min(100, limit - len(tags))
            response = await client.get(
                url,
                params={"page": page, "page_size": page_size},
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ServiceUnavailableError(
                    "Docker Hub returned an invalid tag response."
                )
            results = body.get("results")
            if not isinstance(results, list):
                raise ServiceUnavailableError(
                    "Docker Hub returned an invalid tag response."
                )
            if page == 1:
                count = body.get("count")
                if isinstance(count, int) and count >= 0:
                    total = count

            for item in results:
                tag = self._parse_tag(item)
                if tag is not None and tag.name not in seen:
                    tags.append(tag)
                    seen.add(tag.name)
                    if len(tags) == limit:
                        break

            if not results or not body.get("next"):
                break
            page += 1

        return ContainerRegistryTagPage(items=tags, total=max(total, len(tags)))

    @staticmethod
    def _parse_tag(item: Any) -> ContainerRegistryTag | None:
        """Normalize one Docker Hub tag and discard malformed provider data."""

        if not isinstance(item, dict):
            return None
        name = item.get("name")
        if not isinstance(name, str) or not _DOCKER_TAG_RE.fullmatch(name):
            return None

        digest = item.get("digest")
        if not isinstance(digest, str) or not digest:
            images = item.get("images")
            if isinstance(images, dict):
                images = [images]
            digest = next(
                (
                    image.get("digest")
                    for image in images or []
                    if isinstance(image, dict)
                    and isinstance(image.get("digest"), str)
                    and image.get("digest")
                ),
                None,
            )

        last_updated = item.get("last_updated")
        parsed_last_updated = None
        if isinstance(last_updated, str):
            try:
                parsed_last_updated = datetime.fromisoformat(
                    last_updated.replace("Z", "+00:00")
                )
            except ValueError:
                parsed_last_updated = None

        return ContainerRegistryTag(
            name=name,
            digest=digest,
            last_updated=parsed_last_updated,
        )
