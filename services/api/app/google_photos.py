"""Shared Google Photos integration helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db.models import DataConnection, DEFAULT_TEST_USER_ID


GOOGLE_PHOTOS_PICKER_SESSIONS_ENDPOINT = "https://photospicker.googleapis.com/v1/sessions"
GOOGLE_PHOTOS_PICKER_MEDIA_ENDPOINT = "https://photospicker.googleapis.com/v1/mediaItems"
GOOGLE_PHOTOS_PICKER_MEDIA_ITEM_ENDPOINT = "https://photospicker.googleapis.com/v1/mediaItems/"
GOOGLE_PHOTOS_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def parse_google_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def extract_picker_media_fields(item: dict) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return base_url, filename, mime_type, creation_time from a picker item."""
    gpm = (
        item.get("googlePhotosMediaItem")
        or item.get("mediaItem")
        or item.get("mediaFile")
        or item
    )
    base_url = item.get("baseUrl") or gpm.get("baseUrl")
    filename = item.get("filename") or gpm.get("filename")
    mime_type = item.get("mimeType") or gpm.get("mimeType")
    creation_time = (
        item.get("createTime")
        or (item.get("mediaMetadata") or {}).get("creationTime")
        or (gpm.get("mediaMetadata") or {}).get("creationTime")
        or gpm.get("createTime")
    )
    return base_url, filename, mime_type, creation_time


async def store_google_photos_tokens(
    session: AsyncSession,
    token_data: dict,
    access_token: str,
    expires_at: Optional[datetime],
) -> None:
    connection = await _get_google_photos_connection(session)
    connected_at = datetime.now(timezone.utc)
    refresh_token = token_data.get("refresh_token")
    if connection and not refresh_token:
        refresh_token = (connection.config or {}).get("refresh_token")

    config = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "connected_at": connected_at.isoformat(),
    }

    if connection is None:
        connection = DataConnection(
            user_id=DEFAULT_TEST_USER_ID,
            provider="google_photos",
            status="active",
            config=config,
            updated_at=connected_at,
        )
        session.add(connection)
    else:
        connection.config = config
        connection.updated_at = connected_at
    await session.commit()


async def get_valid_access_token(session: AsyncSession, connection: DataConnection) -> Optional[str]:
    config = connection.config or {}
    access_token = config.get("access_token")
    expires_at = parse_google_timestamp(config.get("expires_at"))
    if access_token and expires_at:
        if expires_at - datetime.now(timezone.utc) > timedelta(minutes=5):
            return access_token
    if access_token and not expires_at:
        return access_token
    return await refresh_access_token(session, connection)


async def refresh_access_token(
    session: AsyncSession,
    connection: DataConnection,
) -> Optional[str]:
    settings = get_settings()
    refresh_token = (connection.config or {}).get("refresh_token")
    if not refresh_token:
        return None
    if not settings.google_photos_client_id or not settings.google_photos_client_secret:
        logger.warning("Google Photos client credentials missing; cannot refresh token")
        return None

    payload = {
        "client_id": settings.google_photos_client_id,
        "client_secret": settings.google_photos_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(GOOGLE_PHOTOS_TOKEN_ENDPOINT, data=payload)
    if response.status_code >= 400:
        logger.warning("Failed to refresh Google Photos token: {}", response.text)
        return None

    token_data = response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return None

    expires_at = None
    if token_data.get("expires_in"):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token_data["expires_in"]))

    config = dict(connection.config or {})
    config["access_token"] = access_token
    if token_data.get("refresh_token"):
        config["refresh_token"] = token_data["refresh_token"]
    config["expires_at"] = expires_at.isoformat() if expires_at else None
    connection.config = config
    connection.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return access_token


async def create_picker_session(access_token: str) -> Tuple[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(GOOGLE_PHOTOS_PICKER_SESSIONS_ENDPOINT, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Google Photos picker session failed ({response.status_code}): {response.text}"
        )
    payload = response.json()
    session_id = payload.get("id")
    picker_uri = payload.get("pickerUri")
    if not session_id or not picker_uri:
        raise RuntimeError("Google Photos picker session response missing id or pickerUri")
    return session_id, picker_uri


async def fetch_picker_media_items(access_token: str, session_id: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {access_token}"}
    page_token: Optional[str] = None
    items: list[dict] = []
    use_fields_mask = False
    fields_mask = (
        "mediaItems("
        "id,"
        "googlePhotosMediaItem(baseUrl,filename,mimeType,mediaMetadata/creationTime)"
        "),nextPageToken"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params = {
                "pageSize": "100",
                "sessionId": session_id,
            }
            if use_fields_mask:
                params["fields"] = fields_mask
            if page_token:
                params["pageToken"] = page_token
            response = await client.get(GOOGLE_PHOTOS_PICKER_MEDIA_ENDPOINT, headers=headers, params=params)
            if response.status_code >= 400:
                if use_fields_mask and response.status_code == 400 and "fields" in response.text:
                    logger.warning("Picker media request failed with fields mask; retrying without fields: {}", response.text)
                    use_fields_mask = False
                    page_token = None
                    items = []
                    continue
                raise RuntimeError(
                    f"Google Photos picker media fetch failed ({response.status_code}): {response.text}"
                )
            payload = response.json()
            items.extend(payload.get("mediaItems", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
    if items:
        first = items[0]
        logger.info(
            "Picker sample keys={} gpm_keys={} mediaItem_keys={}",
            list(first.keys()),
            list((first.get("googlePhotosMediaItem") or {}).keys()),
            list((first.get("mediaItem") or {}).keys()),
        )
        logger.info("Picker first item snippet={}", {k: first.get(k) for k in list(first.keys())[:6]})
    return items


async def fetch_picker_media_item(
    access_token: str,
    session_id: str,
    media_item_id: str,
) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{GOOGLE_PHOTOS_PICKER_MEDIA_ITEM_ENDPOINT}{media_item_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers, params={"sessionId": session_id})
    if response.status_code >= 400:
        raise RuntimeError(
            f"Google Photos picker media item fetch failed ({response.status_code}): {response.text}"
        )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _get_google_photos_connection(session: AsyncSession) -> Optional[DataConnection]:
    result = await session.execute(
        select(DataConnection).where(
            DataConnection.user_id == DEFAULT_TEST_USER_ID,
            DataConnection.provider == "google_photos",
        )
    )
    return result.scalar_one_or_none()
