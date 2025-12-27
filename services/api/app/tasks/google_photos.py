"""Google Photos ingestion tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import select

from ..celery_app import celery_app
from ..db.models import DEFAULT_TEST_USER_ID, DataConnection, SourceItem, User
from ..db.session import isolated_session
from ..google_photos import (
    extract_picker_media_fields,
    fetch_picker_media_item,
    fetch_picker_media_items,
    get_valid_access_token,
    parse_google_timestamp,
)
from .process_item import process_item


def _media_item_type(mime_type: Optional[str]) -> str:
    if mime_type and mime_type.startswith("video/"):
        return "video"
    return "photo"


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
    base_url, _, mime_type, creation_time = extract_picker_media_fields(item)
    if not base_url:
        try:
            hydrated = await fetch_picker_media_item(access_token, session_id, media_id)
        except RuntimeError as exc:
            logger.warning("Failed to hydrate picker item {}: {}", media_id, exc)
            hydrated = {}
        base_url, _, mime_type, creation_time = extract_picker_media_fields(hydrated)
    if not base_url:
        logger.warning(
            "Skipping media item {} without baseUrl (keys={} session={})",
            media_id,
            list(item.keys()),
            session_id,
        )
        return None
    download_url = f"{base_url}=d"
    storage_key = download_url
    mime_type = mime_type or "application/octet-stream"
    captured_at = parse_google_timestamp(creation_time)

    async with isolated_session() as session:
        result = await session.execute(
            select(SourceItem).where(
                SourceItem.connection_id == connection.id,
                SourceItem.storage_key == storage_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            if existing.processing_status != "completed":
                existing.processing_status = "pending"
                existing.processing_error = None
                existing.updated_at = datetime.now(timezone.utc)
                await session.commit()

                process_item.delay(
                    {
                        "item_id": str(existing.id),
                        "storage_key": storage_key,
                        "item_type": existing.item_type,
                        "user_id": str(connection.user_id),
                        "captured_at": (existing.captured_at or captured_at or datetime.now(timezone.utc)).isoformat(),
                        "content_type": existing.content_type or mime_type,
                        "original_filename": existing.original_filename or item.get("filename"),
                        "remote_only": True,
                    }
                )
            return existing.id

        user = await session.get(User, connection.user_id)
        if user is None:
            session.add(User(id=connection.user_id))

        source_item = SourceItem(
            id=uuid4(),
            user_id=connection.user_id,
            connection_id=connection.id,
            storage_key=storage_key,
            item_type=_media_item_type(mime_type),
            content_type=mime_type,
            original_filename=item.get("filename"),
            captured_at=captured_at,
            processing_status="pending",
        )
        session.add(source_item)
        await session.commit()

        process_item.delay(
            {
                "item_id": str(source_item.id),
                "storage_key": storage_key,
                "item_type": source_item.item_type,
                "user_id": str(connection.user_id),
                "captured_at": captured_at.isoformat() if captured_at else None,
                "content_type": mime_type,
                "original_filename": item.get("filename"),
                "remote_only": True,
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
