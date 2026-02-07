"""Primary media processing task implementation."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..celery_app import celery_app
from ..db.models import ProcessedContext, SourceItem
from ..db.session import isolated_session
from ..integrations.openclaw_sync import get_openclaw_sync
from ..pipeline import run_pipeline
from ..pipeline.utils import parse_iso_datetime
from ..user_settings import fetch_user_settings


async def _sync_openclaw_contexts_for_item(session: AsyncSession, item: SourceItem) -> None:
    """Sync non-episode memory contexts for an item to OpenClaw local memory files."""
    try:
        user_settings = await fetch_user_settings(session, item.user_id)
        openclaw_sync = get_openclaw_sync(user_settings)
        if not openclaw_sync.enabled:
            return

        stmt = select(ProcessedContext).where(
            ProcessedContext.user_id == item.user_id,
            ProcessedContext.is_episode.is_(False),
            ProcessedContext.source_item_ids.contains([item.id]),
        )
        rows = await session.execute(stmt)
        contexts = list(rows.scalars().all())
        synced = 0
        for context in contexts:
            payload = {
                "id": str(context.id),
                "title": context.title,
                "summary": context.summary,
                "keywords": context.keywords or [],
                "context_type": context.context_type,
                "event_time_utc": context.event_time_utc or context.start_time_utc,
            }
            if openclaw_sync.sync_memory_entry(user_id=str(item.user_id), context=payload):
                synced += 1
        if synced:
            logger.info("Synced {} memory context(s) for item {} to OpenClaw", synced, item.id)
    except Exception as exc:
        logger.warning("OpenClaw per-memory sync failed for item {}: {}", item.id, exc)


async def _process_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        item_id = UUID(str(payload["item_id"]))
    except (KeyError, ValueError) as exc:  # pragma: no cover - validation guard
        raise ValueError("process_item payload missing valid item_id") from exc

    logger.info("Processing item {}", item_id)

    # Use an isolated engine per task to avoid asyncpg cross-loop/concurrency issues.
    async with isolated_session() as session:
        item = await session.get(SourceItem, item_id)
        if item is None:
            raise ValueError(f"source item {item_id} not found")

        if payload.get("content_type") and not item.content_type:
            item.content_type = payload["content_type"]
        if payload.get("original_filename") and not item.original_filename:
            item.original_filename = payload["original_filename"]
        if payload.get("captured_at") and not item.captured_at:
            parsed = parse_iso_datetime(payload.get("captured_at"))
            if parsed:
                item.captured_at = parsed

        item.processing_status = "processing"
        item.processing_error = None
        await session.flush()

        try:
            await run_pipeline(session, item, payload)
            await _sync_openclaw_contexts_for_item(session, item)
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

    return {
        "status": "completed",
        "item_id": str(item_id),
        "processed_at": datetime.utcnow().isoformat(),
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
