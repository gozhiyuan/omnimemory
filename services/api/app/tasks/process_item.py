"""Primary media processing task implementation."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from ..celery_app import celery_app
from ..db.models import SourceItem
from ..db.session import isolated_session
from ..pipeline import run_pipeline
from ..pipeline.utils import parse_iso_datetime


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
