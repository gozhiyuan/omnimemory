"""User settings helpers."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


def resolve_annotation_defaults(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    prefs = resolve_preferences(settings)
    defaults = prefs.get("annotation_defaults")
    return defaults if isinstance(defaults, dict) else {}
