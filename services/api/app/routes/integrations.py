"""Integration endpoints for third-party data sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from urllib.parse import urlencode, urljoin

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from secrets import token_urlsafe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user_id
from ..config import get_settings
from ..db.models import DataConnection
from ..db.session import get_session
from ..google_photos import (
    create_picker_session,
    extract_picker_media_fields,
    fetch_picker_media_items,
    get_valid_access_token,
    PickerPendingError,
    store_google_photos_tokens,
)
from ..tasks.google_photos import sync_google_photos_media


router = APIRouter()
settings = get_settings()


@dataclass
class StateEntry:
    created_at: datetime
    user_id: UUID


class OAuthStateStore:
    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._states: dict[str, StateEntry] = {}

    def create_state(self, user_id: UUID) -> str:
        state = token_urlsafe(32)
        self._states[state] = StateEntry(created_at=datetime.now(timezone.utc), user_id=user_id)
        return state

    def consume_state(self, state: str) -> Optional[UUID]:
        entry = self._states.pop(state, None)
        if not entry:
            return None
        if datetime.now(timezone.utc) - entry.created_at > self._ttl:
            return None
        return entry.user_id


state_store = OAuthStateStore()


class AuthUrlResponse(BaseModel):
    auth_url: str = Field(..., description="Google OAuth authorization URL")
    state: str = Field(..., description="State token to prevent CSRF")


class StatusResponse(BaseModel):
    connected: bool
    connected_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class SyncResponse(BaseModel):
    task_id: str
    status: str = "queued"


class SyncRequest(BaseModel):
    session_id: Optional[str] = None


class PickerSessionResponse(BaseModel):
    session_id: str
    picker_uri: str


class PickerMediaItem(BaseModel):
    id: str
    base_url: Optional[str] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    creation_time: Optional[str] = None


class PickerMediaResponse(BaseModel):
    items: list[PickerMediaItem]
    status: str = "ready"
    message: Optional[str] = None


class DisconnectResponse(BaseModel):
    status: str = "disconnected"


def _ensure_google_photos_config() -> None:
    if not settings.google_photos_client_id or not settings.google_photos_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Google Photos OAuth is not configured.",
        )
    if not settings.google_photos_redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Google Photos redirect URI is not configured.",
        )


@router.get("/integrations/google/photos/auth-url", response_model=AuthUrlResponse)
async def get_google_photos_auth_url(
    user_id: UUID = Depends(get_current_user_id),
) -> AuthUrlResponse:
    _ensure_google_photos_config()
    state = state_store.create_state(user_id)
    params = {
        "client_id": settings.google_photos_client_id,
        "redirect_uri": settings.google_photos_redirect_uri,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "scope": " ".join(settings.google_photos_scopes),
        "state": state,
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return AuthUrlResponse(auth_url=auth_url, state=state)


@router.get("/integrations/google/photos/status", response_model=StatusResponse)
async def get_google_photos_status(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> StatusResponse:
    connection = await _get_google_photos_connection(session, user_id)
    if not connection:
        return StatusResponse(connected=False)

    config = connection.config or {}
    connected_at = _parse_timestamp(config.get("connected_at"))
    expires_at = _parse_timestamp(config.get("expires_at"))
    if not config.get("access_token"):
        return StatusResponse(connected=False)
    return StatusResponse(connected=True, connected_at=connected_at, expires_at=expires_at)


@router.get("/integrations/google/photos/callback")
async def google_photos_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _ensure_google_photos_config()
    if error:
        destination = _build_web_redirect("error", error)
        return RedirectResponse(destination)
    if not code or not state:
        destination = _build_web_redirect("error", "missing_code_or_state")
        return RedirectResponse(destination)
    resolved_user_id = state_store.consume_state(state)
    if not resolved_user_id:
        destination = _build_web_redirect("error", "invalid_state")
        return RedirectResponse(destination)

    token_data = await _exchange_google_photos_code(code)
    access_token = token_data.get("access_token")
    if not access_token:
        destination = _build_web_redirect("error", "token_exchange_failed")
        return RedirectResponse(destination)

    expires_at = None
    if token_data.get("expires_in"):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token_data["expires_in"]))

    await store_google_photos_tokens(session, resolved_user_id, token_data, access_token, expires_at)

    destination = _build_web_redirect("connected", None)
    return RedirectResponse(destination)


async def _exchange_google_photos_code(code: str) -> dict:
    token_endpoint = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": settings.google_photos_client_id,
        "client_secret": settings.google_photos_client_secret,
        "redirect_uri": settings.google_photos_redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(token_endpoint, data=payload)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Failed to exchange Google OAuth code.")
    return response.json()


def _build_web_redirect(status: str, detail: Optional[str]) -> str:
    base_url = settings.web_app_url.rstrip("/") + "/"
    params = {"integration": "google_photos", "status": status}
    if detail:
        params["detail"] = detail
    return urljoin(base_url, f"?{urlencode(params)}")


@router.post("/integrations/google/photos/sync", response_model=SyncResponse)
async def sync_google_photos(
    request: SyncRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> SyncResponse:
    task = sync_google_photos_media.delay(user_id=str(user_id), session_id=request.session_id)
    return SyncResponse(task_id=task.id)


@router.post("/integrations/google/photos/picker-session", response_model=PickerSessionResponse)
async def start_google_photos_picker(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> PickerSessionResponse:
    _ensure_google_photos_config()
    connection = await _get_google_photos_connection(session, user_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Google Photos connection not found.")
    access_token = await get_valid_access_token(session, connection)
    if not access_token:
        raise HTTPException(status_code=401, detail="Google Photos token missing or expired.")

    try:
        session_id, picker_uri = await create_picker_session(access_token)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to create Google Photos picker session. "
                "Ensure the Google Photos Picker API is enabled and the OAuth client is authorized. "
                f"Details: {exc}"
            ),
        ) from exc
    config = dict(connection.config or {})
    config["picker_session_id"] = session_id
    config["picker_session_created_at"] = datetime.now(timezone.utc).isoformat()
    connection.config = config
    connection.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return PickerSessionResponse(session_id=session_id, picker_uri=picker_uri)


@router.get("/integrations/google/photos/picker-items", response_model=PickerMediaResponse)
async def get_google_photos_picker_items(
    session_id: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> PickerMediaResponse:
    _ensure_google_photos_config()
    connection = await _get_google_photos_connection(session, user_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Google Photos connection not found.")
    access_token = await get_valid_access_token(session, connection)
    if not access_token:
        raise HTTPException(status_code=401, detail="Google Photos token missing or expired.")
    try:
        items = await fetch_picker_media_items(access_token, session_id)
    except PickerPendingError:
        return PickerMediaResponse(
            items=[],
            status="pending",
            message="Waiting for Google Photos selection to complete.",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch picker items: {exc}") from exc

    mapped: list[PickerMediaItem] = []
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        base_url, filename, mime_type, creation_time = extract_picker_media_fields(item)
        mapped.append(
            PickerMediaItem(
                id=item_id,
                base_url=base_url,
                filename=filename,
                mime_type=mime_type,
                creation_time=creation_time,
            )
        )
    return PickerMediaResponse(items=mapped)


@router.post("/integrations/google/photos/disconnect", response_model=DisconnectResponse)
async def disconnect_google_photos(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> DisconnectResponse:
    connection = await _get_google_photos_connection(session, user_id)
    if not connection:
        return DisconnectResponse()
    config = dict(connection.config or {})
    for key in [
        "access_token",
        "refresh_token",
        "expires_at",
        "connected_at",
        "picker_session_id",
        "picker_session_created_at",
    ]:
        config.pop(key, None)
    connection.config = config
    connection.status = "disconnected"
    connection.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return DisconnectResponse()


async def _get_google_photos_connection(
    session: AsyncSession,
    user_id: UUID,
) -> Optional[DataConnection]:
    result = await session.execute(
        select(DataConnection).where(
            DataConnection.user_id == user_id,
            DataConnection.provider == "google_photos",
        )
    )
    return result.scalar_one_or_none()


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
