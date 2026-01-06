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
