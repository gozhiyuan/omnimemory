"""Dashboard summary endpoints."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..config import get_settings
from ..db.models import DEFAULT_TEST_USER_ID, DataConnection, ProcessedContent, SourceItem
from ..db.session import get_session
from ..storage import get_storage_provider


router = APIRouter()


class DashboardActivityPoint(BaseModel):
    date: date
    count: int


class DashboardRecentItem(BaseModel):
    id: UUID
    item_type: str
    captured_at: Optional[str] = None
    processed: bool
    storage_key: str
    content_type: Optional[str] = None
    original_filename: Optional[str] = None
    caption: Optional[str] = None
    download_url: Optional[str] = None


class DashboardStats(BaseModel):
    total_items: int
    processed_items: int
    failed_items: int
    active_connections: int
    uploads_last_7_days: int
    storage_used_bytes: int
    recent_items: list[DashboardRecentItem]
    activity: list[DashboardActivityPoint]


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> DashboardStats:
    """Return aggregate counts used by the dashboard cards."""

    since = datetime.utcnow() - timedelta(days=7)
    total_items_stmt = select(func.count(SourceItem.id)).where(SourceItem.user_id == user_id)
    processed_items_stmt = total_items_stmt.where(SourceItem.processing_status == "completed")
    failed_items_stmt = total_items_stmt.where(SourceItem.processing_status == "failed")
    connections_stmt = select(func.count(DataConnection.id)).where(
        DataConnection.user_id == user_id, DataConnection.status == "active"
    )
    uploads_last_week_stmt = total_items_stmt.where(SourceItem.created_at >= since)

    storage_sum_stmt = (
        select(
            func.coalesce(
                func.sum(cast(ProcessedContent.data["size_bytes"].astext, Integer)), 0
            )
        )
        .join(SourceItem, SourceItem.id == ProcessedContent.item_id)
        .where(
            SourceItem.user_id == user_id,
            ProcessedContent.content_role == "metadata",
        )
    )

    recent_items_stmt = (
        select(SourceItem)
        .where(SourceItem.user_id == user_id)
        .order_by(SourceItem.created_at.desc())
        .limit(5)
    )

    activity_stmt = (
        select(func.date(SourceItem.created_at).label("day"), func.count(SourceItem.id))
        .where(SourceItem.user_id == user_id, SourceItem.created_at >= since)
        .group_by("day")
        .order_by("day")
    )

    total_items = (await session.execute(total_items_stmt)).scalar_one()
    processed_items = (await session.execute(processed_items_stmt)).scalar_one()
    failed_items = (await session.execute(failed_items_stmt)).scalar_one()
    active_connections = (await session.execute(connections_stmt)).scalar_one()
    uploads_last_7_days = (await session.execute(uploads_last_week_stmt)).scalar_one()
    storage_used_bytes = (await session.execute(storage_sum_stmt)).scalar_one() or 0

    recent_items_result = await session.execute(recent_items_stmt)
    recent_items = list(recent_items_result.scalars().all())

    captions: dict[UUID, str] = {}
    if recent_items:
        caption_stmt = select(ProcessedContent.item_id, ProcessedContent.data).where(
            ProcessedContent.item_id.in_([item.id for item in recent_items]),
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
    if recent_items:
        signed_list = await asyncio.gather(
            *(sign_url(item.storage_key) for item in recent_items),
            return_exceptions=False,
        )
        download_urls = {item.id: url for item, url in zip(recent_items, signed_list)}

    activity_rows = await session.execute(activity_stmt)
    activity_by_day = {row.day: row[1] for row in activity_rows.fetchall()}
    activity: list[DashboardActivityPoint] = []
    for i in range(6, -1, -1):
        day = (date.today() - timedelta(days=i))
        activity.append(DashboardActivityPoint(date=day, count=activity_by_day.get(day, 0)))

    return DashboardStats(
        total_items=total_items,
        processed_items=processed_items,
        failed_items=failed_items,
        active_connections=active_connections,
        uploads_last_7_days=uploads_last_7_days,
        storage_used_bytes=storage_used_bytes,
        recent_items=[
            DashboardRecentItem(
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
            for item in recent_items
        ],
        activity=activity,
    )
