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
from ..db.models import DEFAULT_TEST_USER_ID, DataConnection, ProcessedContent, ProcessedContext, SourceItem
from ..db.session import get_session
from ..google_photos import get_valid_access_token
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
    total_items_stmt = select(func.count(SourceItem.id)).where(
        SourceItem.user_id == user_id,
        SourceItem.processing_status == "completed",
    )
    processed_items_stmt = total_items_stmt
    failed_items_stmt = select(func.count(SourceItem.id)).where(
        SourceItem.user_id == user_id,
        SourceItem.processing_status == "failed",
    )
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
        .where(
            SourceItem.user_id == user_id,
            SourceItem.processing_status == "completed",
        )
        .order_by(SourceItem.event_time_utc.desc().nulls_last(), SourceItem.created_at.desc())
        .limit(5)
    )

    activity_stmt = (
        select(func.date(SourceItem.created_at).label("day"), func.count(SourceItem.id))
        .where(
            SourceItem.user_id == user_id,
            SourceItem.processing_status == "completed",
            SourceItem.created_at >= since,
        )
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
    context_summaries: dict[UUID, str] = {}
    if recent_items:
        recent_ids = [item.id for item in recent_items]
        caption_stmt = select(ProcessedContent.item_id, ProcessedContent.data).where(
            ProcessedContent.item_id.in_(recent_ids),
            ProcessedContent.content_role == "caption",
        )
        caption_rows = await session.execute(caption_stmt)
        captions = {
            row.item_id: (row.data or {}).get("text")
            for row in caption_rows.fetchall()
            if row.data
        }

        context_stmt = select(ProcessedContext).where(
            ProcessedContext.user_id == user_id,
            ProcessedContext.is_episode.is_(False),
            ProcessedContext.source_item_ids.overlap(recent_ids),
        )
        context_rows = await session.execute(context_stmt)
        for context in context_rows.scalars().all():
            for source_id in context.source_item_ids:
                existing = context_summaries.get(source_id)
                if existing and context.context_type != "activity_context":
                    continue
                if source_id not in context_summaries or context.context_type == "activity_context":
                    context_summaries[source_id] = context.summary

    settings = get_settings()
    storage = get_storage_provider()

    connections: dict[UUID, DataConnection] = {}
    if recent_items:
        connection_ids = [
            getattr(item, "connection_id", None) for item in recent_items if getattr(item, "connection_id", None)
        ]
        if connection_ids:
            conn_rows = await session.execute(select(DataConnection).where(DataConnection.id.in_(connection_ids)))
            connections = {conn.id: conn for conn in conn_rows.scalars().all()}

    async def build_url(item: SourceItem) -> Optional[str]:
        storage_key = item.storage_key
        if storage_key.startswith("http://") or storage_key.startswith("https://"):
            conn_id = getattr(item, "connection_id", None)
            conn = connections.get(conn_id) if conn_id else None
            if conn and conn.provider == "google_photos":
                token = await get_valid_access_token(session, conn)
                if token:
                    sep = "&" if "?" in storage_key else "?"
                    return f"{storage_key}{sep}access_token={token}"
            return storage_key
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
            *(build_url(item) for item in recent_items),
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
                captured_at=(item.event_time_utc or item.captured_at or item.created_at).isoformat(),
                processed=item.processing_status == "completed",
                storage_key=item.storage_key,
                content_type=item.content_type,
                original_filename=item.original_filename,
                caption=context_summaries.get(item.id) or captions.get(item.id),
                download_url=download_urls.get(item.id),
            )
            for item in recent_items
        ],
        activity=activity,
    )
