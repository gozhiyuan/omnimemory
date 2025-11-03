"""Primary media processing task skeleton."""

from __future__ import annotations

import time
from typing import Any, Dict

from loguru import logger

from ..celery_app import celery_app


@celery_app.task(name="pipeline.process_item", bind=True)
def process_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process an uploaded item into derived artifacts.

    The payload is expected to contain:
        - item_id: UUID from the database
        - storage_key: location of the original asset
        - item_type: photo | video | audio | document
        - user_id: owner reference
    """

    start = time.perf_counter()
    item_id = payload.get("item_id")
    storage_key = payload.get("storage_key")
    item_type = payload.get("item_type")

    logger.info(
        "Processing item task received: item_id={} storage_key={} item_type={}",
        item_id,
        storage_key,
        item_type,
    )

    # TODO: download from storage, run OCR/caption/transcription, persist output, update Qdrant
    time.sleep(0.1)

    elapsed = time.perf_counter() - start
    logger.info("Processing item completed item_id={} duration={:.3f}s", item_id, elapsed)

    return {
        "status": "completed",
        "item_id": item_id,
        "duration_s": elapsed,
    }

