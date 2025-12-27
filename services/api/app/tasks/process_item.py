"""Primary media processing task implementation."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from ..celery_app import celery_app
from ..db.models import DataConnection, ProcessedContent, SourceItem
from ..db.session import isolated_session
from ..google_photos import get_valid_access_token
from ..storage import get_storage_provider
from ..vectorstore import upsert_random_embedding


async def _upsert_content(session, item_id: UUID, role: str, data: Dict[str, Any]) -> None:
    await session.execute(
        delete(ProcessedContent).where(
            ProcessedContent.item_id == item_id,
            ProcessedContent.content_role == role,
        )
    )
    session.add(ProcessedContent(item_id=item_id, content_role=role, data=data))


def _extract_metadata(blob: bytes, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "size_bytes": len(blob),
        "item_type": payload.get("item_type"),
        "captured_at": payload.get("captured_at"),
        "storage_key": payload.get("storage_key"),
        "processed_at": datetime.utcnow().isoformat(),
    }


def _generate_caption(item: SourceItem, metadata: Dict[str, Any]) -> Dict[str, Any]:
    text = f"Auto-generated caption for {item.item_type} item {item.id}"
    return {"text": text, "summary": text, "metadata": metadata}


def _generate_transcription(item: SourceItem) -> Dict[str, Any]:
    text = f"Placeholder transcription for item {item.id}"
    return {"text": text}


def _generate_ocr(item: SourceItem) -> Dict[str, Any]:
    text = f"Placeholder OCR text for item {item.id}"
    return {"text": text}


async def _fetch_item_blob(session, storage, item: SourceItem) -> bytes:
    storage_key = item.storage_key
    if storage_key.startswith("http://") or storage_key.startswith("https://"):
        headers = {}
        if item.connection_id:
            connection = await session.get(DataConnection, item.connection_id)
            if connection and connection.provider == "google_photos":
                access_token = await get_valid_access_token(session, connection)
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(storage_key, headers=headers)
            response.raise_for_status()
            return response.content
    return storage.fetch(storage_key)


async def _process_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    storage = get_storage_provider()
    remote_only = bool(payload.get("remote_only"))

    try:
        item_id = UUID(str(payload["item_id"]))
    except (KeyError, ValueError) as exc:  # pragma: no cover - validation guard
        raise ValueError("process_item payload missing valid item_id") from exc

    logger.info("Processing item {}", item_id)

    metadata: Dict[str, Any] = {}
    caption: Dict[str, Any] = {}

    # Use an isolated engine per task to avoid asyncpg cross-loop/concurrency issues.
    async with isolated_session() as session:
        item = await session.get(SourceItem, item_id)
        if item is None:
            raise ValueError(f"source item {item_id} not found")

        if payload.get("content_type") and not item.content_type:
            item.content_type = payload["content_type"]
        if payload.get("original_filename") and not item.original_filename:
            item.original_filename = payload["original_filename"]

        item.processing_status = "processing"
        item.processing_error = None
        await session.flush()

        try:
            if remote_only:
                # Remote-only sources avoid storing blobs in object storage.
                blob = b""
            else:
                blob = await _fetch_item_blob(session, storage, item)
            metadata = _extract_metadata(blob, payload)
            if remote_only:
                metadata["remote_only"] = True
            caption = _generate_caption(item, metadata)
            transcription = _generate_transcription(item)
            ocr = _generate_ocr(item)

            await _upsert_content(session, item.id, "metadata", metadata)
            await _upsert_content(session, item.id, "caption", caption)
            await _upsert_content(session, item.id, "transcription", transcription)
            await _upsert_content(session, item.id, "ocr", ocr)

            item.processing_status = "completed"
            item.processed_at = datetime.utcnow()
            await session.commit()
        except Exception as exc:
            await session.rollback()
            item.processing_status = "failed"
            item.processing_error = str(exc)
            session.add(item)
            await session.commit()
            logger.exception("Processing failed for item {}", item_id)
            raise

    try:
        await asyncio.to_thread(
            upsert_random_embedding,
            item_id,
            {
                "item_id": str(item_id),
                "caption": caption.get("text"),
                "metadata": metadata,
            },
        )
    except Exception as exc:  # pragma: no cover - external service dependency
        logger.warning("Failed to upsert embedding for item {}: {}", item_id, exc)

    return {
        "status": "completed",
        "item_id": str(item_id),
        "processed_at": metadata["processed_at"],
    }


@celery_app.task(name="pipeline.process_item", bind=True)
def process_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process an uploaded item into derived artifacts."""

    try:
        return asyncio.run(_process_payload(payload))
    except SQLAlchemyError as exc:  # pragma: no cover - unexpected database errors
        logger.exception("Database error while processing item: {}", exc)
        raise
    except Exception as exc:  # pragma: no cover - propagate to retry logic
        logger.exception("Unhandled exception in process_item: {}", exc)
        raise
