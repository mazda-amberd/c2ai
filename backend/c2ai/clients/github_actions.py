"""
GitHub Actions API client for triggering workflows.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GitHubActionsClient:
    """Client for triggering GitHub Actions workflows."""

    def __init__(
        self,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        github_token: str | None = None,
        api_base_url: str = "https://api.github.com",
    ):
        self.repo_owner = repo_owner or os.getenv("GITHUB_REPO_OWNER")
        self.repo_name = repo_name or os.getenv("GITHUB_REPO_NAME")
        self.github_token = (
            github_token or os.getenv("GITHUB_PAT") or os.getenv("GITHUB_TOKEN")
        )
        self.api_base_url = api_base_url.rstrip("/")

        if not self.repo_owner:
            raise ValueError("GITHUB_REPO_OWNER environment variable is required")
        if not self.repo_name:
            raise ValueError("GITHUB_REPO_NAME environment variable is required")
        if not self.github_token:
            raise ValueError("GITHUB_PAT or GITHUB_TOKEN environment variable is required")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "Content-Type": "application/json",
        }

    async def _list_repository_refs(self, kind: str, *, limit: int) -> list[str]:
        """Read branch or tag names using bounded GitHub pagination."""
        url = f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}/{kind}"
        refs: list[str] = []
        per_page = min(limit, 100)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for page in range(1, (limit + per_page - 1) // per_page + 1):
                response = await client.get(
                    url, headers=self._headers(),
                    params={"per_page": per_page, "page": page},
                )
                response.raise_for_status()
                items = response.json()
                if not isinstance(items, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("name"), str)
                    for item in items
                ):
                    raise ValueError(f"Invalid GitHub {kind} response")
                refs.extend(item["name"] for item in items)
                if len(items) < per_page:
                    break
        return list(dict.fromkeys(refs))[:limit]

    async def list_repository_tags(self, *, limit: int = 200) -> list[str]:
        """Read tag names using this connection."""
        return await self._list_repository_refs("tags", limit=limit)

    async def list_repository_branches(self, *, limit: int = 200) -> list[str]:
        """Read branch names using this connection."""
        return await self._list_repository_refs("branches", limit=limit)

    async def trigger_workflow(
        self,
        workflow_id: str,
        ref: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Trigger a GitHub Actions workflow_dispatch event.

        Args:
            workflow_id: Workflow filename or workflow ID, e.g. deploy-instance.yml
            ref: Git ref to run the workflow on, e.g. main
            inputs: Workflow inputs

        Returns:
            Dictionary describing the dispatched workflow request
        """
        # GitHub's workflow dispatch endpoint accepts a numeric workflow ID or
        # the workflow filename, not the repository-relative
        # ``.github/workflows/...`` path stored by Athena's registration form.
        normalized_workflow_id = PurePosixPath(workflow_id).name
        if not normalized_workflow_id:
            raise ValueError("A workflow filename or workflow ID is required")
        url = (
            f"{self.api_base_url}/repos/"
            f"{self.repo_owner}/{self.repo_name}/actions/workflows/"
            f"{normalized_workflow_id}/dispatches"
        )

        payload = {
            "ref": ref,
            "inputs": {key: str(value) for key, value in inputs.items()},
            # Current GitHub.com API versions can return the created run directly.
            # A 204 response is still supported for older GitHub Enterprise servers.
            "return_run_details": True,
        }
        dispatched_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Triggering GitHub Actions workflow: workflow_id=%s repo=%s/%s ref=%s",
            normalized_workflow_id,
            self.repo_owner,
            self.repo_name,
            ref,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=self._headers())

            if response.status_code >= 400:
                logger.error(
                    "GitHub workflow dispatch failed: status=%s body=%s",
                    response.status_code,
                    response.text[:500],
                )
                response.raise_for_status()

        reference: dict[str, Any] = {
            "trigger_method": "workflow_dispatch",
            "workflow_id": normalized_workflow_id,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "ref": ref,
            "inputs": payload["inputs"],
            "api_base_url": self.api_base_url,
            "dispatched_at": dispatched_at,
        }
        if response.status_code == 200:
            response_body = response.json()
            if response_body.get("workflow_run_id") is not None:
                reference["run_id"] = int(response_body["workflow_run_id"])
            for field in ("run_url", "html_url"):
                if response_body.get(field):
                    reference[field] = response_body[field]
        return reference

    async def trigger_repository_dispatch(
        self,
        event_type: str,
        client_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Trigger a repository_dispatch event for a registered application."""

        url = (
            f"{self.api_base_url}/repos/"
            f"{self.repo_owner}/{self.repo_name}/dispatches"
        )
        payload = {"event_type": event_type, "client_payload": client_payload}
        dispatched_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Triggering GitHub repository dispatch: event_type=%s repo=%s/%s",
            event_type,
            self.repo_owner,
            self.repo_name,
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=self._headers())
            if response.status_code >= 400:
                logger.error(
                    "GitHub repository dispatch failed: status=%s body=%s",
                    response.status_code,
                    response.text[:500],
                )
                response.raise_for_status()

        return {
            "trigger_method": "repository_dispatch",
            "event_type": event_type,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "api_base_url": self.api_base_url,
            "dispatched_at": dispatched_at,
        }

    async def get_workflow_run(self, run_id: int) -> dict[str, Any]:
        """Return one workflow run from this client's repository."""

        url = (
            f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}"
            f"/actions/runs/{run_id}"
        )
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def get_workflow_run_jobs(self, run_id: int) -> list[dict[str, Any]]:
        """Return all jobs and their embedded steps for one workflow run."""

        url = (
            f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}"
            f"/actions/runs/{run_id}/jobs"
        )
        jobs: list[dict[str, Any]] = []
        page = 1
        async with httpx.AsyncClient(timeout=20.0) as client:
            while True:
                response = await client.get(
                    url,
                    params={"filter": "latest", "per_page": 100, "page": page},
                    headers=self._headers(),
                )
                response.raise_for_status()
                page_jobs = response.json().get("jobs", [])
                jobs.extend(page_jobs)
                if len(page_jobs) < 100:
                    break
                page += 1
        return jobs

    async def list_workflow_runs(
        self,
        workflow_id: str,
        *,
        trigger_method: str | None = None,
        ref: str | None = None,
        dispatched_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent candidate runs for a dispatched workflow."""

        normalized_workflow_id = PurePosixPath(workflow_id).name
        url = (
            f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}"
            f"/actions/workflows/{normalized_workflow_id}/runs"
        )
        params: dict[str, Any] = {"per_page": 50}
        if trigger_method:
            params["event"] = trigger_method
        if ref and trigger_method == "workflow_dispatch":
            params["branch"] = ref
        if dispatched_at:
            params["created"] = (
                f">={(dispatched_at - timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params=params, headers=self._headers())
        response.raise_for_status()
        return response.json().get("workflow_runs", [])

    async def resolve_workflow_run_id(
        self,
        *,
        workflow_id: str,
        trigger_method: str | None,
        ref: str | None,
        dispatched_at: datetime | None,
        instance_name: str,
        deployment_id: str,
    ) -> int | None:
        """Correlate legacy/repository dispatches that did not return a run ID."""

        runs = await self.list_workflow_runs(
            workflow_id,
            trigger_method=trigger_method,
            ref=ref,
            dispatched_at=dispatched_at,
        )
        if not runs:
            return None

        identifiers = (instance_name.lower(), deployment_id.lower())
        for run in runs:
            searchable = " ".join(
                str(run.get(field, ""))
                for field in ("name", "display_title", "run_name")
            ).lower()
            if any(identifier and identifier in searchable for identifier in identifiers):
                return int(run["id"])

        if dispatched_at:
            not_before = dispatched_at - timedelta(seconds=30)
            temporal_matches: list[tuple[datetime, dict[str, Any]]] = []
            for run in runs:
                try:
                    created_at = datetime.fromisoformat(
                        str(run.get("created_at", "")).replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                if created_at >= not_before:
                    temporal_matches.append((created_at, run))
            if temporal_matches:
                temporal_matches.sort(key=lambda item: item[0])
                return int(temporal_matches[0][1]["id"])
        return None

    async def get_workflow_progress(
        self,
        reference: dict[str, Any],
        *,
        instance_name: str,
        deployment_id: str,
    ) -> dict[str, Any] | None:
        """Resolve and normalize a run with its job/category and step hierarchy."""

        run_id = reference.get("run_id")
        if run_id is None:
            dispatched_at: datetime | None = None
            if reference.get("dispatched_at"):
                try:
                    dispatched_at = datetime.fromisoformat(
                        str(reference["dispatched_at"]).replace("Z", "+00:00")
                    )
                except ValueError:
                    dispatched_at = None
            workflow_id = reference.get("workflow_id")
            if not workflow_id:
                return None
            run_id = await self.resolve_workflow_run_id(
                workflow_id=str(workflow_id),
                trigger_method=reference.get("trigger_method"),
                ref=reference.get("ref"),
                dispatched_at=dispatched_at,
                instance_name=instance_name,
                deployment_id=deployment_id,
            )
        if run_id is None:
            return None

        run, jobs = await asyncio.gather(
            self.get_workflow_run(int(run_id)),
            self.get_workflow_run_jobs(int(run_id)),
        )
        return {
            "run_id": int(run_id),
            "run_number": run.get("run_number"),
            "name": run.get("name"),
            "display_title": run.get("display_title"),
            "status": run.get("status", "queued"),
            "conclusion": run.get("conclusion"),
            "html_url": run.get("html_url") or reference.get("html_url"),
            "event": run.get("event"),
            "head_branch": run.get("head_branch"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "jobs": [
                {
                    "id": int(job["id"]),
                    "name": job.get("name", "Unnamed job"),
                    "status": job.get("status", "queued"),
                    "conclusion": job.get("conclusion"),
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                    "html_url": job.get("html_url"),
                    "steps": [
                        {
                            "number": int(step.get("number", index + 1)),
                            "name": step.get("name", f"Step {index + 1}"),
                            "status": step.get("status", "queued"),
                            "conclusion": step.get("conclusion"),
                            "started_at": step.get("started_at"),
                            "completed_at": step.get("completed_at"),
                        }
                        for index, step in enumerate(job.get("steps") or [])
                    ],
                }
                for job in jobs
            ],
        }
