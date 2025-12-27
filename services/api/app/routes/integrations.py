"""Integration endpoints for third-party data sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urljoin

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from secrets import token_urlsafe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import DEFAULT_TEST_USER_ID, DataConnection
from ..db.session import get_session
from ..google_photos import create_picker_session, get_valid_access_token, store_google_photos_tokens
from ..tasks.google_photos import sync_google_photos_media


router = APIRouter()
settings = get_settings()


@dataclass
class StateEntry:
    created_at: datetime


class OAuthStateStore:
    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._states: dict[str, StateEntry] = {}

    def create_state(self) -> str:
        state = token_urlsafe(32)
        self._states[state] = StateEntry(created_at=datetime.now(timezone.utc))
        return state

    def validate_state(self, state: str) -> bool:
        entry = self._states.pop(state, None)
        if not entry:
            return False
        if datetime.now(timezone.utc) - entry.created_at > self._ttl:
            return False
        return True


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
async def get_google_photos_auth_url() -> AuthUrlResponse:
    _ensure_google_photos_config()
    state = state_store.create_state()
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
async def get_google_photos_status(session: AsyncSession = Depends(get_session)) -> StatusResponse:
    connection = await _get_google_photos_connection(session)
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
    if not state_store.validate_state(state):
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

    await store_google_photos_tokens(session, token_data, access_token, expires_at)

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
async def sync_google_photos(request: SyncRequest) -> SyncResponse:
    task = sync_google_photos_media.delay(session_id=request.session_id)
    return SyncResponse(task_id=task.id)


@router.post("/integrations/google/photos/picker-session", response_model=PickerSessionResponse)
async def start_google_photos_picker(
    session: AsyncSession = Depends(get_session),
) -> PickerSessionResponse:
    _ensure_google_photos_config()
    connection = await _get_google_photos_connection(session)
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


async def _get_google_photos_connection(session: AsyncSession) -> Optional[DataConnection]:
    result = await session.execute(
        select(DataConnection).where(
            DataConnection.user_id == DEFAULT_TEST_USER_ID,
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
