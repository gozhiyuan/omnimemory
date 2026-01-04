"""Google Photos ingestion tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from loguru import logger
import re
from pathlib import Path
import httpx
from sqlalchemy import select, or_

from ..celery_app import celery_app
from ..db.models import DEFAULT_TEST_USER_ID, DataConnection, SourceItem, User
from ..db.session import isolated_session
from ..google_photos import (
    extract_picker_location,
    extract_picker_media_fields,
    fetch_picker_media_item,
    fetch_picker_media_items,
    get_valid_access_token,
    parse_google_timestamp,
)
from ..storage import get_storage_provider
from .process_item import process_item


def _media_item_type(mime_type: Optional[str]) -> str:
    if mime_type and mime_type.startswith("video/"):
        return "video"
    return "photo"


def _safe_filename(name: str) -> str:
    base = Path(name).name
    if "." in base:
        stem, suffix = base.rsplit(".", 1)
    else:
        stem, suffix = base, ""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-") or "file"
    suffix = re.sub(r"[^A-Za-z0-9]", "", suffix)
    return f"{stem}.{suffix}" if suffix else stem


def _infer_filename(media_id: str, filename: Optional[str], mime_type: Optional[str]) -> str:
    if filename:
        return _safe_filename(filename)
    ext_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
    }
    ext = ext_map.get((mime_type or "").lower(), "")
    return _safe_filename(media_id + ext)


def _build_download_url(base_url: str, mime_type: Optional[str]) -> str:
    if not base_url:
        return base_url
    suffix = "dv" if mime_type and mime_type.startswith("video/") else "d"
    base = base_url.split("=", 1)[0]
    return f"{base}={suffix}"


async def _fetch_media_items(access_token: str, session_id: str) -> list[dict[str, Any]]:
    items = await fetch_picker_media_items(access_token, session_id)
    return [item for item in items if isinstance(item, dict)]


async def _ingest_media_item(
    connection: DataConnection,
    item: dict[str, Any],
    access_token: str,
    session_id: str,
) -> Optional[UUID]:
    media_id = (
        item.get("id")
        or (item.get("mediaItem") or {}).get("id")
        or (item.get("googlePhotosMediaItem") or {}).get("id")
        or (item.get("mediaFile") or {}).get("id")
    )
    if not media_id:
        return None
    base_url, filename, mime_type, creation_time = extract_picker_media_fields(item)
    provider_location = extract_picker_location(item)
    if not base_url:
        try:
            hydrated = await fetch_picker_media_item(access_token, session_id, media_id)
        except RuntimeError as exc:
            logger.warning("Failed to hydrate picker item {}: {}", media_id, exc)
            hydrated = {}
        base_url, _, mime_type, creation_time = extract_picker_media_fields(hydrated)
        if not provider_location:
            provider_location = extract_picker_location(hydrated)
    if not base_url:
        logger.warning(
            "Skipping media item {} without baseUrl (keys={} session={})",
            media_id,
            list(item.keys()),
            session_id,
        )
        return None
    mime_type = mime_type or "application/octet-stream"
    captured_at = parse_google_timestamp(creation_time)
    key_date = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    inferred_name = _infer_filename(media_id, filename, mime_type)
    desired_storage_key = (
        f"google_photos/{connection.user_id}/{key_date:%Y/%m/%d}/{media_id}-{inferred_name}"
    )
    download_url = _build_download_url(base_url, mime_type)
    storage_key = desired_storage_key

    storage = get_storage_provider()

    async with isolated_session() as session:
        result = await session.execute(
            select(SourceItem).where(
                SourceItem.connection_id == connection.id,
                or_(
                    SourceItem.external_id == media_id,
                    SourceItem.storage_key.in_([storage_key, download_url]),
                ),
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            if existing.storage_key and not existing.storage_key.startswith(("http://", "https://")):
                storage_key = existing.storage_key
            else:
                existing.storage_key = desired_storage_key
                storage_key = desired_storage_key
            existing.content_type = existing.content_type or mime_type
            existing.original_filename = existing.original_filename or filename
            existing.provider = existing.provider or "google_photos"
            existing.external_id = existing.external_id or media_id
            if captured_at and not existing.captured_at:
                existing.captured_at = captured_at
            if captured_at and not existing.event_time_utc:
                existing.event_time_utc = captured_at
                existing.event_time_source = "provider"
                existing.event_time_confidence = 0.85
            existing.processing_status = "pending"
            existing.processing_error = None
            existing.updated_at = datetime.now(timezone.utc)

        user = await session.get(User, connection.user_id)
        if user is None:
            session.add(User(id=connection.user_id))

        if not existing:
            source_item = SourceItem(
                id=uuid4(),
                user_id=connection.user_id,
                connection_id=connection.id,
                provider="google_photos",
                external_id=media_id,
                storage_key=storage_key,
                item_type=_media_item_type(mime_type),
                content_type=mime_type,
                original_filename=filename,
                captured_at=captured_at,
                event_time_utc=captured_at,
                event_time_source="provider",
                event_time_confidence=0.85,
                processing_status="pending",
            )
            session.add(source_item)
            await session.commit()
        else:
            source_item = existing
            await session.commit()

    # Download and store the media in our storage to survive token revocation.
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(download_url, headers=headers, follow_redirects=True)
        response.raise_for_status()
        blob = response.content
    storage.store(storage_key, blob, mime_type)

    process_item.delay(
        {
            "item_id": str(source_item.id),
            "storage_key": storage_key,
            "item_type": source_item.item_type,
            "user_id": str(connection.user_id),
            "captured_at": captured_at.isoformat() if captured_at else None,
            "content_type": mime_type,
            "original_filename": filename,
            "provider_location": provider_location,
        }
    )
    return source_item.id


async def _sync_google_photos(session_id: Optional[str]) -> dict[str, Any]:
    async with isolated_session() as session:
        result = await session.execute(
            select(DataConnection).where(
                DataConnection.user_id == DEFAULT_TEST_USER_ID,
                DataConnection.provider == "google_photos",
            )
        )
        connection = result.scalar_one_or_none()
        if not connection:
            return {"status": "skipped", "reason": "no_connection"}

        access_token = await get_valid_access_token(session, connection)
        if not access_token:
            return {"status": "skipped", "reason": "no_access_token"}

        config = connection.config or {}
        resolved_session_id = session_id or config.get("picker_session_id")
        if not resolved_session_id:
            return {"status": "skipped", "reason": "no_picker_session"}

        await session.commit()
        connection_id = connection.id

    media_items = await _fetch_media_items(access_token, resolved_session_id)
    ingested = 0
    for item in media_items:
        item_id = await _ingest_media_item(connection, item, access_token, resolved_session_id)
        if item_id:
            ingested += 1

    async with isolated_session() as session:
        result = await session.execute(select(DataConnection).where(DataConnection.id == connection_id))
        connection = result.scalar_one_or_none()
        if connection:
            config = dict(connection.config or {})
            config["last_sync_at"] = datetime.now(timezone.utc).isoformat()
            connection.config = config
            connection.updated_at = datetime.now(timezone.utc)
            await session.commit()

    return {"status": "completed", "ingested": ingested, "total": len(media_items)}


@celery_app.task(name="integrations.google_photos.sync", bind=True)
def sync_google_photos_media(self, session_id: Optional[str] = None) -> dict[str, Any]:
    """Fetch Google Photos media items and enqueue ingestion."""

    try:
        return asyncio.run(_sync_google_photos(session_id))
    except Exception as exc:  # pragma: no cover - task boundary
        logger.exception("Google Photos sync failed: {}", exc)
        raise
