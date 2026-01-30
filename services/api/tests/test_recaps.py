"""Tests for recaps helper functions."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.recaps import resolve_week_window, WeekWindow


# ---------------------------------------------------------------------------
# resolve_week_window tests
# ---------------------------------------------------------------------------


def test_resolve_week_window_default():
    """Default window is previous 7 days ending yesterday."""
    result = resolve_week_window(tz_name=None)

    assert isinstance(result, WeekWindow)
    # End date should be yesterday
    today = datetime.now(timezone.utc).date()
    assert result.end_date == today - timedelta(days=1)
    # Start date should be 6 days before end
    assert result.start_date == result.end_date - timedelta(days=6)
    # Timezone defaults to UTC
    assert result.timezone == "UTC"


def test_resolve_week_window_explicit_dates():
    """Explicit start and end dates are respected."""
    start = date(2025, 1, 1)
    end = date(2025, 1, 7)
    result = resolve_week_window(tz_name=None, start_date=start, end_date=end)

    assert result.start_date == start
    assert result.end_date == end


def test_resolve_week_window_inverted_dates():
    """Inverted dates are swapped automatically."""
    start = date(2025, 1, 15)  # Later
    end = date(2025, 1, 1)     # Earlier
    result = resolve_week_window(tz_name=None, start_date=start, end_date=end)

    # Should be swapped
    assert result.start_date == end
    assert result.end_date == start


def test_resolve_week_window_end_date_only():
    """End date only: start is 6 days earlier."""
    end = date(2025, 6, 15)
    result = resolve_week_window(tz_name=None, end_date=end)

    assert result.end_date == end
    assert result.start_date == date(2025, 6, 9)


def test_resolve_week_window_start_date_only():
    """Start date only: end is 6 days later."""
    start = date(2025, 6, 1)
    result = resolve_week_window(tz_name=None, start_date=start)

    assert result.start_date == start
    assert result.end_date == date(2025, 6, 7)


def test_resolve_week_window_with_timezone():
    """Valid timezone is respected."""
    result = resolve_week_window(
        tz_name="America/New_York",
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 7),
    )

    assert result.timezone == "America/New_York"
    # UTC times should reflect the timezone offset
    # June in NYC is UTC-4 (EDT)
    assert result.start_utc.tzinfo == timezone.utc


def test_resolve_week_window_invalid_timezone():
    """Invalid timezone falls back to UTC."""
    result = resolve_week_window(
        tz_name="Invalid/Timezone",
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 7),
    )

    assert result.timezone == "UTC"


def test_resolve_week_window_utc_conversion():
    """start_utc and end_utc are properly converted."""
    result = resolve_week_window(
        tz_name="UTC",
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 7),
    )

    # start_utc should be midnight on start_date
    assert result.start_utc == datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    # end_utc should be midnight on day AFTER end_date (exclusive)
    assert result.end_utc == datetime(2025, 6, 8, 0, 0, 0, tzinfo=timezone.utc)


def test_resolve_week_window_pacific_timezone():
    """Pacific timezone UTC conversion."""
    result = resolve_week_window(
        tz_name="America/Los_Angeles",
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 7),
    )

    # June in LA is UTC-7 (PDT)
    # Midnight June 1 PDT = 7 AM June 1 UTC
    assert result.start_utc.hour == 7
    assert result.start_utc.day == 1


def test_resolve_week_window_frozen_dataclass():
    """WeekWindow is frozen (immutable)."""
    result = resolve_week_window(tz_name=None)

    with pytest.raises(AttributeError):
        result.start_date = date(2020, 1, 1)
