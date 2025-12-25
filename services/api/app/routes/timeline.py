"""Timeline aggregation endpoints."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import asyncio
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..config import get_settings
from ..db.models import DEFAULT_TEST_USER_ID, ProcessedContent, SourceItem
from ..db.session import get_session
from ..storage import get_storage_provider


router = APIRouter()


class TimelineItem(BaseModel):
    id: UUID
    item_type: str
    captured_at: Optional[str]
    processed: bool
    storage_key: str
    content_type: Optional[str]
    original_filename: Optional[str]
    caption: Optional[str]
    download_url: Optional[str]


class TimelineDay(BaseModel):
    date: date
    item_count: int
    items: List[TimelineItem]


@router.get("/", response_model=list[TimelineDay])
async def get_timeline(
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
    limit: int = 200,
) -> list[TimelineDay]:
    """Return a grouped timeline of items for the user.

    Items are ordered by capture timestamp (falling back to creation time) and
    grouped by their calendar date. A modest limit keeps payloads predictable
    for UI rendering while still surfacing recent activity.
    """

    stmt = (
        select(SourceItem)
        .where(
            SourceItem.user_id == user_id,
            SourceItem.processing_status == "completed",
        )
        .order_by(SourceItem.captured_at.desc().nulls_last(), SourceItem.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    items: list[SourceItem] = list(result.scalars().all())

    captions: dict[UUID, str] = {}
    if items:
        caption_stmt = select(ProcessedContent.item_id, ProcessedContent.data).where(
            ProcessedContent.item_id.in_([item.id for item in items]),
            ProcessedContent.content_role == "caption",
        )
        caption_rows = await session.execute(caption_stmt)
        captions = {
            row.item_id: (row.data or {}).get("text")
            for row in caption_rows.fetchall()
            if row.data
        }

    settings = get_settings()
    storage = get_storage_provider()

    async def sign_url(storage_key: str) -> Optional[str]:
        try:
            signed = await asyncio.to_thread(
                storage.get_presigned_download, storage_key, settings.presigned_url_ttl_seconds
            )
        except Exception as exc:  # pragma: no cover - external service dependency
            logger.warning("Failed to sign download URL for {}: {}", storage_key, exc)
            return None
        return signed.get("url") if signed else None

    download_urls: dict[UUID, Optional[str]] = {}
    if items:
        signed_list = await asyncio.gather(
            *(sign_url(item.storage_key) for item in items),
            return_exceptions=False,
        )
        download_urls = {item.id: url for item, url in zip(items, signed_list)}

    grouped: dict[date, list[SourceItem]] = defaultdict(list)
    for item in items:
        item_date = (item.captured_at or item.created_at).date()
        grouped[item_date].append(item)

    timeline: list[TimelineDay] = []
    for day in sorted(grouped.keys(), reverse=True):
        day_items = grouped[day]
        timeline.append(
            TimelineDay(
                date=day,
                item_count=len(day_items),
                items=[
                    TimelineItem(
                        id=item.id,
                        item_type=item.item_type,
                        captured_at=(item.captured_at or item.created_at).isoformat(),
                        processed=item.processing_status == "completed",
                        storage_key=item.storage_key,
                        content_type=item.content_type,
                        original_filename=item.original_filename,
                        caption=captions.get(item.id),
                        download_url=download_urls.get(item.id),
                    )
                    for item in day_items
                ],
            )
        )

    return timeline
