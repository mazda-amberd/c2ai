"""Backend rendering for troubleshooting metric charts shown in the UI."""

from __future__ import annotations

import base64
import math

from c2ai.schemas.troubleshooting import TroubleshootingMetric

_WIDTH = 600
_HEIGHT = 145
_LEFT = 10
_RIGHT = _WIDTH - 10
_TOP = 8
_BOTTOM = 137

def build_metric_plot_data_url(metric: TroubleshootingMetric) -> str:
    """Return a self-contained SVG chart encoded as an image data URL."""

    points = [point for point in metric.points if math.isfinite(point.value)]
    if not points:
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}">
<rect width="100%" height="100%" fill="transparent"/>
<rect x="10" y="8" width="580" height="129" rx="8" fill="none" stroke="#263340" stroke-dasharray="5 5"/>
<text x="300" y="78" text-anchor="middle" fill="#788494" font-family="Arial,sans-serif" font-size="13">No metric data available</text>
</svg>"""
        return _svg_data_url(svg)

    values = [point.value for point in points]
    minimum = min(values)
    maximum = max(values)
    value_range = maximum - minimum
    if value_range == 0:
        value_range = max(abs(maximum) * 0.1, 1.0)
        minimum -= value_range / 2

    coordinates: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        x = (
            _WIDTH / 2
            if len(points) == 1
            else _LEFT + index * (_RIGHT - _LEFT) / (len(points) - 1)
        )
        y = _BOTTOM - (point.value - minimum) * (_BOTTOM - _TOP) / value_range
        coordinates.append((x, y))

    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates)
    area = " ".join(f"L {x:.2f} {y:.2f}" for x, y in coordinates)
    grid = "".join(
        f'<line x1="{_LEFT}" x2="{_RIGHT}" y1="{y}" y2="{y}" stroke="#263340" stroke-width="1"/>'
        for y in (40, 73, 106)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}">
<defs><linearGradient id="area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#36dff1" stop-opacity="0.28"/><stop offset="100%" stop-color="#36dff1" stop-opacity="0"/></linearGradient></defs>
<rect width="100%" height="100%" fill="transparent"/>
{grid}
<path d="M {coordinates[0][0]:.2f} {_BOTTOM} {area} L {coordinates[-1][0]:.2f} {_BOTTOM} Z" fill="url(#area)"/>
<polyline points="{polyline}" fill="none" stroke="#36dff1" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
</svg>"""
    return _svg_data_url(svg)


def with_metric_plots(
    metrics: list[TroubleshootingMetric],
) -> list[TroubleshootingMetric]:
    """Attach backend-rendered plot images to the metric response objects."""

    return [
        metric.model_copy(update={"plot_data_url": build_metric_plot_data_url(metric)})
        for metric in metrics
    ]


def _svg_data_url(svg: str) -> str:
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
