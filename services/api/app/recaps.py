"""Helpers for weekly recap scheduling and date windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class WeekWindow:
    start_date: date
    end_date: date
    start_utc: datetime
    end_utc: datetime
    timezone: str


def resolve_week_window(
    *,
    tz_name: Optional[str],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> WeekWindow:
    resolved_tz = "UTC"
    tzinfo = timezone.utc
    if tz_name:
        try:
            tzinfo = ZoneInfo(tz_name)
            resolved_tz = tz_name
        except Exception:
            tzinfo = timezone.utc
            resolved_tz = "UTC"

    local_today = datetime.now(tzinfo).date()
    if start_date and end_date:
        window_start = start_date
        window_end = end_date
    elif end_date:
        window_end = end_date
        window_start = end_date - timedelta(days=6)
    elif start_date:
        window_start = start_date
        window_end = start_date + timedelta(days=6)
    else:
        window_end = local_today - timedelta(days=1)
        window_start = window_end - timedelta(days=6)

    if window_start > window_end:
        window_start, window_end = window_end, window_start

    start_local = datetime.combine(window_start, time.min, tzinfo=tzinfo)
    end_local = datetime.combine(window_end + timedelta(days=1), time.min, tzinfo=tzinfo)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    return WeekWindow(
        start_date=window_start,
        end_date=window_end,
        start_utc=start_utc,
        end_utc=end_utc,
        timezone=resolved_tz,
    )
