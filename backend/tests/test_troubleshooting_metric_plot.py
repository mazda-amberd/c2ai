"""Tests for server-rendered troubleshooting metric charts."""

import base64
from datetime import datetime, timedelta, timezone

from c2ai.schemas.troubleshooting import (
    TroubleshootingMetric,
    TroubleshootingMetricPoint,
)
from c2ai.services.troubleshooting_metric_plot import (
    build_metric_plot_data_url,
    with_metric_plots,
)


def _decode(data_url: str) -> str:
    prefix, encoded = data_url.split(",", maxsplit=1)
    assert prefix == "data:image/svg+xml;base64"
    return base64.b64decode(encoded).decode("utf-8")


def test_build_metric_plot_data_url_renders_complete_time_series():
    start = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    metric = TroubleshootingMetric(
        name="request_latency_seconds",
        label="Request Latency Seconds",
        value=3,
        points=[
            TroubleshootingMetricPoint(
                timestamp=start + timedelta(minutes=index),
                value=float(index),
            )
            for index in range(4)
        ],
    )

    svg = _decode(build_metric_plot_data_url(metric))

    assert svg.startswith("<svg")
    assert "<polyline" in svg
    assert "4 points" not in svg
    assert "Aug 24" not in svg


def test_with_metric_plots_renders_backend_empty_state():
    metrics = with_metric_plots(
        [TroubleshootingMetric(name="queue_depth", label="Queue Depth")]
    )

    assert metrics[0].plot_data_url is not None
    assert "No metric data available" in _decode(metrics[0].plot_data_url)
