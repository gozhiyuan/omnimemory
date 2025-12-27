"""Background tasks for Google Photos ingestion."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..celery_app import celery_app
from ..config import get_settings
from ..db.models import DataConnection, SourceItem
from ..db.session import isolated_session
from ..google_photos import MEDIA_ITEMS_ENDPOINT, ensure_valid_access_token
from .process_item import process_item


def _parse_google_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _media_item_type(media_item: Dict[str, Any]) -> str:
    metadata = media_item.get("mediaMetadata") or {}
    if "video" in metadata:
        return "video"
    return "photo"


async def _fetch_media_page(client: httpx.AsyncClient, access_token: str, page_token: Optional[str]) -> Dict[str, Any]:
    params = {"pageSize": 100}
    if page_token:
        params["pageToken"] = page_token
    response = await client.get(
        MEDIA_ITEMS_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
    )
    response.raise_for_status()
    return response.json()


async def _sync_connection(connection_id: UUID) -> Dict[str, Any]:
    settings = get_settings()
    new_items = 0

    async with isolated_session() as session:
        connection = await session.get(DataConnection, connection_id)
        if connection is None:
            raise ValueError(f"Connection {connection_id} not found")
        if connection.provider != "google_photos":
            raise ValueError("Connection is not a Google Photos provider")
        if not connection.oauth_token:
            raise ValueError("Connection missing OAuth token payload")

        oauth_payload = await ensure_valid_access_token(settings, dict(connection.oauth_token))
        if oauth_payload != connection.oauth_token:
            connection.oauth_token = oauth_payload
            await session.commit()

        access_token = oauth_payload.get("access_token")
        if not access_token:
            raise ValueError("OAuth payload missing access token")

        async with httpx.AsyncClient(timeout=30) as client:
            next_page: Optional[str] = None
            while True:
                payload = await _fetch_media_page(client, access_token, next_page)
                items: List[Dict[str, Any]] = payload.get("mediaItems") or []
                for item in items:
                    base_url = item.get("baseUrl")
                    if not base_url:
                        continue
                    download_url = f"{base_url}=d"
                    exists_stmt = select(SourceItem.id).where(
                        SourceItem.connection_id == connection.id,
                        SourceItem.storage_key == download_url,
                    )
                    exists = await session.execute(exists_stmt)
                    if exists.scalar() is not None:
                        continue

                    captured_at = _parse_google_datetime(
                        (item.get("mediaMetadata") or {}).get("creationTime")
                    )
                    source_item = SourceItem(
                        user_id=connection.user_id,
                        connection_id=connection.id,
                        storage_key=download_url,
                        item_type=_media_item_type(item),
                        captured_at=captured_at,
                        content_type=item.get("mimeType"),
                        original_filename=item.get("filename"),
                        processing_status="pending",
                    )
                    session.add(source_item)
                    await session.flush()

                    process_item.delay(
                        {
                            "item_id": str(source_item.id),
                            "storage_key": download_url,
                            "item_type": source_item.item_type,
                            "user_id": str(connection.user_id),
                            "captured_at": captured_at.isoformat() if captured_at else None,
                            "content_type": source_item.content_type,
                            "original_filename": source_item.original_filename,
                        }
                    )
                    new_items += 1

                await session.commit()
                next_page = payload.get("nextPageToken")
                if not next_page:
                    break

        connection.last_sync_at = datetime.now(timezone.utc)
        await session.commit()

    return {"connection_id": str(connection_id), "new_items": new_items}


@celery_app.task(name="google_photos.sync_connection", bind=True)
def sync_google_photos(self, connection_id: str) -> Dict[str, Any]:
    """Fetch media items for a single Google Photos connection."""

    try:
        return asyncio.run(_sync_connection(UUID(connection_id)))
    except SQLAlchemyError as exc:  # pragma: no cover - unexpected database errors
        logger.exception("Database error while syncing Google Photos: {}", exc)
        raise
    except Exception as exc:  # pragma: no cover - propagate for retry logic
        logger.exception("Unhandled exception while syncing Google Photos: {}", exc)
        raise


async def _sync_all_connections() -> Dict[str, Any]:
    async with isolated_session() as session:
        result = await session.execute(
            select(DataConnection.id).where(
                DataConnection.provider == "google_photos",
                DataConnection.status == "active",
            )
        )
        connection_ids = [row[0] for row in result.fetchall()]

    for connection_id in connection_ids:
        sync_google_photos.delay(str(connection_id))

    return {"connections": len(connection_ids)}


@celery_app.task(name="google_photos.sync_all", bind=True)
def sync_all_google_photos(self) -> Dict[str, Any]:
    """Enqueue sync tasks for all Google Photos connections."""

    try:
        return asyncio.run(_sync_all_connections())
    except SQLAlchemyError as exc:  # pragma: no cover - unexpected database errors
        logger.exception("Database error while enqueueing Google Photos sync: {}", exc)
        raise
    except Exception as exc:  # pragma: no cover - propagate for retry logic
        logger.exception("Unhandled exception while enqueueing Google Photos sync: {}", exc)
        raise
