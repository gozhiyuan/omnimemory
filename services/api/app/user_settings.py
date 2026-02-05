"""User settings helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from .db.models import UserSettings


async def fetch_user_settings(session: AsyncSession, user_id: UUID) -> dict[str, Any]:
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    record = result.scalar_one_or_none()
    if not record or not isinstance(record.settings, dict):
        return {}
    return record.settings


def resolve_language_code(settings: Mapping[str, Any] | None) -> str:
    if not settings:
        return "en"
    profile = settings.get("profile") or {}
    code = profile.get("language")
    return "zh" if code == "zh" else "en"


def resolve_language_label(language_code: str) -> str:
    if language_code == "zh":
        return "Chinese (Simplified)"
    return "English"


def resolve_ocr_language_hints(
    base_hints: list[str] | None,
    language_code: str,
) -> list[str]:
    hints = [value for value in (base_hints or []) if value]
    if language_code == "zh":
        for tag in ("zh", "zh-Hans", "zh-CN"):
            if tag not in hints:
                hints.append(tag)
    return hints


def resolve_preferences(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    if not settings:
        return {}
    prefs = settings.get("preferences")
    return prefs if isinstance(prefs, dict) else {}


def resolve_timezone_name(settings: Mapping[str, Any] | None) -> Optional[str]:
    prefs = resolve_preferences(settings)
    tz_name = prefs.get("timezone")
    if isinstance(tz_name, str) and tz_name.strip():
        return tz_name.strip()
    return None


def compute_timezone_offset_minutes(
    tz_name: str,
    *,
    at: Optional[datetime] = None,
    local_date: Optional[date] = None,
) -> Optional[int]:
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        return None

    if local_date:
        local_dt = datetime.combine(local_date, time.min, tzinfo=tzinfo)
        offset = local_dt.utcoffset()
    else:
        dt = at or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tzinfo)
        offset = local_dt.utcoffset()

    if offset is None:
        return None
    return int(-offset.total_seconds() / 60)


def resolve_timezone_offset_minutes(
    settings: Mapping[str, Any] | None,
    *,
    at: Optional[datetime] = None,
    local_date: Optional[date] = None,
) -> Optional[int]:
    tz_name = resolve_timezone_name(settings)
    if not tz_name:
        return None
    return compute_timezone_offset_minutes(tz_name, at=at, local_date=local_date)


async def resolve_user_tz_offset_minutes(
    session: AsyncSession,
    user_id: UUID,
    *,
    tz_offset_minutes: Optional[int] = None,
    at: Optional[datetime] = None,
    local_date: Optional[date] = None,
) -> int:
    if tz_offset_minutes is not None:
        try:
            return int(tz_offset_minutes)
        except (TypeError, ValueError):
            return 0
    settings = await fetch_user_settings(session, user_id)
    resolved = resolve_timezone_offset_minutes(settings, at=at, local_date=local_date)
    return int(resolved) if resolved is not None else 0


def build_preference_guidance(settings: Mapping[str, Any] | None) -> str:
    prefs = resolve_preferences(settings)
    if not prefs:
        return ""

    focus_tags = prefs.get("focus_tags") if isinstance(prefs.get("focus_tags"), list) else []
    focus_people = prefs.get("focus_people") if isinstance(prefs.get("focus_people"), list) else []
    focus_places = prefs.get("focus_places") if isinstance(prefs.get("focus_places"), list) else []
    focus_topics = prefs.get("focus_topics") if isinstance(prefs.get("focus_topics"), list) else []

    lines: list[str] = []
    if focus_tags:
        lines.append(f"- Emphasize tags: {', '.join(str(t) for t in focus_tags)}.")
    if focus_people:
        lines.append(f"- Emphasize people: {', '.join(str(p) for p in focus_people)}.")
    if focus_places:
        lines.append(f"- Emphasize places: {', '.join(str(p) for p in focus_places)}.")
    if focus_topics:
        lines.append(f"- Emphasize topics: {', '.join(str(t) for t in focus_topics)}.")

    if not lines:
        return ""

    return "\n\nUser focus preferences:\n" + "\n".join(lines) + "\n"

