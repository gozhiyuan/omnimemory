"""User settings and API key management endpoints."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import generate_api_key, get_api_key_prefix, get_current_user_id, API_KEY_PREFIX
from ..db.models import ApiKey, UserSettings
from ..db.session import get_session
from ..recaps import resolve_week_window
from ..tasks.recaps import weekly_recap_for_user
from ..user_settings import fetch_user_settings
from ..config import get_settings


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
    settings = get_settings()
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        default_openclaw = {
            "openclaw": {
                "syncMemory": bool(settings.openclaw_sync_memory),
                "workspace": settings.openclaw_workspace,
            }
        }
        return SettingsResponse(settings=default_openclaw, updated_at=None)
    stored = record.settings or {}
    openclaw = stored.get("openclaw")
    if not isinstance(openclaw, dict):
        openclaw = {}
    if "syncMemory" not in openclaw:
        openclaw = {**openclaw, "syncMemory": bool(settings.openclaw_sync_memory)}
    if "workspace" not in openclaw:
        openclaw = {**openclaw, "workspace": settings.openclaw_workspace}
    if openclaw:
        stored = {**stored, "openclaw": openclaw}
    return SettingsResponse(settings=stored, updated_at=record.updated_at)


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


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------


class ApiKeyCreateRequest(BaseModel):
    """Request to create a new API key."""

    name: str
    scopes: Optional[list[str]] = None  # Default: ["read", "write"]
    expires_in_days: Optional[int] = None  # None = never expires


class ApiKeyCreateResponse(BaseModel):
    """Response after creating an API key. Full key shown only once."""

    id: str
    name: str
    key: str  # Full key - shown only once!
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: Optional[datetime]


class ApiKeyInfo(BaseModel):
    """API key info (without the full key)."""

    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]


class ApiKeyListResponse(BaseModel):
    """List of API keys."""

    keys: list[ApiKeyInfo]


@router.post("/settings/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreateResponse:
    """Create a new API key for external integrations.

    The full key is returned only once in this response.
    Store it securely - it cannot be retrieved again.
    """
    # Validate name
    if not payload.name or len(payload.name) > 255:
        raise HTTPException(status_code=400, detail="Name is required and must be 255 characters or less.")

    # Generate key
    full_key, key_hash = generate_api_key()
    key_prefix = get_api_key_prefix(full_key)

    # Calculate expiration
    expires_at = None
    if payload.expires_in_days:
        from datetime import timedelta

        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)

    # Default scopes
    scopes = payload.scopes or ["read", "write"]

    now = datetime.now(timezone.utc)
    api_key = ApiKey(
        user_id=user_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=payload.name,
        scopes=scopes,
        created_at=now,
        expires_at=expires_at,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    return ApiKeyCreateResponse(
        id=str(api_key.id),
        name=api_key.name,
        key=full_key,  # Only time the full key is returned!
        key_prefix=key_prefix,
        scopes=scopes,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


@router.get("/settings/api-keys", response_model=ApiKeyListResponse)
async def list_api_keys(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyListResponse:
    """List all API keys for the current user (without revealing the full keys)."""
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()

    return ApiKeyListResponse(
        keys=[
            ApiKeyInfo(
                id=str(key.id),
                name=key.name,
                key_prefix=key.key_prefix,
                scopes=key.scopes or [],
                created_at=key.created_at,
                last_used_at=key.last_used_at,
                expires_at=key.expires_at,
            )
            for key in keys
        ]
    )


@router.delete("/settings/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Revoke (delete) an API key."""
    try:
        key_uuid = UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key ID.")

    result = await session.execute(
        select(ApiKey).where(
            ApiKey.id == key_uuid,
            ApiKey.user_id == user_id,
            ApiKey.revoked_at.is_(None),
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found.")

    api_key.revoked_at = datetime.now(timezone.utc)
    await session.commit()

    return {"success": True, "message": "API key revoked."}
