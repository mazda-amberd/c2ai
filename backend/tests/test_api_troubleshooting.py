"""API tests for grounded, batch troubleshooting reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from c2ai.api import troubleshooting as api
from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser
from c2ai.jobs import MemoryJobStore, Worker, get_job_notifier, get_job_store
from c2ai.schemas.troubleshooting import (
    TroubleshootingEvent,
    TroubleshootingMetric,
    TroubleshootingMetricQuery,
)
from c2ai.services import troubleshooting_report as reports
from c2ai.services.troubleshooting_metrics import (
    load_configured_metric_queries,
    unavailable_metrics,
)


@pytest.fixture(autouse=True)
def _default_troubleshooting_window():
    app.dependency_overrides[api.get_troubleshooting_window_hours] = lambda: 4
    yield
    app.dependency_overrides.pop(api.get_troubleshooting_window_hours, None)


@pytest.fixture(autouse=True)
def job_store():
    """Jobs go to an in-memory queue and run right after each request."""

    store = MemoryJobStore()

    async def run_queued_jobs():
        await Worker(store, schedules=[]).run_once()

    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_notifier] = lambda: run_queued_jobs
    yield store
    app.dependency_overrides.pop(get_job_store, None)
    app.dependency_overrides.pop(get_job_notifier, None)


def _event(
    index: int,
    message: str,
    *,
    source: str = "application",
    severity: str = "error",
    reason: str | None = None,
    event_type: str = "container-log",
    attributes: dict[str, str] | None = None,
) -> TroubleshootingEvent:
    return TroubleshootingEvent(
        id=f"event-{index}",
        timestamp=datetime(2026, 8, 19, 4, 0, tzinfo=UTC)
        + timedelta(microseconds=index),
        source=source,
        severity=severity,
        resource="worker-1",
        event_type=event_type,
        reason=reason,
        message=message,
        attributes=attributes or {"pod": "worker-1", "container": "worker"},
    )


class FakeDataProvider:
    def __init__(self, events: list[TroubleshootingEvent]):
        self.events = events
        self.call_args = None
        self.metric_call_args = None
        self.metric_queries_call_args = None

    async def collect_events(
        self,
        *,
        subdomain: str,
        deployment: str,
        tier: int | None,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[TroubleshootingEvent]:
        self.call_args = {
            "subdomain": subdomain,
            "deployment": deployment,
            "tier": tier,
            "start": start,
            "end": end,
            "limit": limit,
        }
        return self.events

    async def list_metric_queries(
        self,
        *,
        subdomain: str,
        deployment: str,
        tier: int | None,
        start: datetime,
        end: datetime,
    ) -> list[TroubleshootingMetricQuery]:
        self.metric_queries_call_args = {
            "subdomain": subdomain,
            "deployment": deployment,
            "tier": tier,
            "start": start,
            "end": end,
        }
        return load_configured_metric_queries(
            namespace=subdomain,
            deployment=deployment,
            tier=tier,
            start=start,
            end=end,
        )

    async def collect_metrics(
        self,
        *,
        queries: list[TroubleshootingMetricQuery],
        subdomain: str,
        start: datetime,
        end: datetime,
    ) -> list[TroubleshootingMetric]:
        self.metric_call_args = {
            "queries": queries,
            "subdomain": subdomain,
            "start": start,
            "end": end,
        }
        return [
            metric.model_copy(update={"value": float(index + 1)})
            for index, metric in enumerate(unavailable_metrics(queries))
        ]


class GroundedFakeLLM:
    def __init__(
        self,
        *,
        invalid: bool = False,
        return_all_evidence_ids: bool = False,
    ):
        self.invalid = invalid
        self.return_all_evidence_ids = return_all_evidence_ids
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        if self.invalid:
            return AIMessage(content="not valid json")
        content = messages[0].content
        metric_queries = json.loads(
            content.split("AVAILABLE_PROMETHEUS_QUERIES:\n", 1)[1].split(
                "\n\nEVIDENCE:", 1
            )[0]
        )
        available_query_ids = {query["id"] for query in metric_queries}
        preferred_metric_query_ids = [
            "cpu_usage",
            "restarts",
            "memory_usage",
            "network_in",
        ]
        evidence = json.loads(content.split("EVIDENCE:\n", 1)[1])
        ids = [event["id"] for event in evidence]
        relevant_ids = ids if self.return_all_evidence_ids else ids[:20]
        relevant_cluster_ids = [
            event["id"]
            for event in evidence
            if event["source"] == "kubernetes"
        ][:10]
        return AIMessage(
            content=json.dumps(
                {
                    "severity": "critical",
                    "summary": "The worker is repeatedly failing.",
                    "most_likely_root_cause": "A dependency connection failed.",
                    "issue_started_event_id": ids[0],
                    "most_critical_event_id": ids[-1],
                    "recommended_actions": [
                        "Check the dependency endpoint.",
                        "Restart the worker after correcting connectivity.",
                        "Verify the worker health checks.",
                        "Monitor the deployment after recovery.",
                    ],
                    "recommended_metric_query_ids": [
                        query_id
                        for query_id in preferred_metric_query_ids
                        if query_id in available_query_ids
                    ],
                    "relevant_event_ids": relevant_ids,
                    "relevant_cluster_event_ids": relevant_cluster_ids,
                }
            )
        )


_provider_patches: list = []


def _override_dependencies(data_provider: FakeDataProvider, llm_provider):
    # The /report endpoint resolves providers through FastAPI; the job worker
    # calls the same hooks directly.
    app.dependency_overrides[reports.troubleshooting_data_provider] = lambda: data_provider
    app.dependency_overrides[reports.troubleshooting_llm_provider] = lambda: llm_provider
    for name, value in (
        ("troubleshooting_data_provider", data_provider),
        ("troubleshooting_llm_provider", llm_provider),
    ):
        patcher = patch.object(reports, name, lambda value=value: value)
        patcher.start()
        _provider_patches.append(patcher)


def _clear_dependencies():
    app.dependency_overrides.pop(reports.troubleshooting_data_provider, None)
    app.dependency_overrides.pop(reports.troubleshooting_llm_provider, None)
    while _provider_patches:
        _provider_patches.pop().stop()


@pytest.fixture(autouse=True)
def _reset_providers():
    yield
    _clear_dependencies()


class TestTroubleshootingReport:
    def test_requires_authentication(self, test_client):
        response = test_client.post(
            "/report",
            json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
        )
        assert response.status_code == 401

    def test_returns_grounded_batch_report(self, deploy_auth_client, monkeypatch):
        fixed_now = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
        monkeypatch.setattr(reports, "_utcnow", lambda: fixed_now)
        events = [
            _event(
                1,
                "connection warning",
                severity="warning",
            ),
            _event(
                2,
                "dependency connection error",
                source="kubernetes",
                reason="ConnectionRefused",
                event_type="Warning",
            ),
        ]
        data_provider = FakeDataProvider(events)
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        try:
            response = deploy_auth_client.post(
                "/report",
                json={
                    "subdomain": "amberd-acme-ada",
                    "deployment": "my-app",
                    "tier": 1,
                },
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 200
        report = response.json()
        assert report["severity"] == "critical"
        assert report["summary"] == "The worker is repeatedly failing."
        assert report["most_likely_root_cause"] == "A dependency connection failed."
        assert report["analyzed_log_lines"] == 2
        assert report["window"]["hours"] == 4
        assert report["most_critical_event"]["message"] == "dependency connection error"
        assert report["most_critical_event"]["reason"] == "ConnectionRefused"
        assert report["most_critical_event"]["source"] == "kubernetes"
        assert [metric["name"] for metric in report["application_metrics"]] == [
            "cpu_usage",
            "restarts",
            "memory_usage",
            "network_in",
        ]
        assert all(
            metric["plot_data_url"].startswith("data:image/svg+xml;base64,")
            for metric in report["application_metrics"]
        )
        assert all(
            "points" not in metric for metric in report["application_metrics"]
        )
        assert data_provider.metric_call_args["subdomain"] == "amberd-acme-ada"
        assert data_provider.metric_queries_call_args["subdomain"] == (
            "amberd-acme-ada"
        )
        assert data_provider.metric_queries_call_args["deployment"] == "my-app"
        assert data_provider.metric_queries_call_args["tier"] == 1
        assert data_provider.metric_queries_call_args[
            "end"
        ] - data_provider.metric_queries_call_args["start"] == timedelta(hours=4)
        assert len(report["relevant_events"]) == 2
        assert data_provider.call_args is not None
        assert data_provider.call_args["end"] - data_provider.call_args["start"] == (
            timedelta(hours=4)
        )
        assert data_provider.call_args["limit"] == 100
        assert data_provider.call_args["subdomain"] == "amberd-acme-ada"
        assert data_provider.call_args["deployment"] == "my-app"
        assert "untrusted evidence" in llm.messages[0].content
        assert "AVAILABLE_PROMETHEUS_QUERIES" in llm.messages[0].content
        assert (
            "Grafana uses Prometheus as the metrics data source"
            in llm.messages[0].content
        )
        assert "estimated_gpu_utilization_by_application" in (
            llm.messages[0].content
        )
        assert 'namespace=~\\"amberd-acme-ada\\"' in llm.messages[0].content
        assert 'label_app=~\\"my-app\\"' in llm.messages[0].content

    def test_caps_and_deduplicates_evidence_at_100_lines(
        self,
        deploy_auth_client,
    ):
        events = [_event(index, f"error line {index}") for index in range(105)]
        events.append(events[-1])
        data_provider = FakeDataProvider(events)
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        try:
            response = deploy_auth_client.post(
                "/report",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 200
        assert response.json()["analyzed_log_lines"] == 100
        evidence = json.loads(llm.messages[0].content.split("EVIDENCE:\n", 1)[1])
        assert len(evidence) == 100
        assert len({event["id"] for event in evidence}) == 100

    def test_uses_database_configured_lookback_window(
        self,
        deploy_auth_client,
    ):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        app.dependency_overrides[api.get_troubleshooting_window_hours] = lambda: 12
        try:
            response = deploy_auth_client.post(
                "/report",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 200
        assert response.json()["window"]["hours"] == 12
        assert data_provider.call_args is not None
        assert data_provider.call_args["end"] - data_provider.call_args["start"] == (
            timedelta(hours=12)
        )
        assert data_provider.call_args["limit"] == 100

    def test_caps_oversized_model_evidence_selection(
        self,
        deploy_auth_client,
    ):
        events = [_event(index, f"error line {index}") for index in range(45)]
        data_provider = FakeDataProvider(events)
        llm = GroundedFakeLLM(return_all_evidence_ids=True)
        _override_dependencies(data_provider, lambda: llm)
        try:
            response = deploy_auth_client.post(
                "/report",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 200
        report = response.json()
        assert report["analyzed_log_lines"] == 45
        assert len(report["relevant_events"]) == 21
        assert [event["id"] for event in report["relevant_events"]] == [
            *(f"event-{index}" for index in range(20)),
            "event-44",
        ]

    def test_returns_ten_most_relevant_cluster_events(
        self,
        deploy_auth_client,
    ):
        events = [
            _event(
                index,
                f"cluster event {index}",
                source="kubernetes",
                reason="Unhealthy",
                event_type="Warning",
            )
            for index in range(14)
        ]
        data_provider = FakeDataProvider(events)
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        try:
            response = deploy_auth_client.post(
                "/report",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 200
        report = response.json()
        assert [event["id"] for event in report["cluster_events"]] == [
            f"event-{index}" for index in range(10)
        ]
        assert len(report["cluster_events"]) == 10

    def test_empty_logs_return_insufficient_data_without_calling_model(
        self,
        deploy_auth_client,
    ):
        data_provider = FakeDataProvider([])

        def should_not_initialize():
            raise AssertionError("LLM must remain lazy for an empty log window")

        _override_dependencies(data_provider, should_not_initialize)
        try:
            response = deploy_auth_client.post(
                "/report",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 200
        report = response.json()
        assert report["analyzed_log_lines"] == 0
        assert report["most_critical_event"] is None
        assert report["issue_started"] is None
        assert "not enough log evidence" in report["most_likely_root_cause"]

    def test_invalid_model_response_maps_to_503(self, deploy_auth_client):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM(invalid=True)
        _override_dependencies(data_provider, lambda: llm)
        try:
            response = deploy_auth_client.post(
                "/report",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 503
        assert response.json()["code"] == "TroubleshootingAnalysisUnavailable"

    def test_unconfigured_devops_provider_maps_to_503(self, deploy_auth_client):
        response = deploy_auth_client.post(
            "/report",
            json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "TroubleshootingDataUnavailable"

    def test_rejects_invalid_application_identity(self, deploy_auth_client):
        response = deploy_auth_client.post(
            "/report",
            json={"subdomain": "INVALID", "deployment": "bad name!"},
        )
        assert response.status_code == 422


class TestTroubleshootingJobs:
    def test_creates_job_and_returns_completed_batch_on_poll(
        self,
        deploy_auth_client,
    ):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        try:
            created = deploy_auth_client.post(
                "/jobs",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
            assert created.status_code == 202
            created_job = created.json()
            assert created_job["status"] == "gathering_data"
            assert created_job["report"] is None
            assert created_job["error"] is None

            polled = deploy_auth_client.get(
                f"/jobs/{created_job['job_id']}"
            )
        finally:
            _clear_dependencies()

        assert polled.status_code == 200
        completed_job = polled.json()
        assert completed_job["status"] == "completed"
        assert completed_job["report"]["summary"] == (
            "The worker is repeatedly failing."
        )
        assert completed_job["error"] is None

    def test_downloads_completed_report_as_backend_pdf(
        self,
        deploy_auth_client,
    ):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        try:
            created = deploy_auth_client.post(
                "/jobs",
                json={
                    "subdomain": "amberd-acme-ada",
                    "deployment": "my-app",
                    "tier": 2,
                    "status": "Healthy",
                    "version": "26.02.08",
                    "instance": "release",
                    "client": "acme",
                },
            )
            job_id = created.json()["job_id"]
            response = deploy_auth_client.get(
                f"/jobs/{job_id}/report.pdf"
            )
        finally:
            _clear_dependencies()

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["cache-control"] == "no-store"
        report_date = datetime.now().astimezone().strftime("%d-%m-%Y")
        assert response.headers["content-disposition"] == (
            'attachment; filename="C2AI-application-troubleshooting-report-'
            f'{report_date}.pdf"'
        )
        assert response.content.startswith(b"%PDF-")
        assert len(response.content) > 5_000

    def test_pdf_is_visible_only_to_completed_job_owner(self, deploy_auth_client):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        try:
            created = deploy_auth_client.post(
                "/jobs",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
            job_id = created.json()["job_id"]
            original_user_override = app.dependency_overrides[
                api.get_current_user_token
            ]
            app.dependency_overrides[api.get_current_user_token] = (
                lambda: AthenaTokenUser(
                    identifier="another-user",
                    service="athena",
                )
            )
            try:
                response = deploy_auth_client.get(
                    f"/jobs/{job_id}/report.pdf"
                )
            finally:
                app.dependency_overrides[api.get_current_user_token] = (
                    original_user_override
                )
        finally:
            _clear_dependencies()

        assert response.status_code == 404

    def test_job_exposes_provider_failure_on_poll(self, deploy_auth_client):
        created = deploy_auth_client.post(
            "/jobs",
            json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
        )
        assert created.status_code == 202

        polled = deploy_auth_client.get(
            f"/jobs/{created.json()['job_id']}"
        )
        assert polled.status_code == 200
        failed_job = polled.json()
        assert failed_job["status"] == "failed"
        assert failed_job["report"] is None
        assert failed_job["error"]["code"] == "TroubleshootingDataUnavailable"

    def test_job_exposes_invalid_model_response_on_poll(self, deploy_auth_client):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM(invalid=True)
        _override_dependencies(data_provider, lambda: llm)
        try:
            created = deploy_auth_client.post(
                "/jobs",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
            polled = deploy_auth_client.get(
                f"/jobs/{created.json()['job_id']}"
            )
        finally:
            _clear_dependencies()

        assert polled.status_code == 200
        failed_job = polled.json()
        assert failed_job["status"] == "failed"
        assert failed_job["report"] is None
        assert failed_job["error"]["code"] == "TroubleshootingAnalysisUnavailable"

    def test_job_is_visible_only_to_its_owner(self, deploy_auth_client):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM()
        _override_dependencies(data_provider, lambda: llm)
        try:
            created = deploy_auth_client.post(
                "/jobs",
                json={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
            )
            job_id = created.json()["job_id"]
            original_user_override = app.dependency_overrides[
                api.get_current_user_token
            ]
            app.dependency_overrides[api.get_current_user_token] = (
                lambda: AthenaTokenUser(
                    identifier="another-user",
                    service="athena",
                )
            )
            try:
                response = deploy_auth_client.get(f"/jobs/{job_id}")
            finally:
                app.dependency_overrides[api.get_current_user_token] = (
                    original_user_override
                )
        finally:
            _clear_dependencies()

        assert response.status_code == 404
        assert response.json()["code"] == "TroubleshootingJobNotFound"

    async def test_report_builder_emits_real_processing_phases(self):
        data_provider = FakeDataProvider([_event(1, "application error")])
        llm = GroundedFakeLLM()
        phases = []

        async def record(phase):
            phases.append(phase)

        report = await reports.build_troubleshooting_report(
            api.TroubleshootingReportRequest(
                subdomain="amberd-acme-ada",
                deployment="my-app",
            ),
            data_provider,
            lambda: llm,
            4,
            on_phase=record,
        )

        assert report.summary == "The worker is repeatedly failing."
        assert phases == ["analyzing_data", "generating_recommendations"]
