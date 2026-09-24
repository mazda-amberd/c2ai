"""
GitHub client for Athena — workflow dispatch and Actions API.

Dispatch strategy
-----------------
Athena uses ``workflow_dispatch`` (not ``repository_dispatch``) so that the target
branch can be specified via the ``DEVOPS_BRANCH`` env var.  This lets us test
against a feature branch (e.g. ``athena/run-name``) without touching the devops
default branch.

Default: ``DEVOPS_BRANCH=main``.  For testing set ``DEVOPS_BRANCH=athena/run-name``.

Deploy/update ``branch`` inputs refer to the application source repo (default
``DEPLOY_SOURCE_REPO=dealership_new``), not devops; guards validate that repo.

Run-id correlation
------------------
``workflow_dispatch`` returns 204 with no run_id.  After dispatching, a background
task calls ``resolve_run_id`` which first tries name-based matching (requires the
``run-name`` field in the workflow YAML, present on the ``athena/run-name`` branch),
then falls back to greedy temporal matching.

Status cache
------------
``get_run_status_cached`` caches each run's status+jobs for ``RUN_STATUS_CACHE_TTL``
seconds so that many concurrent frontend pollers don't hammer the GH API.
Completed runs are not cached (their final state is always re-fetched then evicted).

Cancellation
------------
``cancel_workflow_run`` uses ``POST .../actions/runs/{id}/cancel``. The PAT needs
permission to cancel workflow runs (e.g. classic ``repo`` / ``actions:write``, or
fine-grained write on Actions for ``amberd-ai/devops``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from c2ai.core.exceptions import BadRequestError, ServiceUnavailableError

logger = logging.getLogger(__name__)

GITHUB_REPO_OWNER = os.getenv("GITHUB_REPO_OWNER", "amberd-ai")
GITHUB_REPO_NAME = os.getenv("GITHUB_REPO_NAME", "devops")
GITHUB_API_BASE = "https://api.github.com"

# Workflow file names in amberd-ai/devops
GITHUB_WORKFLOW_DEPLOY = "ada-deploy.yaml"
GITHUB_WORKFLOW_MOVE_TIER = "ada-move-to-tier.yaml"
GITHUB_WORKFLOW_UPDATE = "ada-update.yaml"
GITHUB_WORKFLOW_TERMINATE = "ada-terminate.yaml"

# Keep old event-type constants so existing imports don't break.
GITHUB_REPOSITORY_DISPATCH_EVENT = "ada-deploy"
GITHUB_REPOSITORY_DISPATCH_EVENT_UPDATE = "ada-update"
GITHUB_REPOSITORY_DISPATCH_EVENT_TERMINATE = "ada-terminate"

RUN_STATUS_CACHE_TTL = timedelta(seconds=20)
BRANCH_CACHE_TTL = timedelta(seconds=60)

_run_status_cache: dict[int, tuple[dict, datetime]] = {}
_branch_cache: dict[str, tuple[bool, datetime]] = {}

_GITHUB_PAT_MISSING_DETAIL = (
    "GitHub integration is not configured (GITHUB_PAT is unset or empty)."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _github_headers(pat: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _require_github_pat() -> str:
    pat = os.getenv("GITHUB_PAT", "")
    if not pat:
        raise ServiceUnavailableError(_GITHUB_PAT_MISSING_DETAIL)
    return pat


def get_devops_branch() -> str:
    """Return the devops branch Athena dispatches against (default: main)."""
    return os.getenv("DEVOPS_BRANCH", "main")


def get_deploy_source_repo() -> tuple[str, str]:
    """Return (owner, repo) for the application code repo whose branches deploy/update uses.

    This must match the repo the frontend branch picker lists (``DEPLOY_BRANCH_REPO``).
    """
    repo = os.getenv("DEPLOY_SOURCE_REPO", "dealership_new")
    return GITHUB_REPO_OWNER, repo


# ---------------------------------------------------------------------------
# Branch listing (unchanged)
# ---------------------------------------------------------------------------

async def list_repo_branches(owner: str, repo: str) -> list[str]:
    """Return sorted branch names for ``{owner}/{repo}``."""
    pat = _require_github_pat()
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches"
    branches: list[str] = []
    page = 1
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                response = await client.get(
                    url,
                    params={"per_page": 100, "page": page},
                    headers=_github_headers(pat),
                )
                if response.status_code != 200:
                    logger.error(
                        "GitHub branches fetch failed: status=%s body=%s",
                        response.status_code,
                        response.text,
                    )
                    break
                data = response.json()
                if not data:
                    break
                branches.extend(item["name"] for item in data)
                if len(data) < 100:
                    break
                page += 1
    except httpx.RequestError as exc:
        logger.warning(
            "GitHub branches unreachable (%s/%s): %s — returning empty list",
            owner,
            repo,
            exc,
        )
        return []
    return sorted(branches)


# ---------------------------------------------------------------------------
# Tag listing
# ---------------------------------------------------------------------------

async def list_repo_tags(owner: str, repo: str) -> list[str]:
    """Return sorted tag names for ``{owner}/{repo}``."""
    pat = _require_github_pat()
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/tags"
    tags: list[str] = []
    page = 1
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                response = await client.get(
                    url,
                    params={"per_page": 100, "page": page},
                    headers=_github_headers(pat),
                )
                if response.status_code != 200:
                    logger.error(
                        "GitHub tags fetch failed: status=%s body=%s",
                        response.status_code,
                        response.text,
                    )
                    break
                data = response.json()
                if not data:
                    break
                tags.extend(item["name"] for item in data)
                if len(data) < 100:
                    break
                page += 1
    except httpx.RequestError as exc:
        logger.warning(
            "GitHub tags unreachable (%s/%s): %s — returning empty list",
            owner,
            repo,
            exc,
        )
        return []
    return sorted(tags)


# ---------------------------------------------------------------------------
# Branch / tag existence guard (with short TTL cache)
# ---------------------------------------------------------------------------

async def check_repo_branch_exists(owner: str, repo: str, branch: str) -> bool:
    """
    Return True if ``branch`` exists on ``{owner}/{repo}``.

    Used for deploy/update ``branch`` inputs (application source), not for the
    devops workflow ref (that is ``DEVOPS_BRANCH`` / ``get_devops_branch()``).

    Result is cached for BRANCH_CACHE_TTL seconds per (owner, repo, branch).
    """
    cache_key = f"{owner}/{repo}@{branch}"
    now = datetime.now(tz=UTC)
    if cache_key in _branch_cache:
        exists, cached_at = _branch_cache[cache_key]
        if now - cached_at < BRANCH_CACHE_TTL:
            return exists

    pat = _require_github_pat()
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches/{branch}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_github_headers(pat))
        exists = resp.status_code == 200
    except httpx.RequestError:
        logger.warning(
            "Branch check failed for %s/%s@%r — assuming it exists", owner, repo, branch
        )
        exists = True  # don't block on network hiccup

    _branch_cache[cache_key] = (exists, now)
    return exists


async def check_repo_tag_exists(owner: str, repo: str, tag: str) -> bool:
    """
    Return True if ``tag`` exists on ``{owner}/{repo}``.

    Uses the git refs API endpoint; result is cached for BRANCH_CACHE_TTL seconds.
    """
    cache_key = f"{owner}/{repo}@tag:{tag}"
    now = datetime.now(tz=UTC)
    if cache_key in _branch_cache:
        exists, cached_at = _branch_cache[cache_key]
        if now - cached_at < BRANCH_CACHE_TTL:
            return exists

    pat = _require_github_pat()
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/tags/{tag}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_github_headers(pat))
        exists = resp.status_code == 200
    except httpx.RequestError:
        logger.warning(
            "Tag check failed for %s/%s@%r — assuming it exists", owner, repo, tag
        )
        exists = True  # don't block on network hiccup

    _branch_cache[cache_key] = (exists, now)
    return exists


# ---------------------------------------------------------------------------
# workflow_dispatch
# ---------------------------------------------------------------------------

async def _post_workflow_dispatch(workflow_file: str, inputs: dict[str, str]) -> None:
    """
    Trigger a workflow via ``workflow_dispatch`` against the configured devops branch.

    Raises:
        ServiceUnavailableError: If ``GITHUB_PAT`` is unset.
        BadRequestError: On non-2xx GitHub response.
    """
    pat = _require_github_pat()
    ref = get_devops_branch()
    url = (
        f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/actions/workflows/{workflow_file}/dispatches"
    )
    payload = {"ref": ref, "inputs": inputs}

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload, headers=_github_headers(pat))

    if response.status_code not in (200, 201, 204):
        logger.error(
            "GitHub workflow_dispatch failed: workflow=%s status=%s body=%s",
            workflow_file,
            response.status_code,
            response.text,
        )
        raise BadRequestError(
            f"GitHub Actions dispatch failed (HTTP {response.status_code}): "
            f"{response.text}"
        )

    logger.info(
        "GitHub workflow_dispatch succeeded workflow=%s ref=%s", workflow_file, ref
    )


# ---------------------------------------------------------------------------
# Dispatch functions (public API used by route handlers)
# ---------------------------------------------------------------------------

async def dispatch_github_workflow(
    correlation_id: str,
    branch: str,
    customer_name: str,
    subdomain: str,  # kept for API compatibility; not passed as input (no YAML input)
    domain: str,  # kept for API compat; workflow reads from vars.DOMAIN
    env_instance: str,
    tier: int,
    triggered_by: str,
    slack_user: str | None = None,
) -> None:
    """Dispatch ada-deploy via workflow_dispatch.

    ``slack_user`` is the Slack handle sent to the devops validate-user job,
    which authorizes tier2/3 deploys by Slack username substring.  If omitted,
    ``triggered_by`` (the Athena identifier) is used — which is fine for tier1
    but will fail tier2/3 unless the Athena identifier happens to contain the
    expected Slack handle.  Set ``slack_user`` from JWT metadata when available.
    """
    await _post_workflow_dispatch(
        GITHUB_WORKFLOW_DEPLOY,
        {
            "slack_user": slack_user or triggered_by,
            "env_instance": env_instance.lower(),
            "customer_name": customer_name.lower(),
            "provider": f"tier{tier}",
            "branch": branch,
            "deployment_id": correlation_id,
        },
    )
    logger.info(
        "ada-deploy dispatch done correlation_id=%s branch=%s", correlation_id, branch
    )


async def dispatch_github_update_workflow(
    correlation_id: str,
    branch: str,
    subdomain: str,
    triggered_by: str,
) -> None:
    """Dispatch ada-update via workflow_dispatch."""
    await _post_workflow_dispatch(
        GITHUB_WORKFLOW_UPDATE,
        {
            "slack_user": triggered_by,
            "subdomain": subdomain,
            "branch": branch,
            "deployment_id": correlation_id,
        },
    )
    logger.info(
        "ada-update dispatch done correlation_id=%s subdomain=%s",
        correlation_id,
        subdomain,
    )


async def dispatch_github_move_tier_workflow(
    correlation_id: str,
    subdomain: str,
    tier: int,
    triggered_by: str,
) -> None:
    """
    Dispatch the ada move-to-tier workflow via ``workflow_dispatch``.

    Args:
        correlation_id (str): Athena pipeline run correlation ID.
        subdomain (str): Target deployment subdomain.
        tier (int): Target numeric Athena tier index.
        triggered_by (str): Slack-compatible user identifier sent to DevOps.

    Returns:
        None: This function returns nothing after dispatch succeeds.

    Raises:
        ServiceUnavailableError: If ``GITHUB_PAT`` is unset.
        BadRequestError: If GitHub rejects the workflow dispatch request.
    """
    await _post_workflow_dispatch(
        GITHUB_WORKFLOW_MOVE_TIER,
        {
            "slack_user": triggered_by,
            "subdomain": subdomain,
            "tier": f"tier{tier}",
            "deployment_id": correlation_id,
        },
    )
    logger.info(
        "ada-move-to-tier dispatch done correlation_id=%s subdomain=%s tier=%s",
        correlation_id,
        subdomain,
        tier,
    )


async def dispatch_github_terminate_workflow(
    subdomain: str,
    *,
    correlation_id: str | None = None,
    triggered_by: str = "athena",
    # Legacy params kept for backward compat with old tests / callers
    deployment_id: str | None = None,
    callback_base_url: str | None = None,
) -> None:
    """Dispatch ada-terminate via workflow_dispatch."""
    inputs: dict[str, str] = {
        "slack_user": triggered_by,
        "subdomain": subdomain,
    }
    effective_id = correlation_id or deployment_id
    if effective_id:
        inputs["deployment_id"] = effective_id
    if callback_base_url:
        inputs["callback_base_url"] = callback_base_url

    await _post_workflow_dispatch(GITHUB_WORKFLOW_TERMINATE, inputs)
    logger.info("ada-terminate dispatch done subdomain=%s", subdomain)


# ---------------------------------------------------------------------------
# GitHub Actions API — run listing / fetching
# ---------------------------------------------------------------------------

async def list_workflow_runs(
    workflow_file: str,
    *,
    created_after: datetime | None = None,
    per_page: int = 20,
) -> list[dict[str, Any]]:
    """
    Return recent runs for a specific workflow file.

    Uses ``GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs``.
    """
    pat = _require_github_pat()
    url = (
        f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/actions/workflows/{workflow_file}/runs"
    )
    params: dict[str, Any] = {"per_page": per_page}
    if created_after:
        params["created"] = f">={created_after.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers=_github_headers(pat))
        if resp.status_code != 200:
            logger.warning(
                "list_workflow_runs failed: workflow=%s status=%s",
                workflow_file,
                resp.status_code,
            )
            return []
        return resp.json().get("workflow_runs", [])
    except httpx.RequestError as exc:
        logger.warning("list_workflow_runs request error: %s", exc)
        return []


async def cancel_workflow_run(run_id: int) -> None:
    """
    Request cancellation of a workflow run on amberd-ai/devops.

    GitHub returns 202 Accepted when the cancel request is accepted; 409 if the
    run is already in a completed state (treated as success for idempotency).
    """
    pat = _require_github_pat()
    url = (
        f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/actions/runs/{run_id}/cancel"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=_github_headers(pat))
    except httpx.RequestError as exc:
        logger.warning("cancel_workflow_run request error run_id=%s: %s", run_id, exc)
        raise BadRequestError(f"Could not reach GitHub to cancel run {run_id}.") from exc

    _run_status_cache.pop(run_id, None)

    if resp.status_code in (200, 202):
        logger.info("cancel_workflow_run accepted run_id=%s status=%s", run_id, resp.status_code)
        return
    if resp.status_code == 409:
        logger.info(
            "cancel_workflow_run: GitHub returned 409 (run already finished) run_id=%s",
            run_id,
        )
        return

    logger.error(
        "cancel_workflow_run failed run_id=%s status=%s body=%s",
        run_id,
        resp.status_code,
        resp.text,
    )
    raise BadRequestError(
        f"GitHub refused to cancel run {run_id} (HTTP {resp.status_code}): {resp.text}"
    )


async def get_workflow_run(run_id: int) -> dict[str, Any] | None:
    """Fetch a single run by run_id."""
    pat = _require_github_pat()
    url = (
        f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/actions/runs/{run_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_github_headers(pat))
        if resp.status_code == 200:
            return resp.json()
        logger.warning("get_workflow_run %s → %s", run_id, resp.status_code)
        return None
    except httpx.RequestError as exc:
        logger.warning("get_workflow_run request error: %s", exc)
        return None


async def get_workflow_run_jobs(run_id: int) -> list[dict[str, Any]]:
    """Fetch jobs (with embedded steps) for a run."""
    pat = _require_github_pat()
    url = (
        f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/actions/runs/{run_id}/jobs"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url, params={"filter": "latest"}, headers=_github_headers(pat)
            )
        if resp.status_code == 200:
            return resp.json().get("jobs", [])
        logger.warning("get_workflow_run_jobs %s → %s", run_id, resp.status_code)
        return []
    except httpx.RequestError as exc:
        logger.warning("get_workflow_run_jobs request error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Run-id resolution
# ---------------------------------------------------------------------------

def expected_run_name(
    workflow_file: str,
    subdomain: str,
    customer_name: str | None = None,
    env_instance: str | None = None,
) -> str:
    """
    Construct the expected ``run-name`` for name-based matching.

    For ada-deploy the run-name template on the athena/run-name branch is:
      ``ada-deploy | {customer_name}-{env_instance}``
    For update/terminate:
      ``ada-update | {subdomain}`` / ``ada-terminate | {subdomain}``
    """
    if workflow_file == GITHUB_WORKFLOW_DEPLOY and customer_name and env_instance:
        return f"ada-deploy | {customer_name.lower()}-{env_instance.lower()}"
    op = workflow_file.replace(".yaml", "")
    return f"{op} | {subdomain}"


# Keep the private alias so old call sites inside this module still work.
_expected_run_name = expected_run_name


async def resolve_run_id(
    workflow_file: str,
    subdomain: str,
    dispatched_at: datetime,
    *,
    customer_name: str | None = None,
    env_instance: str | None = None,
) -> int | None:
    """
    Try to find the GH run_id that corresponds to our dispatch.

    Strategy (in order):
    1. Name-based: look for a run whose name matches the expected run-name.
       Works when ``run-name`` is set in the workflow YAML (athena/run-name branch).
    2. Temporal: among runs created after ``dispatched_at - 10s``, take the
       one whose ``created_at`` is closest to ``dispatched_at``.
    """
    window_start = dispatched_at - timedelta(seconds=10)
    runs = await list_workflow_runs(
        workflow_file,
        created_after=window_start,
        per_page=30,
    )
    if not runs:
        return None

    # 1. Name-based match
    expected_name = _expected_run_name(
        workflow_file, subdomain, customer_name, env_instance
    )
    for run in runs:
        if run.get("name", "").strip() == expected_name:
            logger.info(
                "resolve_run_id: name match run_id=%s name=%r",
                run["id"],
                run["name"],
            )
            return run["id"]

    # 2. Temporal match — pick the run closest in time to our dispatch
    dispatched_ts = dispatched_at.timestamp()
    candidates = []
    for run in runs:
        try:
            created = datetime.fromisoformat(
                run["created_at"].replace("Z", "+00:00")
            ).timestamp()
        except (KeyError, ValueError):
            continue
        if created >= dispatched_ts - 10:
            candidates.append((abs(created - dispatched_ts), run["id"]))

    if candidates:
        candidates.sort()
        run_id = candidates[0][1]
        logger.info("resolve_run_id: temporal match run_id=%s", run_id)
        return run_id

    return None


# ---------------------------------------------------------------------------
# Status fetching with in-process cache
# ---------------------------------------------------------------------------

def _extract_active_job_and_step(
    jobs: list[dict],
) -> tuple[str | None, str | None]:
    """
    From a list of job objects return (active_job_name, current_step_name).

    Prefers jobs with status ``in_progress``; within that job looks for the
    in-progress or last-completed step.
    """
    for job in jobs:
        if job.get("status") == "in_progress":
            job_name = job.get("name")
            step_name: str | None = None
            for step in job.get("steps", []):
                if step.get("status") == "in_progress":
                    step_name = step.get("name")
                    break
                if step.get("status") == "completed":
                    step_name = step.get("name")
            return job_name, step_name
    return None, None


async def _fetch_run_status(run_id: int) -> dict[str, Any]:
    """Fetch run + jobs and return a normalised status dict."""
    run, jobs = await asyncio.gather(
        get_workflow_run(run_id),
        get_workflow_run_jobs(run_id),
    )
    if not run:
        return {"run_id": run_id, "gh_status": None, "gh_conclusion": None}

    active_job, current_step = _extract_active_job_and_step(jobs)
    return {
        "run_id": run_id,
        "gh_status": run.get("status"),
        "gh_conclusion": run.get("conclusion"),
        "run_url": run.get("html_url"),
        "active_job": active_job,
        "current_step": current_step,
        "started_at": run.get("run_started_at") or run.get("created_at"),
        "completed_at": run.get("updated_at") if run.get("status") == "completed" else None,
    }


async def get_run_status_cached(run_id: int) -> dict[str, Any]:
    """
    Return the status dict for ``run_id``, using an in-process TTL cache.

    Completed runs are not cached so their final state is always fresh.
    """
    now = datetime.now(tz=UTC)
    if run_id in _run_status_cache:
        data, fetched_at = _run_status_cache[run_id]
        if now - fetched_at < RUN_STATUS_CACHE_TTL:
            return data

    data = await _fetch_run_status(run_id)
    if data.get("gh_status") != "completed":
        _run_status_cache[run_id] = (data, now)
    else:
        _run_status_cache.pop(run_id, None)
    return data
