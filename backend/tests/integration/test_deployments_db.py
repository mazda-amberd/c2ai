"""The unified deployment model against PostgreSQL, with GitHub faked.

ADA operations through /api/deploy* create registered deployment instances
and operation-log rows; status comes from the log; GitHub's run conclusion
settles operations; the database allows one open operation per subdomain.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.db.session import get_db_session
from c2ai.jobs import PostgresJobStore, get_job_store
from c2ai.jobs.handlers.deployments import track_open_operations
from tests.integration.conftest import _SERVER_URL

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

SUBDOMAIN = "amberd-acme-ada"
DEPLOY_BODY = {
    "branch": "main",
    "subdomain": SUBDOMAIN,
    "customer_name": "acme",
    "domain": "amberd.ai",
    "env_instance": "ada",
    "tier": 1,
}


class FakeGitHub:
    """Enough of the GitHub REST API for dispatch, run status and cancel."""

    def __init__(self):
        self.refs = {"main", "release-2"}
        self.dispatches: list[tuple[str, dict]] = []
        # repository_dispatch events: (event_type, client_payload)
        self.events: list[tuple[str, dict]] = []
        # Authorization header of every dispatch, in order
        self.dispatch_auth: list[str] = []
        self.runs: dict[int, dict] = {}
        self.cancelled: list[int] = []
        self.reject_dispatches = False
        self.calls = 0
        self._ids = itertools.count(1000)

    def finish(self, run_id: int, conclusion: str = "success") -> None:
        self.runs[run_id].update(status="completed", conclusion=conclusion)

    def last_run_id(self) -> int:
        return max(self.runs)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        parts = request.url.path.strip("/").split("/")
        # repos/{owner}/{repo}/...
        rest = parts[3:]
        if not rest and request.method == "GET":  # connection validation
            return httpx.Response(200, json={"full_name": f"{parts[1]}/{parts[2]}"})
        if rest == ["dispatches"] and request.method == "POST":
            if self.reject_dispatches:
                return httpx.Response(422, json={"message": "Invalid payload"})
            body = json.loads(request.content)
            self.events.append((body["event_type"], body["client_payload"]))
            self.dispatch_auth.append(request.headers.get("Authorization", ""))
            return httpx.Response(204)
        if rest[:1] == ["branches"] and len(rest) == 2:
            return httpx.Response(200 if rest[1] in self.refs else 404, json={})
        if rest[:3] == ["git", "ref", "tags"]:
            return httpx.Response(404, json={})
        if rest[:2] == ["actions", "workflows"] and rest[-1] == "dispatches":
            if self.reject_dispatches:
                return httpx.Response(422, json={"message": "Unexpected inputs"})
            body = json.loads(request.content)
            self.dispatches.append((rest[2], body["inputs"]))
            self.dispatch_auth.append(request.headers.get("Authorization", ""))
            run_id = next(self._ids)
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            self.runs[run_id] = {
                "id": run_id,
                "run_number": run_id - 999,
                "name": rest[2],
                "status": "in_progress",
                "conclusion": None,
                "html_url": f"https://github.test/runs/{run_id}",
                "created_at": now,
                "updated_at": now,
            }
            return httpx.Response(
                200, json={"workflow_run_id": run_id, "html_url": self.runs[run_id]["html_url"]}
            )
        if rest[:2] == ["actions", "runs"]:
            run_id = int(rest[2])
            if rest[3:] == ["jobs"]:
                return httpx.Response(200, json={"jobs": []})
            if rest[3:] == ["cancel"]:
                self.cancelled.append(run_id)
                self.finish(run_id, "cancelled")
                return httpx.Response(202, json={})
            return httpx.Response(200, json=self.runs[run_id])
        return httpx.Response(404, json={"message": f"unexpected {request.url.path}"})


@pytest.fixture
async def clean(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE pipeline_runs, deployment_instance_events, deployment_instances,"
                " application_instances CASCADE"
            )
        )
        await session.commit()


@pytest.fixture
def github(http_mock, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    fake = FakeGitHub()
    http_mock(fake)
    return fake


@pytest.fixture
def user():
    return {"identity": AthenaTokenUser(identifier="alice", metadata={"slack_username": "alice.s"})}


@pytest.fixture
def client(session_factory, clean, github, user):
    async def _session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_current_user_token] = lambda: user["identity"]
    app.dependency_overrides[get_job_store] = lambda: PostgresJobStore(session_factory)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user_token, None)
        app.dependency_overrides.pop(get_job_store, None)


async def _instance_rows(session_factory):
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT instance_name, tier, status, application_id::text FROM deployment_instances"
                " ORDER BY created_at"
            )
        )
        return [tuple(row) for row in rows]


def _active(client):
    return client.get("/api/pipeline/active").json()


async def _track(session_factory):
    """One deployments.track run: the only code that polls GitHub."""

    return await track_open_operations(session_factory)


async def test_ada_deploy_is_a_registered_deployment_with_legacy_inputs(client, github, session_factory):
    response = client.post("/api/deploy", json=DEPLOY_BODY)
    assert response.status_code == 201, response.text
    operation = response.json()
    assert operation["operation"] == "deploy"
    assert operation["subdomain"] == SUBDOMAIN
    assert operation["event_type"] == "ada-deploy.yaml"
    assert operation["run_id"] == github.last_run_id()  # returned by the dispatch, no polling

    workflow, inputs = github.dispatches[0]
    assert workflow == "ada-deploy.yaml"
    assert inputs["customer_name"] == "acme"
    assert inputs["env_instance"] == "ada"
    assert inputs["branch"] == "main"
    assert inputs["provider"] == "tier1"
    assert inputs["slack_user"] == "alice.s"
    assert set(inputs) == {"customer_name", "env_instance", "branch", "provider", "slack_user",
                           "deployment_id"}

    [(name, tier, state, application_id)] = await _instance_rows(session_factory)
    assert (name, tier, state) == (SUBDOMAIN, 1, "deploying")
    assert application_id == "ada00000-0000-4000-8000-000000000001"

    calls = github.calls
    [active] = _active(client)
    assert (active["id"], active["gh_status"]) == (operation["id"], "queued")
    assert github.calls == calls  # status reads never call GitHub
    await _track(session_factory)  # the tracker does
    assert github.calls > calls
    [active] = _active(client)
    assert active["gh_status"] == "in_progress"

    # The same subdomain cannot be deployed twice.
    assert client.post("/api/deploy", json=DEPLOY_BODY).status_code == 409

    github.finish(operation["run_id"])
    await _track(session_factory)
    assert _active(client) == []
    assert (await _instance_rows(session_factory))[0][2] == "running"
    history = client.get("/api/pipeline/history", params={"subdomain": SUBDOMAIN}).json()
    assert [h["ended_at"] is not None for h in history] == [True]


async def test_unknown_branch_is_rejected_before_anything_is_written(client, session_factory):
    response = client.post("/api/deploy", json={**DEPLOY_BODY, "branch": "nope"})
    assert response.status_code == 422
    assert await _instance_rows(session_factory) == []


async def test_failed_dispatch_is_undone_and_reported(client, github, session_factory):
    github.reject_dispatches = True
    response = client.post("/api/deploy", json=DEPLOY_BODY)
    assert response.status_code == 503
    [(_name, _tier, state, _app)] = await _instance_rows(session_factory)
    assert state == "cancelled"  # never deployed: the name is free again
    [operation] = client.get("/api/pipeline/history", params={"subdomain": SUBDOMAIN}).json()
    assert operation["ended_at"] is not None
    [shown] = _active(client)  # the failure stays visible on the tier page
    assert (shown["gh_conclusion"], shown["id"]) == ("failure", operation["id"])

    github.reject_dispatches = False
    assert client.post("/api/deploy", json=DEPLOY_BODY).status_code == 201


async def test_update_move_and_terminate_one_at_a_time(client, github, session_factory):
    deployed = client.post("/api/deploy", json=DEPLOY_BODY).json()
    github.finish(deployed["run_id"])
    await _track(session_factory)

    # Updating to the same branch is a redeploy, which ADA allows.
    update = client.post("/api/deploy/update", json=DEPLOY_BODY)
    assert update.status_code == 201, update.text
    assert update.json()["operation"] == "update"
    assert github.dispatches[-1] == (
        "ada-update.yaml",
        {
            "slack_user": "alice",
            "subdomain": SUBDOMAIN,
            "branch": "main",
            "deployment_id": github.dispatches[-1][1]["deployment_id"],
        },
    )
    # Anything else on the subdomain waits for the update.
    blocked = client.post("/api/deploy/terminate", json={"subdomain": SUBDOMAIN})
    assert blocked.status_code == 409

    github.finish(update.json()["run_id"])
    await _track(session_factory)
    move = client.post("/api/deploy/move-tier", json={"subdomain": SUBDOMAIN, "tier": 2})
    assert move.status_code == 201, move.text
    assert move.json()["operation"] == "migration"
    workflow, inputs = github.dispatches[-1]
    assert workflow == "ada-move-to-tier.yaml"
    assert (inputs["subdomain"], inputs["tier"], inputs["slack_user"]) == (SUBDOMAIN, "tier2", "alice.s")

    [active] = _active(client)
    assert (active["operation"], active["subdomain"], active["tier"]) == ("migration", SUBDOMAIN, 2)
    github.finish(move.json()["run_id"])
    await _track(session_factory)
    assert (await _instance_rows(session_factory))[0][1:3] == (2, "running")

    terminate = client.post("/api/deploy/terminate", json={"subdomain": SUBDOMAIN})
    assert terminate.status_code == 201
    github.finish(terminate.json()["run_id"])
    await _track(session_factory)
    assert (await _instance_rows(session_factory))[0][2] == "terminated"

    # A terminated name can be deployed again.
    again = client.post("/api/deploy", json=DEPLOY_BODY)
    assert again.status_code == 201, again.text
    assert [row[2] for row in await _instance_rows(session_factory)] == ["terminated", "deploying"]
    history = client.get("/api/pipeline/history", params={"subdomain": SUBDOMAIN}).json()
    assert [h["operation"] for h in history] == ["deploy", "terminate", "migration", "update", "deploy"]


async def test_failed_run_is_reported_then_retryable(client, github, session_factory):
    deployed = client.post("/api/deploy", json=DEPLOY_BODY).json()
    github.finish(deployed["run_id"], "failure")
    await _track(session_factory)
    [failed] = _active(client)  # failures stay visible for a while
    assert (failed["gh_status"], failed["gh_conclusion"]) == ("completed", "failure")
    assert (await _instance_rows(session_factory))[0][2] == "failed"
    assert client.post("/api/deploy/update", json=DEPLOY_BODY).status_code == 201


async def test_cancel_is_limited_to_the_requester(client, github, session_factory, user):
    deployed = client.post("/api/deploy", json=DEPLOY_BODY).json()

    user["identity"] = AthenaTokenUser(identifier="bob")
    forbidden = client.post("/api/pipeline/cancel", json={"pipeline_run_id": deployed["id"]})
    assert forbidden.status_code == 403

    user["identity"] = AthenaTokenUser(identifier="alice")
    cancelled = client.post("/api/pipeline/cancel", json={"pipeline_run_id": deployed["id"]})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["ended_at"] is not None
    assert github.cancelled == [deployed["run_id"]]
    assert (await _instance_rows(session_factory))[0][2] == "cancelled"
    assert client.post("/api/pipeline/cancel", json={"pipeline_run_id": deployed["id"]}).status_code == 409
    missing = client.post("/api/pipeline/cancel", json={"pipeline_run_id": "no-such-run"})
    assert missing.status_code == 404


async def _inventory(session_factory, nodename: str, *, age: timedelta = timedelta()):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO application_instances"
                " (tier_name, name, nodename, cpu, memory, gpu, status, updated_at)"
                " VALUES ('Tier 3', 'ada', :node, 0, 0, 0, 'Healthy',"
                " now() - make_interval(secs => :age))"
            ),
            {"node": nodename, "age": age.total_seconds()},
        )
        await session.commit()


async def test_cluster_only_instances_are_adopted(client, github, session_factory):
    await _inventory(session_factory, "amberd-legacy-ada")

    response = client.post("/api/deploy/terminate", json={"subdomain": "amberd-legacy-ada"})
    assert response.status_code == 201, response.text
    [(name, tier, state, _application)] = await _instance_rows(session_factory)
    assert (name, tier, state) == ("amberd-legacy-ada", 3, "terminating")
    assert github.dispatches[-1][1]["subdomain"] == "amberd-legacy-ada"

    # With a fresh inventory, a subdomain that is nowhere is refused.
    missing = client.post("/api/deploy/terminate", json={"subdomain": "amberd-ghost-ada"})
    assert missing.status_code == 422


async def test_reconciler_settles_and_abandons_unwatched_operations(client, github, session_factory):
    deployed = client.post("/api/deploy", json=DEPLOY_BODY).json()
    github.finish(deployed["run_id"])

    await _inventory(session_factory, "amberd-quiet-ada")
    quiet = client.post("/api/deploy/update", json={**DEPLOY_BODY, "subdomain": "amberd-quiet-ada"})
    assert quiet.status_code == 201
    # Pretend GitHub never produced a run for this dispatch 40 minutes ago.
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE pipeline_runs SET run_id = NULL, dispatched_at = now() - interval '40 minutes'"
                " WHERE id = :id"
            ),
            {"id": quiet.json()["id"]},
        )
        await session.execute(
            text(
                "UPDATE deployment_instances"
                " SET dispatch_reference = dispatch_reference - 'run_id'"
                " WHERE instance_name = 'amberd-quiet-ada'"
            )
        )
        await session.commit()
    # GitHub's run listing is not faked, so no run can be matched to it.

    summary = await _track(session_factory)
    assert summary == {"checked": 2, "settled": 1, "restored": 0, "abandoned": 1}
    async with session_factory() as session:
        rows = dict(
            (await session.execute(text("SELECT subdomain, conclusion FROM pipeline_runs"))).all()
        )
    assert rows == {SUBDOMAIN: "success", "amberd-quiet-ada": "abandoned"}
    states = {row[0]: row[2] for row in await _instance_rows(session_factory)}
    assert states == {SUBDOMAIN: "running", "amberd-quiet-ada": "failed"}
