"""Tests for secure Docker Hub and Registry v2 image-tag discovery."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from c2ai.clients.container_registry import (
    ContainerRegistryClient,
    build_image_reference,
)
from c2ai.core.exceptions import (
    ContainerImageTagNotFound,
    ContainerRegistryNotSupported,
    InvalidContainerImageRepository,
    ServiceUnavailableError,
)


def _response(body: object, *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    response.json.return_value = body
    return response


def _http_context(http_client: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=http_client)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


@pytest.mark.asyncio
async def test_lists_public_docker_hub_tags_as_safe_metadata():
    http_client = MagicMock()
    http_client.get = AsyncMock(
        return_value=_response(
            {
                "count": 2,
                "next": None,
                "results": [
                    {
                        "name": "2.0.0",
                        "images": [{"digest": "sha256:two"}],
                        "last_updated": "2026-08-15T09:30:00Z",
                    },
                    {
                        "name": "latest",
                        "images": [{"digest": "sha256:latest"}],
                        "last_updated": "not-a-date",
                    },
                ],
            }
        )
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        page = await ContainerRegistryClient(
            docker_hub_api_url="https://hub.docker.test"
        ).list_tags(
            registry="Docker Hub",
            repository="amberd/chat-service",
            credential_id=None,
            limit=100,
        )

    assert page.total == 2
    assert [item.name for item in page.items] == ["2.0.0", "latest"]
    assert page.items[0].digest == "sha256:two"
    assert page.items[0].last_updated.isoformat() == "2026-08-15T09:30:00+00:00"
    assert page.items[1].last_updated is None
    request = http_client.get.await_args
    assert request.args[0].endswith(
        "/v2/namespaces/amberd/repositories/chat-service/tags"
    )
    assert "Authorization" not in request.kwargs["headers"]


@pytest.mark.asyncio
async def test_resolves_private_credential_and_uses_short_lived_bearer_token():
    credential_response = _response(
        {"identifier": "registry-user", "secret": "dckr_pat_sensitive"}
    )
    tags_response = _response(
        {
            "count": 1,
            "next": None,
            "results": [{"name": "1.4.0", "images": []}],
        }
    )
    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=[credential_response, tags_response])
    http_client.post = AsyncMock(
        return_value=_response({"access_token": "short-lived-access-token"})
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        page = await ContainerRegistryClient(
            docker_hub_api_url="https://hub.docker.test",
            credential_provider_url="https://credentials.internal/v1",
            credential_provider_token="provider-token",
        ).list_tags(
            registry="docker.io",
            repository="amberd/private-service",
            credential_id="dockerhub/private credential",
            limit=20,
        )

    assert [item.name for item in page.items] == ["1.4.0"]
    credential_request = http_client.get.await_args_list[0]
    assert credential_request.args[0].endswith(
        "/credentials/dockerhub%2Fprivate%20credential"
    )
    assert credential_request.kwargs["headers"]["Authorization"] == (
        "Bearer provider-token"
    )
    assert http_client.post.await_args.kwargs["json"] == {
        "identifier": "registry-user",
        "secret": "dckr_pat_sensitive",
    }
    tags_request = http_client.get.await_args_list[1]
    assert tags_request.kwargs["headers"]["Authorization"] == (
        "Bearer short-lived-access-token"
    )
    assert "dckr_pat_sensitive" not in repr(page)


@pytest.mark.asyncio
async def test_uses_registered_username_and_password_without_credential_provider():
    http_client = MagicMock()
    http_client.post = AsyncMock(
        return_value=_response({"access_token": "short-lived-access-token"})
    )
    http_client.get = AsyncMock(
        return_value=_response(
            {
                "count": 1,
                "next": None,
                "results": [{"name": "1.5.0", "images": []}],
            }
        )
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        page = await ContainerRegistryClient(
            docker_hub_api_url="https://hub.docker.test",
        ).list_tags(
            registry="Docker Hub",
            repository="amberd/private-service",
            credential_id=None,
            limit=20,
            username="registry-user",
            password="dckr_pat_sensitive",
        )

    assert [item.name for item in page.items] == ["1.5.0"]
    assert http_client.post.await_args.kwargs["json"] == {
        "identifier": "registry-user",
        "secret": "dckr_pat_sensitive",
    }
    assert http_client.get.await_args.kwargs["headers"]["Authorization"] == (
        "Bearer short-lived-access-token"
    )


@pytest.mark.asyncio
async def test_paginates_without_following_registry_supplied_next_url():
    first_page = {
        "count": 101,
        "next": "https://untrusted.example/second-page",
        "results": [{"name": f"tag-{index}", "images": []} for index in range(100)],
    }
    second_page = {
        "count": 101,
        "next": None,
        "results": [{"name": "tag-100", "images": []}],
    }
    http_client = MagicMock()
    http_client.get = AsyncMock(
        side_effect=[_response(first_page), _response(second_page)]
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        page = await ContainerRegistryClient(
            docker_hub_api_url="https://hub.docker.test"
        ).list_tags(
            registry="Docker Hub",
            repository="amberd/chat-service",
            credential_id=None,
            limit=101,
        )

    assert page.total == 101
    assert len(page.items) == 101
    assert (
        http_client.get.await_args_list[1]
        .args[0]
        .startswith("https://hub.docker.test/")
    )
    assert http_client.get.await_args_list[1].kwargs["params"]["page"] == 2


@pytest.mark.asyncio
async def test_private_repository_requires_credential_provider_configuration():
    with patch.dict(
        "os.environ",
        {
            "REGISTRY_CREDENTIAL_PROVIDER_URL": "",
            "REGISTRY_CREDENTIAL_PROVIDER_TOKEN": "",
        },
    ):
        with pytest.raises(ServiceUnavailableError, match="not configured"):
            await ContainerRegistryClient().list_tags(
                registry="Docker Hub",
                repository="amberd/private-service",
                credential_id="dockerhub-private",
                limit=20,
            )


@pytest.mark.asyncio
async def test_get_tag_validates_one_exact_docker_hub_tag():
    http_client = MagicMock()
    http_client.get = AsyncMock(
        return_value=_response(
            {
                "name": "2.0.0",
                "images": [{"digest": "sha256:next"}],
                "last_updated": "2026-08-15T10:00:00Z",
            }
        )
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        tag = await ContainerRegistryClient(
            docker_hub_api_url="https://hub.docker.test"
        ).get_tag(
            registry="Docker Hub",
            repository="amberd/chat-service",
            tag="2.0.0",
            credential_id=None,
        )

    assert tag.name == "2.0.0"
    assert tag.digest == "sha256:next"
    assert http_client.get.await_args.args[0].endswith(
        "/v2/namespaces/amberd/repositories/chat-service/tags/2.0.0"
    )


@pytest.mark.asyncio
async def test_get_tag_maps_docker_hub_not_found_to_validation_error():
    http_client = MagicMock()
    http_client.get = AsyncMock(
        return_value=_response({"detail": "not found"}, status_code=404)
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        with pytest.raises(ContainerImageTagNotFound, match=r"9\.9\.9"):
            await ContainerRegistryClient(
                docker_hub_api_url="https://hub.docker.test"
            ).get_tag(
                registry="Docker Hub",
                repository="amberd/chat-service",
                tag="9.9.9",
                credential_id=None,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("registry", "repository", "exception"),
    [
        ("quay.io", "amberd/chat-service", ContainerRegistryNotSupported),
        ("Docker Hub", "chat-service", InvalidContainerImageRepository),
    ],
)
async def test_rejects_unsupported_registry_configuration(
    registry,
    repository,
    exception,
):
    with pytest.raises(exception):
        await ContainerRegistryClient().list_tags(
            registry=registry,
            repository=repository,
            credential_id=None,
            limit=20,
        )


def _v2_response(
    body: object,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    response = _response(body, status_code=status_code)
    response.headers = headers or {}
    return response


_ECR_REPOSITORY = "575108952327.dkr.ecr.us-west-2.amazonaws.com/llm_gateway"


@pytest.mark.asyncio
async def test_lists_ecr_tags_over_the_registry_v2_api():
    http_client = MagicMock()
    http_client.get = AsyncMock(
        return_value=_v2_response({"name": "llm_gateway", "tags": ["1.0.0", "latest"]})
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        page = await ContainerRegistryClient().list_tags(
            registry="ecr",
            repository=_ECR_REPOSITORY,
            credential_id=None,
            limit=100,
            username="AWS",
            password="ecr-authorization-token",
        )

    assert [item.name for item in page.items] == ["1.0.0", "latest"]
    assert page.total == 2
    request = http_client.get.await_args
    assert request.args[0] == (
        "https://575108952327.dkr.ecr.us-west-2.amazonaws.com/v2/llm_gateway/tags/list"
    )
    assert request.kwargs["params"] == {"n": 100}
    authorization = request.kwargs["headers"]["Authorization"]
    assert authorization == "Basic " + base64.b64encode(
        b"AWS:ecr-authorization-token"
    ).decode("ascii")


@pytest.mark.asyncio
async def test_ecr_tag_listing_follows_the_registry_cursor_up_to_the_limit():
    http_client = MagicMock()
    http_client.get = AsyncMock(
        side_effect=[
            _v2_response({"tags": ["a", "b"]}),
            _v2_response({"tags": ["c"]}),
        ]
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        page = await ContainerRegistryClient().list_tags(
            registry="Amazon ECR",
            repository=_ECR_REPOSITORY,
            credential_id=None,
            limit=2,
            username="AWS",
            password="token",
        )

    # The first page already satisfies the limit, so no cursor request follows.
    assert [item.name for item in page.items] == ["a", "b"]
    assert http_client.get.await_count == 1


@pytest.mark.asyncio
async def test_ecr_accepts_a_pre_encoded_authorization_token():
    encoded = base64.b64encode(b"AWS:secret-password").decode("ascii")
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=_v2_response({"tags": ["latest"]}))

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        await ContainerRegistryClient().list_tags(
            registry="ecr",
            repository=_ECR_REPOSITORY,
            credential_id=None,
            limit=10,
            username="AWS",
            password=encoded,
        )

    headers = http_client.get.await_args.kwargs["headers"]
    assert headers["Authorization"] == f"Basic {encoded}"


@pytest.mark.asyncio
async def test_expired_ecr_credentials_report_a_refreshable_failure():
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=_v2_response({}, status_code=401))

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        with pytest.raises(ServiceUnavailableError) as error:
            await ContainerRegistryClient().list_tags(
                registry="ecr",
                repository=_ECR_REPOSITORY,
                credential_id=None,
                limit=10,
                username="AWS",
                password="expired",
            )

    assert "expire" in error.value.detail


@pytest.mark.asyncio
async def test_unknown_ecr_repository_is_reported_as_invalid():
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=_v2_response({}, status_code=404))

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        with pytest.raises(InvalidContainerImageRepository):
            await ContainerRegistryClient().list_tags(
                registry="ecr",
                repository=_ECR_REPOSITORY,
                credential_id=None,
                limit=10,
                username="AWS",
                password="token",
            )


@pytest.mark.asyncio
async def test_validates_one_ecr_tag_through_the_manifest_endpoint():
    http_client = MagicMock()
    http_client.get = AsyncMock(
        return_value=_v2_response(
            {},
            headers={"Docker-Content-Digest": "sha256:manifest"},
        )
    )

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        tag = await ContainerRegistryClient().get_tag(
            registry="ecr",
            repository=_ECR_REPOSITORY,
            tag="latest",
            credential_id=None,
            username="AWS",
            password="token",
        )

    assert tag.name == "latest"
    assert tag.digest == "sha256:manifest"
    request = http_client.get.await_args
    assert request.args[0] == (
        "https://575108952327.dkr.ecr.us-west-2.amazonaws.com"
        "/v2/llm_gateway/manifests/latest"
    )
    assert "manifest.v2+json" in request.kwargs["headers"]["Accept"]


@pytest.mark.asyncio
async def test_missing_ecr_tag_raises_tag_not_found():
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=_v2_response({}, status_code=404))

    with patch(
        "c2ai.clients.container_registry.http_client",
        return_value=_http_context(http_client),
    ):
        with pytest.raises(ContainerImageTagNotFound):
            await ContainerRegistryClient().get_tag(
                registry="ecr",
                repository=_ECR_REPOSITORY,
                tag="9.9.9",
                credential_id=None,
                username="AWS",
                password="token",
            )


@pytest.mark.asyncio
async def test_ecr_repository_without_its_registry_host_is_rejected():
    with pytest.raises(InvalidContainerImageRepository):
        await ContainerRegistryClient().list_tags(
            registry="ecr",
            repository="llm_gateway",
            credential_id=None,
            limit=10,
            username="AWS",
            password="token",
        )


@pytest.mark.parametrize(
    ("registry", "repository", "expected"),
    [
        ("Docker Hub", "amberd/chat-service", "docker.io/amberd/chat-service:1.2.3"),
        (
            "ecr",
            _ECR_REPOSITORY,
            f"{_ECR_REPOSITORY}:1.2.3",
        ),
        ("ghcr.io", "amberd-ai/chatbot", "ghcr.io/amberd-ai/chatbot:1.2.3"),
    ],
)
def test_builds_registry_aware_image_references(registry, repository, expected):
    assert build_image_reference(registry, repository, "1.2.3") == expected
