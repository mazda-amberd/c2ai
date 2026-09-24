"""Unit tests for metrics time-range resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from c2ai.constants.time_ranges import (
    DEFAULT_RANGE,
    RANGE_PRESETS,
    InvalidWindow,
    resolve_window,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("preset", sorted(RANGE_PRESETS))
def test_every_preset_resolves(preset):
    """Each advertised preset produces a window of exactly its length."""
    window = resolve_window(preset, now=NOW)
    assert window.end == NOW
    assert window.end - window.start == RANGE_PRESETS[preset][0]
    assert window.preset == preset


def test_default_range_when_nothing_requested():
    window = resolve_window(now=NOW)
    assert window.preset == DEFAULT_RANGE


def test_custom_window():
    start, end = NOW - timedelta(hours=3), NOW
    window = resolve_window(start=start, end=end)
    assert (window.start, window.end, window.preset) == (start, end, None)


def test_naive_datetimes_treated_as_utc():
    """A naive from/to pair must not raise on comparison."""
    window = resolve_window(start=datetime(2026, 8, 5, 9, 0), end=datetime(2026, 8, 5, 12, 0))
    assert window.end - window.start == timedelta(hours=3)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"range_key": "1h", "start": NOW, "end": NOW}, "not both"),
        ({"start": NOW}, "together"),
        ({"end": NOW}, "together"),
        ({"start": NOW, "end": NOW - timedelta(hours=1)}, "later than"),
        ({"start": NOW, "end": NOW}, "later than"),
        ({"range_key": "42y"}, "Unknown range"),
        ({"start": NOW - timedelta(days=400), "end": NOW}, "maximum"),
        ({"start": NOW - timedelta(seconds=5), "end": NOW}, "at least"),
    ],
)
def test_invalid_windows_rejected(kwargs, message):
    with pytest.raises(InvalidWindow) as exc:
        resolve_window(**kwargs)
    assert message in str(exc.value)



def test_preset_list_matches_the_prd_timeline_dropdown():
    """Pinned to the PRD list so an edit cannot silently drift from the UI."""
    assert list(RANGE_PRESETS) == [
        "1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "24h", "2d"
    ]
    assert DEFAULT_RANGE == "6h"


def test_thirty_seconds_is_not_offered():
    """Dropped on review: a 30s window cannot hold two 60s-scrape samples."""
    assert "30s" not in RANGE_PRESETS
    with pytest.raises(InvalidWindow):
        resolve_window("30s", now=NOW)


def test_no_step_is_finer_than_the_scrape_interval():
    """A step below 60s would interpolate rather than reflect real samples."""
    assert all(step >= 60 for _, step in RANGE_PRESETS.values())


def test_custom_window_is_capped_at_two_days():
    with pytest.raises(InvalidWindow, match="2-day maximum"):
        resolve_window(None, NOW - timedelta(days=3), NOW, now=NOW)
