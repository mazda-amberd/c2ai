"""Unit tests for GitHub client — workflow_dispatch and Actions API helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from c2ai.clients.github import (
    GITHUB_WORKFLOW_DEPLOY,
    GITHUB_WORKFLOW_MOVE_TIER,
    GITHUB_WORKFLOW_TERMINATE,
    GITHUB_WORKFLOW_UPDATE,
    cancel_workflow_run,
    check_repo_tag_exists,
    dispatch_github_move_tier_workflow,
    dispatch_github_terminate_workflow,
    dispatch_github_update_workflow,
    dispatch_github_workflow,
    expected_run_name,
    list_repo_branches,
    list_repo_tags,
)
from c2ai.core.exceptions import BadRequestError, ServiceUnavailableError
from c2ai.utils.host_labels import workflow_prepare_subdomain

# ---------------------------------------------------------------------------
# workflow_prepare_subdomain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("customer", "env", "expected"),
    [
        ("acme", "ada", "amberd-acme-ada"),
        ("Acme Co", "ada", "amberd-acme-co-ada"),
        ("x/y", "ada", "amberd-x-y-ada"),
        ("  Trimmed  ", "ada", "amberd-trimmed-ada"),
        ("hello world!", "ada", "amberd-hello-world-ada"),
        ("a--b", "ada", "amberd-a-b-ada"),
        ("--leading", "ada", "amberd-leading-ada"),
        ("acme", "ada-1", "amberd-acme-ada-1"),
    ],
)
def test_workflow_prepare_subdomain(customer: str, env: str, expected: str) -> None:
    assert workflow_prepare_subdomain(customer, env) == expected


# ---------------------------------------------------------------------------
# expected run-name helper
# ---------------------------------------------------------------------------

def test_expected_run_name_deploy():
    assert (
        expected_run_name(GITHUB_WORKFLOW_DEPLOY, "amberd-acme-ada", "ACME", "ADA")
        == "ada-deploy | acme-ada"
    )


def test_expected_run_name_update():
    assert (
        expected_run_name(GITHUB_WORKFLOW_UPDATE, "amberd-acme-ada")
        == "ada-update | amberd-acme-ada"
    )


def test_expected_run_name_migration():
    """
    Verify the expected GitHub run name for the move-tier workflow.

    Returns:
        None: This test asserts the expected run name format.
    """
    assert (
        expected_run_name(GITHUB_WORKFLOW_MOVE_TIER, "amberd-acme-ada")
        == "ada-move-to-tier | amberd-acme-ada"
    )


def test_expected_run_name_terminate():
    assert (
        expected_run_name(GITHUB_WORKFLOW_TERMINATE, "amberd-acme-ada")
        == "ada-terminate | amberd-acme-ada"
    )


# ---------------------------------------------------------------------------
# list_repo_branches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_repo_branches_raises_when_pat_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    with pytest.raises(ServiceUnavailableError, match="GITHUB_PAT"):
        await list_repo_branches("Inferaim", "devops")


@pytest.mark.asyncio
async def test_list_repo_branches_empty_on_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    inner = MagicMock()
    inner.get = AsyncMock(
        side_effect=httpx.ConnectError("all connection attempts failed"),
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        out = await list_repo_branches("Inferaim", "devops")

    assert out == []


# ---------------------------------------------------------------------------
# list_repo_tags
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_repo_tags_raises_when_pat_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    with pytest.raises(ServiceUnavailableError, match="GITHUB_PAT"):
        await list_repo_tags("Inferaim", "dealership_new")


@pytest.mark.asyncio
async def test_list_repo_tags_empty_on_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    inner = MagicMock()
    inner.get = AsyncMock(
        side_effect=httpx.ConnectError("all connection attempts failed"),
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        out = await list_repo_tags("Inferaim", "dealership_new")

    assert out == []


@pytest.mark.asyncio
async def test_list_repo_tags_returns_sorted_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    page1 = [{"name": "v1.2.0"}, {"name": "v1.0.0"}]

    inner = MagicMock()
    inner.get = AsyncMock(
        side_effect=[
            MagicMock(status_code=200, json=MagicMock(return_value=page1)),
            MagicMock(status_code=200, json=MagicMock(return_value=[])),
        ]
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        out = await list_repo_tags("Inferaim", "dealership_new")

    assert out == ["v1.0.0", "v1.2.0"]


# ---------------------------------------------------------------------------
# check_repo_tag_exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_repo_tag_exists_returns_true_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    inner = MagicMock()
    inner.get = AsyncMock(return_value=MagicMock(status_code=200))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        result = await check_repo_tag_exists("Inferaim", "dealership_new", "v1.0.0")

    assert result is True
    url = inner.get.await_args.args[0]
    assert "/git/ref/tags/v1.0.0" in url


@pytest.mark.asyncio
async def test_check_repo_tag_exists_returns_false_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    inner = MagicMock()
    inner.get = AsyncMock(return_value=MagicMock(status_code=404))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        result = await check_repo_tag_exists("Inferaim", "dealership_new", "no-such-tag")

    assert result is False


@pytest.mark.asyncio
async def test_check_repo_tag_exists_returns_true_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-open: assume the tag exists on transient network errors."""
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    inner = MagicMock()
    inner.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        result = await check_repo_tag_exists("Inferaim", "dealership_new", "v2.0.0")

    assert result is True


# ---------------------------------------------------------------------------
# dispatch_github_terminate_workflow
# ---------------------------------------------------------------------------

def _make_mock_http_client(status_code: int = 204) -> tuple:
    inner = MagicMock()
    inner.post = AsyncMock(return_value=MagicMock(status_code=status_code, text=""))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return inner, mock_cm


@pytest.mark.asyncio
async def test_dispatch_terminate_posts_workflow_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")
    monkeypatch.setenv("DEVOPS_BRANCH", "athena/run-name")

    inner, mock_cm = _make_mock_http_client()

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        await dispatch_github_terminate_workflow(
            "amberd-acme-ada",
            triggered_by="alex",
        )

    call = inner.post.await_args
    url = call.args[0] if call.args else call.kwargs.get("url", call.args[0])
    assert "/repos/amberd-ai/devops/" in url
    assert "ada-terminate.yaml" in url
    body = call.kwargs["json"]
    assert body["ref"] == "athena/run-name"
    assert body["inputs"]["slack_user"] == "alex"
    assert body["inputs"]["subdomain"] == "amberd-acme-ada"


@pytest.mark.asyncio
async def test_dispatch_terminate_optional_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    inner, mock_cm = _make_mock_http_client()

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        await dispatch_github_terminate_workflow(
            "amberd-x",
            correlation_id="my-uuid-123",
            triggered_by="alex",
        )

    inputs = inner.post.await_args.kwargs["json"]["inputs"]
    assert inputs["subdomain"] == "amberd-x"
    assert inputs["deployment_id"] == "my-uuid-123"


@pytest.mark.asyncio
async def test_dispatch_terminate_raises_when_pat_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    with pytest.raises(ServiceUnavailableError, match="GITHUB_PAT"):
        await dispatch_github_terminate_workflow("amberd-x")


@pytest.mark.asyncio
async def test_dispatch_terminate_raises_on_non_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    _inner, mock_cm = _make_mock_http_client(status_code=422)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        with pytest.raises(BadRequestError):
            await dispatch_github_terminate_workflow("amberd-x")


# ---------------------------------------------------------------------------
# dispatch_github_workflow (deploy)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_deploy_sends_correct_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")
    monkeypatch.setenv("DEVOPS_BRANCH", "main")

    inner, mock_cm = _make_mock_http_client()

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        await dispatch_github_workflow(
            correlation_id="corr-uuid",
            branch="feature/x",
            customer_name="Acme",
            subdomain="amberd-acme-ada",
            domain="amberd.ai",
            env_instance="ADA",
            tier=1,
            triggered_by="bob",
        )

    body = inner.post.await_args.kwargs["json"]
    assert body["ref"] == "main"
    inputs = body["inputs"]
    assert inputs["slack_user"] == "bob"
    assert inputs["customer_name"] == "acme"
    assert inputs["env_instance"] == "ada"
    assert inputs["provider"] == "tier1"
    assert inputs["branch"] == "feature/x"
    assert inputs["deployment_id"] == "corr-uuid"


# ---------------------------------------------------------------------------
# dispatch_github_update_workflow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_update_sends_correct_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")
    monkeypatch.setenv("DEVOPS_BRANCH", "athena/run-name")

    inner, mock_cm = _make_mock_http_client()

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        await dispatch_github_update_workflow(
            correlation_id="corr-uuid",
            branch="main",
            subdomain="amberd-acme-ada",
            triggered_by="charlie",
        )

    body = inner.post.await_args.kwargs["json"]
    assert body["ref"] == "athena/run-name"
    assert body["inputs"]["slack_user"] == "charlie"
    assert body["inputs"]["subdomain"] == "amberd-acme-ada"
    assert body["inputs"]["deployment_id"] == "corr-uuid"


@pytest.mark.asyncio
async def test_dispatch_move_tier_sends_tier_string_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify move-tier dispatch converts Athena's numeric tier into DevOps tier format.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture for environment setup.

    Returns:
        None: This test asserts the workflow dispatch payload.
    """
    monkeypatch.setenv("GITHUB_PAT", "test-token")
    monkeypatch.setenv("DEVOPS_BRANCH", "main")

    inner, mock_cm = _make_mock_http_client()

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        await dispatch_github_move_tier_workflow(
            correlation_id="corr-uuid",
            subdomain="amberd-acme-ada",
            tier=2,
            triggered_by="hayk",
        )

    body = inner.post.await_args.kwargs["json"]
    assert body["ref"] == "main"
    assert body["inputs"]["slack_user"] == "hayk"
    assert body["inputs"]["subdomain"] == "amberd-acme-ada"
    assert body["inputs"]["tier"] == "tier2"
    assert body["inputs"]["deployment_id"] == "corr-uuid"


@pytest.mark.asyncio
async def test_cancel_workflow_run_posts_cancel_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "test-token")

    inner = MagicMock()
    inner.post = AsyncMock(return_value=MagicMock(status_code=202, text=""))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("c2ai.clients.github.http_client", return_value=mock_cm):
        await cancel_workflow_run(12_345)

    inner.post.assert_awaited_once()
    url = inner.post.await_args.args[0]
    assert "/actions/runs/12345/cancel" in url
