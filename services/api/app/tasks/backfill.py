"""Backfill pipeline tasks for previously ingested items."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Iterable, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import exists, select

from ..celery_app import celery_app
from ..db.models import DEFAULT_TEST_USER_ID, DerivedArtifact, SourceItem
from ..db.session import isolated_session
from ..pipeline.utils import parse_iso_datetime
from .process_item import process_item


def _apply_missing_artifact_filter(stmt, artifact_type: str):
    subq = select(DerivedArtifact.id).where(
        DerivedArtifact.source_item_id == SourceItem.id,
        DerivedArtifact.artifact_type == artifact_type,
    )
    return stmt.where(~exists(subq))


async def _enqueue_backfill(
    user_id: UUID,
    limit: int,
    offset: int,
    item_type: Optional[str],
    provider: Optional[str],
    processing_statuses: Optional[Iterable[str]],
    missing_artifacts: Optional[Iterable[str]],
    since: Optional[datetime],
    until: Optional[datetime],
    reprocess_duplicates: bool,
) -> dict[str, Any]:
    async with isolated_session() as session:
        stmt = select(SourceItem).where(SourceItem.user_id == user_id)
        if processing_statuses:
            stmt = stmt.where(SourceItem.processing_status.in_(list(processing_statuses)))
        if item_type:
            stmt = stmt.where(SourceItem.item_type == item_type)
        if provider:
            stmt = stmt.where(SourceItem.provider == provider)
        if since:
            stmt = stmt.where(SourceItem.created_at >= since)
        if until:
            stmt = stmt.where(SourceItem.created_at <= until)
        if missing_artifacts:
            for artifact in missing_artifacts:
                stmt = _apply_missing_artifact_filter(stmt, artifact)

        stmt = stmt.order_by(SourceItem.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = list(result.scalars().all())

    enqueued = 0
    for item in items:
        payload = {
            "item_id": str(item.id),
            "storage_key": item.storage_key,
            "item_type": item.item_type,
            "user_id": str(item.user_id),
            "captured_at": item.captured_at.isoformat() if item.captured_at else None,
            "content_type": item.content_type,
            "original_filename": item.original_filename,
            "reprocess_duplicates": reprocess_duplicates,
        }
        process_item.delay(payload)
        enqueued += 1

    logger.info(
        "Backfill enqueued user={} count={} limit={} offset={}",
        user_id,
        enqueued,
        limit,
        offset,
    )
    return {
        "status": "enqueued",
        "user_id": str(user_id),
        "count": enqueued,
        "limit": limit,
        "offset": offset,
        "reprocess_duplicates": reprocess_duplicates,
        "missing_artifacts": list(missing_artifacts) if missing_artifacts else None,
    }


@celery_app.task(name="maintenance.backfill_pipeline")
def backfill_pipeline(
    user_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    item_type: Optional[str] = None,
    provider: Optional[str] = None,
    processing_statuses: Optional[list[str]] = None,
    missing_artifacts: Optional[list[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    reprocess_duplicates: bool = True,
) -> dict[str, Any]:
    """Enqueue pipeline processing for existing items."""

    resolved_user = UUID(user_id) if user_id else DEFAULT_TEST_USER_ID
    since_dt = parse_iso_datetime(since) if since else None
    until_dt = parse_iso_datetime(until) if until else None

    return asyncio.run(
        _enqueue_backfill(
            user_id=resolved_user,
            limit=limit,
            offset=offset,
            item_type=item_type,
            provider=provider,
            processing_statuses=processing_statuses or ["completed"],
            missing_artifacts=missing_artifacts,
            since=since_dt,
            until=until_dt,
            reprocess_duplicates=reprocess_duplicates,
        )
    )

