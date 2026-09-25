"""Request ids, structured logs, and the Prometheus endpoint."""

import json
import logging

from c2ai.core.observability import CorrelationIdFilter, JsonFormatter, correlation_id


def test_request_id_is_echoed_or_generated(test_client):
    response = test_client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["x-request-id"] == "abc123"
    generated = test_client.get("/health").headers["x-request-id"]
    assert len(generated) == 16 and generated != "abc123"


def test_metrics_count_requests_by_route_template(test_client):
    test_client.get("/health")
    body = test_client.get("/metrics").text
    assert 'c2ai_http_requests_total{method="GET",route="/health",status="200"}' in body
    assert "c2ai_job_runs_total" in body  # declared even before any job ran


def test_metrics_token_is_enforced_when_configured(test_client, monkeypatch):
    monkeypatch.setenv("C2AI_METRICS_TOKEN", "scrape-me")
    assert test_client.get("/metrics").status_code == 401
    ok = test_client.get("/metrics", headers={"Authorization": "Bearer scrape-me"})
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("text/plain")


def test_log_lines_carry_the_correlation_id():
    record = logging.LogRecord("c2ai.test", logging.INFO, __file__, 1, "hello %s", ("x",), None)
    token = correlation_id.set("req-42")
    try:
        CorrelationIdFilter().filter(record)
    finally:
        correlation_id.reset(token)
    line = json.loads(JsonFormatter().format(record))
    assert (line["message"], line["correlation_id"], line["level"]) == ("hello x", "req-42", "INFO")


async def test_job_runs_are_counted_and_tagged(caplog):
    from datetime import timedelta

    from c2ai.core.observability import JOB_RUNS
    from c2ai.jobs import MemoryJobStore, Worker
    from c2ai.jobs.worker import HandlerSpec

    seen = []

    async def handler(_ctx):
        seen.append(correlation_id.get())
        return None

    store = MemoryJobStore()
    await store.enqueue("probe")
    before = JOB_RUNS.labels("probe", "succeeded")._value.get()
    worker = Worker(
        store,
        handlers={"probe": HandlerSpec("probe", handler, timedelta(minutes=1), timedelta(1))},
        schedules=[],
    )
    await worker.run_once()
    assert JOB_RUNS.labels("probe", "succeeded")._value.get() == before + 1
    assert seen[0].startswith("job:probe:")
