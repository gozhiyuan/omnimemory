"""Tests for RAG date parsing and entity normalization functions."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.rag import (
    _day_bounds_local,
    _month_bounds_local,
    _extract_explicit_dates,
    _parse_date_range,
    _normalize_entity_list,
)


# ---------------------------------------------------------------------------
# _day_bounds_local tests
# ---------------------------------------------------------------------------


def test_day_bounds_local_basic():
    """Basic day bounds with no offset."""
    day = date(2025, 6, 15)
    offset = timedelta(0)
    start, end = _day_bounds_local(day, offset)

    expected_start = datetime(2025, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
    expected_end = datetime(2025, 6, 16, 0, 0, 0, tzinfo=timezone.utc)

    assert start == expected_start
    assert end == expected_end


def test_day_bounds_local_with_positive_offset():
    """Day bounds with positive timezone offset (e.g., UTC+8)."""
    day = date(2025, 6, 15)
    offset = timedelta(hours=8)  # UTC+8
    start, end = _day_bounds_local(day, offset)

    # Offset is added to UTC midnight: 00:00 UTC + 8h = 08:00 UTC
    expected_start = datetime(2025, 6, 15, 8, 0, 0, tzinfo=timezone.utc)
    expected_end = datetime(2025, 6, 16, 8, 0, 0, tzinfo=timezone.utc)

    assert start == expected_start
    assert end == expected_end


def test_day_bounds_local_with_negative_offset():
    """Day bounds with negative timezone offset (e.g., UTC-5)."""
    day = date(2025, 6, 15)
    offset = timedelta(hours=-5)  # UTC-5
    start, end = _day_bounds_local(day, offset)

    # Offset is added to UTC midnight: 00:00 UTC + (-5h) = 19:00 UTC previous day
    expected_start = datetime(2025, 6, 14, 19, 0, 0, tzinfo=timezone.utc)
    expected_end = datetime(2025, 6, 15, 19, 0, 0, tzinfo=timezone.utc)

    assert start == expected_start
    assert end == expected_end


def test_day_bounds_local_year_boundary():
    """Day bounds at year boundary."""
    day = date(2025, 12, 31)
    offset = timedelta(0)
    start, end = _day_bounds_local(day, offset)

    assert start.year == 2025
    assert end.year == 2026
    assert end.month == 1
    assert end.day == 1


# ---------------------------------------------------------------------------
# _month_bounds_local tests
# ---------------------------------------------------------------------------


def test_month_bounds_local_regular_month():
    """Month bounds for a regular month."""
    start, end = _month_bounds_local(2025, 6, timedelta(0))

    assert start == datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2025, 7, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_month_bounds_local_december_rollover():
    """Month bounds for December (year rollover)."""
    start, end = _month_bounds_local(2025, 12, timedelta(0))

    assert start == datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_month_bounds_local_with_offset():
    """Month bounds with timezone offset."""
    start, end = _month_bounds_local(2025, 3, timedelta(hours=-5))

    assert start == datetime(2025, 2, 28, 19, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2025, 3, 31, 19, 0, 0, tzinfo=timezone.utc)


def test_month_bounds_local_january():
    """Month bounds for January."""
    start, end = _month_bounds_local(2025, 1, timedelta(0))

    assert start == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _extract_explicit_dates tests
# ---------------------------------------------------------------------------


def test_extract_explicit_dates_iso_format():
    """Extract dates in ISO format (YYYY-MM-DD)."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = _extract_explicit_dates("Meeting on 2025-06-20", now)

    assert date(2025, 6, 20) in dates


def test_extract_explicit_dates_iso_format_with_slashes():
    """Extract dates in ISO-like format with slashes."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = _extract_explicit_dates("Meeting on 2025/06/20", now)

    assert date(2025, 6, 20) in dates


def test_extract_explicit_dates_month_day_format():
    """Extract dates in 'Month DD' format."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = _extract_explicit_dates("Meeting on June 20", now)

    assert date(2025, 6, 20) in dates


def test_extract_explicit_dates_month_day_with_year():
    """Extract dates in 'Month DD, YYYY' format."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = _extract_explicit_dates("Meeting on December 25, 2024", now)

    assert date(2024, 12, 25) in dates


def test_extract_explicit_dates_mdy_format():
    """Extract dates in MM/DD format."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = _extract_explicit_dates("Meeting on 06/20", now)

    assert date(2025, 6, 20) in dates


def test_extract_explicit_dates_invalid_date():
    """Invalid dates should be skipped."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = _extract_explicit_dates("2025-02-30 is not valid", now)

    # Feb 30 doesn't exist, should result in empty list
    assert len(dates) == 0


def test_extract_explicit_dates_multiple_dates():
    """Extract multiple dates from query."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = _extract_explicit_dates("Between 2025-01-01 and 2025-01-15", now)

    assert date(2025, 1, 1) in dates
    assert date(2025, 1, 15) in dates


# ---------------------------------------------------------------------------
# _parse_date_range tests
# ---------------------------------------------------------------------------


def test_parse_date_range_today():
    """Parse 'today' keyword."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("What did I do today?", now, offset)

    assert result is not None
    start, end = result
    assert start.date() == date(2025, 6, 15)
    assert (end - start).days == 1


def test_parse_date_range_yesterday():
    """Parse 'yesterday' keyword."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("What happened yesterday?", now, offset)

    assert result is not None
    start, end = result
    assert start.date() == date(2025, 6, 14)


def test_parse_date_range_last_week():
    """Parse 'last week' keyword."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("Summary of last week", now, offset)

    assert result is not None
    start, end = result
    # Last 7 days
    assert (end - start).days == 8  # Includes today's end


def test_parse_date_range_this_week():
    """Parse 'this week' keyword."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)  # Sunday
    offset = timedelta(0)
    result = _parse_date_range("What happened this week?", now, offset)

    assert result is not None
    start, end = result
    assert start <= now <= end


def test_parse_date_range_last_month():
    """Parse 'last month' keyword."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("Photos from last month", now, offset)

    assert result is not None
    start, end = result
    assert start.month == 5
    assert end.month == 6


def test_parse_date_range_this_month():
    """Parse 'this month' keyword."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("Activities this month", now, offset)

    assert result is not None
    start, end = result
    assert start.month == 6
    assert end.month == 7


def test_parse_date_range_last_n_days():
    """Parse 'last N days' pattern."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("Show me the last 30 days", now, offset)

    assert result is not None
    start, end = result
    assert (end - start).days == 30


def test_parse_date_range_month_year():
    """Parse 'January 2024' format."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("Photos from January 2024", now, offset)

    assert result is not None
    start, end = result
    assert start.year == 2024
    assert start.month == 1
    assert end.month == 2


def test_parse_date_range_no_match():
    """Query with no date reference returns None."""
    now = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    offset = timedelta(0)
    result = _parse_date_range("Show me photos of cats", now, offset)

    assert result is None


def test_parse_date_range_with_timezone_offset():
    """Date range respects timezone offset."""
    now = datetime(2025, 6, 15, 2, 0, 0, tzinfo=timezone.utc)  # 2 AM UTC
    offset = timedelta(hours=8)  # UTC+8, so local is 10 AM
    result = _parse_date_range("What did I do today?", now, offset)

    assert result is not None
    start, end = result
    # With UTC+8 offset, "today" should still refer to June 15 local time


# ---------------------------------------------------------------------------
# _normalize_entity_list tests
# ---------------------------------------------------------------------------


def test_normalize_entity_list_basic():
    """Basic entity list normalization."""
    result = _normalize_entity_list(["Alice", "Bob", "Charlie"])
    assert result == ["Alice", "Bob", "Charlie"]


def test_normalize_entity_list_dedup():
    """Duplicate entities should be removed."""
    result = _normalize_entity_list(["Alice", "Bob", "Alice"])
    assert result == ["Alice", "Bob"]


def test_normalize_entity_list_filter_empty():
    """Empty strings should be filtered."""
    result = _normalize_entity_list(["Alice", "", "Bob", "   "])
    assert result == ["Alice", "Bob"]


def test_normalize_entity_list_filter_none():
    """None values should be filtered."""
    result = _normalize_entity_list(["Alice", None, "Bob"])
    assert result == ["Alice", "Bob"]


def test_normalize_entity_list_not_a_list():
    """Non-list input returns empty list."""
    assert _normalize_entity_list("not a list") == []
    assert _normalize_entity_list(42) == []
    assert _normalize_entity_list(None) == []


def test_normalize_entity_list_type_coercion():
    """Non-string values should be coerced to strings."""
    result = _normalize_entity_list(["Alice", 123, "Bob"])
    assert "123" in result


def test_normalize_entity_list_whitespace_trimming():
    """Whitespace should be trimmed from values."""
    result = _normalize_entity_list(["  Alice  ", "Bob\n"])
    assert result == ["Alice", "Bob"]
