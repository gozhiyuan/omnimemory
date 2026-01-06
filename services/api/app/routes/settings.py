"""User settings endpoints."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user_id
from ..db.models import UserSettings
from ..db.session import get_session
from ..recaps import resolve_week_window
from ..tasks.recaps import weekly_recap_for_user
from ..user_settings import fetch_user_settings


router = APIRouter()


class SettingsResponse(BaseModel):
    settings: dict[str, Any]
    updated_at: Optional[datetime] = None


class SettingsUpdateRequest(BaseModel):
    settings: dict[str, Any]


class WeeklyRecapRequest(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    force: bool = False


class WeeklyRecapResponse(BaseModel):
    task_id: str
    start_date: date
    end_date: date
    status: str = "queued"


@router.get("/settings", response_model=SettingsResponse)
async def get_user_settings(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        return SettingsResponse(settings={}, updated_at=None)
    return SettingsResponse(settings=record.settings or {}, updated_at=record.updated_at)


@router.put("/settings", response_model=SettingsResponse)
async def update_user_settings(
    payload: SettingsUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    now = datetime.now(timezone.utc)
    table = UserSettings.__table__
    settings_payload = payload.settings or {}
    stmt = insert(table).values(
        {
            table.c.user_id: user_id,
            table.c.settings: settings_payload,
            table.c.updated_at: now,
        }
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.user_id],
        set_={
            table.c.settings: settings_payload,
            table.c.updated_at: now,
        },
    )
    await session.execute(stmt)
    await session.commit()
    return SettingsResponse(settings=settings_payload, updated_at=now)


@router.post("/settings/weekly-recap", response_model=WeeklyRecapResponse)
async def trigger_weekly_recap(
    payload: WeeklyRecapRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> WeeklyRecapResponse:
    settings_payload = await fetch_user_settings(session, user_id)
    notifications = settings_payload.get("notifications") if isinstance(settings_payload, dict) else {}
    weekly_enabled = bool(notifications.get("weeklySummary")) if isinstance(notifications, dict) else False
    if not payload.force and not weekly_enabled:
        raise HTTPException(status_code=400, detail="Weekly recap is disabled in settings.")

    preferences = settings_payload.get("preferences") if isinstance(settings_payload, dict) else {}
    tz_name = preferences.get("timezone") if isinstance(preferences, dict) else None
    window = resolve_week_window(
        tz_name=tz_name,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    task = weekly_recap_for_user.delay(
        str(user_id),
        start_date=window.start_date.isoformat(),
        end_date=window.end_date.isoformat(),
        tz_name=window.timezone,
    )
    return WeeklyRecapResponse(
        task_id=task.id,
        start_date=window.start_date,
        end_date=window.end_date,
    )
