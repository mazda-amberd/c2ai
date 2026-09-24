"""Time-range presets for the metrics API (Story 1.5 timeline filtering).

The list is the PRD's timeline dropdown. "Last 30 seconds" was dropped on review: the
dashboard refreshes once a minute and Prometheus scrapes once a minute, so a 30-second
window cannot contain two samples. Longer ranges (month, year, all time) were declined
for now — this is a troubleshooting tool, not a reporting one.

Step is floored at the 60-second scrape interval; anything finer would interpolate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

RANGE_PRESETS: dict[str, tuple[timedelta, int]] = {
    "1m": (timedelta(minutes=1), 60),
    "5m": (timedelta(minutes=5), 60),
    "15m": (timedelta(minutes=15), 60),
    "30m": (timedelta(minutes=30), 60),
    "1h": (timedelta(hours=1), 60),
    "3h": (timedelta(hours=3), 120),
    "6h": (timedelta(hours=6), 300),
    "12h": (timedelta(hours=12), 600),
    "24h": (timedelta(hours=24), 900),
    "2d": (timedelta(days=2), 1800),
}

DEFAULT_RANGE = "6h"
MAX_CUSTOM_WINDOW = timedelta(days=2)
MIN_CUSTOM_WINDOW = timedelta(minutes=1)
# Tolerated client/server clock drift before a window counts as "in the future".
_CLOCK_SKEW = timedelta(minutes=5)


class Window(NamedTuple):
    """A resolved query window; ``preset`` is None for custom from/to."""

    start: datetime
    end: datetime
    preset: str | None
    step_seconds: int


class InvalidWindow(ValueError):
    """Raised when the requested range or from/to pair cannot be resolved."""


def _step_for(duration: timedelta) -> int:
    """Step for a custom window: nearest preset step at or above its length."""
    for length, step in sorted(RANGE_PRESETS.values(), key=lambda item: item[0]):
        if duration <= length:
            return step
    return max(step for _, step in RANGE_PRESETS.values())


def resolve_window(
    range_key: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime | None = None,
) -> Window:
    """Resolve a preset key or an explicit from/to pair into a Window."""
    now = now or datetime.now(UTC)

    if start or end:
        if range_key:
            raise InvalidWindow("Pass either 'range' or 'from'/'to', not both.")
        if not (start and end):
            raise InvalidWindow("'from' and 'to' must be provided together.")
        start, end = _as_utc(start), _as_utc(end)
        if end <= start:
            raise InvalidWindow("'to' must be later than 'from'.")
        duration = end - start
        if duration > MAX_CUSTOM_WINDOW:
            raise InvalidWindow(f"Window exceeds the {MAX_CUSTOM_WINDOW.days}-day maximum.")
        if duration < MIN_CUSTOM_WINDOW:
            raise InvalidWindow("Window must be at least 1 minute.")
        if start > now + _CLOCK_SKEW:
            raise InvalidWindow("'from' must not be in the future.")
        return Window(start, end, None, _step_for(duration))

    key = range_key or DEFAULT_RANGE
    if key not in RANGE_PRESETS:
        raise InvalidWindow(
            f"Unknown range '{key}'. Supported: {', '.join(RANGE_PRESETS)}."
        )
    length, step = RANGE_PRESETS[key]
    return Window(now - length, now, key, step)


def _as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC so comparisons never raise."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

