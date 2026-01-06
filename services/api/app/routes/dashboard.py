"""Dashboard summary endpoints."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..auth import get_current_user_id
from ..cache import get_cache_json, set_cache_json
from ..config import get_settings
from ..db.models import (
    AiUsageEvent,
    DataConnection,
    DerivedArtifact,
    ProcessedContent,
    ProcessedContext,
    SourceItem,
)
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
    poster_url: Optional[str] = None


class UsageTotals(BaseModel):
    prompt_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float


class UsageDailyPoint(BaseModel):
    date: date
    total_tokens: int
    cost_usd: float


class DashboardStats(BaseModel):
    total_items: int
    processed_items: int
    failed_items: int
    active_connections: int
    uploads_last_7_days: int
    storage_used_bytes: int
    recent_items: list[DashboardRecentItem]
    activity: list[DashboardActivityPoint]
    usage_this_week: UsageTotals
    usage_all_time: UsageTotals
    usage_daily: list[UsageDailyPoint]


def _build_date_range(start_day: date, end_day: date) -> list[date]:
    days: list[date] = []
    cursor = start_day
    while cursor <= end_day:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> DashboardStats:
    """Return aggregate counts used by the dashboard cards."""

    settings = get_settings()
    since = datetime.utcnow() - timedelta(days=7)
    range_end = end_date or date.today()
    range_start = start_date or (range_end - timedelta(days=6))
    if range_start > range_end:
        range_start, range_end = range_end, range_start
    cache_key = f"dashboard:stats:v1:{user_id}:{range_start.isoformat()}:{range_end.isoformat()}"
    if settings.dashboard_cache_ttl_seconds > 0:
        cached = await get_cache_json(cache_key)
        if cached:
            return DashboardStats.model_validate(cached)
    range_start_dt = datetime.combine(range_start, time.min, tzinfo=timezone.utc)
    range_end_dt = datetime.combine(range_end, time.min, tzinfo=timezone.utc) + timedelta(days=1)
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
            SourceItem.created_at >= range_start_dt,
            SourceItem.created_at < range_end_dt,
        )
        .group_by("day")
        .order_by("day")
    )

    usage_week_stmt = select(
        func.coalesce(func.sum(AiUsageEvent.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AiUsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(AiUsageEvent.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(AiUsageEvent.cost_usd), 0.0).label("cost_usd"),
    ).where(
        AiUsageEvent.user_id == user_id,
        AiUsageEvent.created_at >= since,
    )

    usage_all_time_stmt = select(
        func.coalesce(func.sum(AiUsageEvent.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AiUsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(AiUsageEvent.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(AiUsageEvent.cost_usd), 0.0).label("cost_usd"),
    ).where(AiUsageEvent.user_id == user_id)

    usage_daily_stmt = (
        select(
            func.date(AiUsageEvent.created_at).label("day"),
            func.coalesce(func.sum(AiUsageEvent.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AiUsageEvent.cost_usd), 0.0).label("cost_usd"),
        )
        .where(
            AiUsageEvent.user_id == user_id,
            AiUsageEvent.created_at >= range_start_dt,
            AiUsageEvent.created_at < range_end_dt,
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

    usage_week_row = (await session.execute(usage_week_stmt)).one()
    usage_all_time_row = (await session.execute(usage_all_time_stmt)).one()

    recent_items_result = await session.execute(recent_items_stmt)
    recent_items = list(recent_items_result.scalars().all())

    captions: dict[UUID, str] = {}
    context_summaries: dict[UUID, str] = {}
    keyframe_keys: dict[UUID, str] = {}
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

        keyframe_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
            DerivedArtifact.source_item_id.in_(recent_ids),
            DerivedArtifact.artifact_type == "keyframes",
        )
        keyframe_rows = await session.execute(keyframe_stmt)
        for row in keyframe_rows.fetchall():
            payload = row.payload or {}
            if not isinstance(payload, dict):
                continue
            poster = payload.get("poster")
            if isinstance(poster, dict) and poster.get("storage_key"):
                keyframe_keys[row.source_item_id] = poster["storage_key"]
                continue
            frames = payload.get("frames")
            if isinstance(frames, list) and frames:
                first = frames[0]
                if isinstance(first, dict) and first.get("storage_key"):
                    keyframe_keys[row.source_item_id] = first["storage_key"]

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

    async def sign_storage_key(storage_key: str) -> Optional[str]:
        if storage_key.startswith("http://") or storage_key.startswith("https://"):
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
    poster_urls: dict[UUID, Optional[str]] = {}
    if recent_items:
        signed_list = await asyncio.gather(
            *(build_url(item) for item in recent_items),
            return_exceptions=False,
        )
        download_urls = {item.id: url for item, url in zip(recent_items, signed_list)}

        poster_candidates = [
            (item.id, keyframe_keys.get(item.id))
            for item in recent_items
            if item.item_type == "video" and keyframe_keys.get(item.id)
        ]
        if poster_candidates:
            poster_signed = await asyncio.gather(
                *(sign_storage_key(key) for _, key in poster_candidates),
                return_exceptions=False,
            )
            poster_urls = {
                item_id: url for (item_id, _), url in zip(poster_candidates, poster_signed)
            }

    activity_rows = await session.execute(activity_stmt)
    activity_by_day = {row.day: row[1] for row in activity_rows.fetchall()}
    activity: list[DashboardActivityPoint] = []
    for day in _build_date_range(range_start, range_end):
        activity.append(DashboardActivityPoint(date=day, count=activity_by_day.get(day, 0)))

    usage_rows = await session.execute(usage_daily_stmt)
    usage_by_day = {
        row.day: {
            "total_tokens": row.total_tokens or 0,
            "cost_usd": float(row.cost_usd or 0),
        }
        for row in usage_rows.fetchall()
    }
    usage_daily: list[UsageDailyPoint] = []
    for day in _build_date_range(range_start, range_end):
        usage = usage_by_day.get(day, {"total_tokens": 0, "cost_usd": 0.0})
        usage_daily.append(
            UsageDailyPoint(
                date=day,
                total_tokens=int(usage["total_tokens"]),
                cost_usd=float(usage["cost_usd"]),
            )
        )

    stats = DashboardStats(
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
                poster_url=poster_urls.get(item.id),
            )
            for item in recent_items
        ],
        activity=activity,
        usage_this_week=UsageTotals(
            prompt_tokens=int(usage_week_row.prompt_tokens or 0),
            output_tokens=int(usage_week_row.output_tokens or 0),
            total_tokens=int(usage_week_row.total_tokens or 0),
            cost_usd=float(usage_week_row.cost_usd or 0),
        ),
        usage_all_time=UsageTotals(
            prompt_tokens=int(usage_all_time_row.prompt_tokens or 0),
            output_tokens=int(usage_all_time_row.output_tokens or 0),
            total_tokens=int(usage_all_time_row.total_tokens or 0),
            cost_usd=float(usage_all_time_row.cost_usd or 0),
        ),
        usage_daily=usage_daily,
    )
    if settings.dashboard_cache_ttl_seconds > 0:
        await set_cache_json(cache_key, stats.model_dump(), settings.dashboard_cache_ttl_seconds)
    return stats
