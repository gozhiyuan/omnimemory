"""OpenClaw integration endpoints.

Provides optimized API endpoints for OpenClaw tool consumption.
These endpoints return concise, tool-friendly response formats.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..auth import get_current_user_id
from ..config import get_settings
from ..db.models import (
    DerivedArtifact,
    ProcessedContext,
    SourceItem,
)
from ..db.session import get_session
from ..storage import get_storage_provider
from ..vectorstore import search_contexts
from ..pipeline.utils import ensure_tz_aware


router = APIRouter()


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------


class OpenClawSearchRequest(BaseModel):
    """Search request optimized for OpenClaw tools."""

    query: str
    date_from: Optional[str] = None  # ISO format YYYY-MM-DD
    date_to: Optional[str] = None
    context_types: Optional[list[str]] = None
    limit: int = 10


class OpenClawMemoryItem(BaseModel):
    """Memory item optimized for tool response."""

    id: str
    title: str
    summary: str  # Truncated to 500 chars
    date: Optional[str]
    type: str
    thumbnail_url: Optional[str]
    keywords: list[str]
    score: Optional[float] = None


class OpenClawSearchResponse(BaseModel):
    """Search response for OpenClaw tools."""

    success: bool = True
    total: int
    items: list[OpenClawMemoryItem]


class OpenClawEpisode(BaseModel):
    """Episode summary for timeline response."""

    title: str
    time_range: str
    summary: str
    item_count: int


class OpenClawTimelineResponse(BaseModel):
    """Day timeline response for OpenClaw tools."""

    success: bool = True
    date: str
    daily_summary: Optional[str]
    episode_count: int
    episodes: list[OpenClawEpisode]
    highlights: list[str]


class OpenClawIngestRequest(BaseModel):
    """Ingest request from OpenClaw."""

    storage_key: str
    item_type: str  # photo, video, audio
    captured_at: Optional[str] = None  # ISO format
    provider: str = "openclaw"


class OpenClawIngestResponse(BaseModel):
    """Ingest response for OpenClaw tools."""

    success: bool
    item_id: Optional[str] = None
    message: str


class OpenClawConnectionTestResponse(BaseModel):
    """Connection test response."""

    success: bool
    message: str
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/search", response_model=OpenClawSearchResponse)
async def search_for_openclaw(
    request: OpenClawSearchRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> OpenClawSearchResponse:
    """Search memories optimized for OpenClaw tool consumption.

    Returns truncated summaries and thumbnail URLs for efficient display.
    """
    # Parse date filters
    filter_start: Optional[datetime] = None
    filter_end: Optional[datetime] = None

    if request.date_from:
        try:
            start_date = date.fromisoformat(request.date_from)
            filter_start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        except ValueError:
            pass

    if request.date_to:
        try:
            end_date = date.fromisoformat(request.date_to)
            filter_end = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            pass

    # Search contexts using vector search
    results = search_contexts(
        request.query,
        limit=request.limit,
        user_id=str(user_id),
        is_episode=True,  # Prefer episodes first
        start_time=filter_start,
        end_time=filter_end,
    )

    # Fallback to non-episode contexts if needed
    if len(results) < request.limit:
        fallback = search_contexts(
            request.query,
            limit=request.limit * 2,
            user_id=str(user_id),
            start_time=filter_start,
            end_time=filter_end,
        )
        seen = {result.get("context_id") for result in results}
        for entry in fallback:
            payload = entry.get("payload") or {}
            if payload.get("is_episode") is True:
                continue
            context_id = entry.get("context_id")
            if context_id in seen:
                continue
            results.append(entry)
            seen.add(context_id)
            if len(results) >= request.limit:
                break

    # Fetch context details from database
    context_ids: list[UUID] = []
    for result in results:
        try:
            context_ids.append(UUID(result["context_id"]))
        except (KeyError, ValueError, TypeError):
            continue

    contexts_by_id: dict[UUID, ProcessedContext] = {}
    if context_ids:
        stmt = select(ProcessedContext).where(ProcessedContext.id.in_(context_ids))
        db_results = await session.execute(stmt)
        contexts_by_id = {context.id: context for context in db_results.scalars().all()}

    # Filter by context types if specified
    if request.context_types:
        contexts_by_id = {
            cid: ctx
            for cid, ctx in contexts_by_id.items()
            if ctx.context_type in request.context_types
        }

    # Get thumbnail URLs for items
    all_source_ids: set[UUID] = set()
    for context in contexts_by_id.values():
        all_source_ids.update(context.source_item_ids)

    thumbnail_urls = await _get_thumbnail_urls(session, list(all_source_ids))

    # Build response items
    items: list[OpenClawMemoryItem] = []
    for result in results:
        context_id_raw = result.get("context_id")
        try:
            context_id = UUID(context_id_raw)
        except (TypeError, ValueError):
            continue

        context = contexts_by_id.get(context_id)
        if not context:
            continue

        # Get first available thumbnail from source items
        thumbnail_url = None
        for source_id in context.source_item_ids:
            if source_id in thumbnail_urls:
                thumbnail_url = thumbnail_urls[source_id]
                break

        items.append(
            OpenClawMemoryItem(
                id=str(context.id),
                title=context.title or "Untitled",
                summary=(context.summary or "")[:500],  # Truncate for tool response
                date=context.event_time_utc.isoformat() if context.event_time_utc else None,
                type=context.context_type or "unknown",
                thumbnail_url=thumbnail_url,
                keywords=(context.keywords or [])[:10],  # Limit keywords
                score=result.get("score"),
            )
        )

    return OpenClawSearchResponse(
        success=True,
        total=len(items),
        items=items,
    )


@router.get("/timeline/{date_str}", response_model=OpenClawTimelineResponse)
async def timeline_for_openclaw(
    date_str: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    tz_offset_minutes: int = 0,
) -> OpenClawTimelineResponse:
    """Get day summary with episodes formatted for OpenClaw tools."""
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}")

    offset = timedelta(minutes=tz_offset_minutes)
    start_dt = datetime.combine(target_date, time.min, tzinfo=timezone.utc) + offset
    end_dt = start_dt + timedelta(days=1)

    # Fetch daily summary
    daily_summary_text: Optional[str] = None
    daily_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(True),
        ProcessedContext.context_type == "daily_summary",
        func.coalesce(ProcessedContext.start_time_utc, ProcessedContext.event_time_utc) >= start_dt,
        func.coalesce(ProcessedContext.start_time_utc, ProcessedContext.event_time_utc) < end_dt,
    ).order_by(ProcessedContext.created_at.desc()).limit(1)

    daily_rows = await session.execute(daily_stmt)
    daily_context = daily_rows.scalar_one_or_none()
    if daily_context:
        daily_summary_text = daily_context.summary

    # Fetch episodes for the day
    episode_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(True),
        ProcessedContext.context_type != "daily_summary",
        func.coalesce(ProcessedContext.start_time_utc, ProcessedContext.event_time_utc) >= start_dt,
        func.coalesce(ProcessedContext.start_time_utc, ProcessedContext.event_time_utc) < end_dt,
    ).order_by(ProcessedContext.start_time_utc.asc().nullslast())

    episode_rows = await session.execute(episode_stmt)
    episode_contexts = list(episode_rows.scalars().all())

    # Group by episode_id
    episode_groups: dict[str, list[ProcessedContext]] = {}
    for context in episode_contexts:
        versions = context.processor_versions or {}
        episode_id = versions.get("episode_id") if isinstance(versions, dict) else None
        episode_key = str(episode_id) if episode_id else str(context.id)
        episode_groups.setdefault(episode_key, []).append(context)

    # Build episode summaries
    episodes: list[OpenClawEpisode] = []
    highlights: list[str] = []

    for episode_id, contexts in episode_groups.items():
        if not contexts:
            continue

        primary = next(
            (ctx for ctx in contexts if ctx.context_type == "activity_context"),
            contexts[0],
        )

        start_times = [
            ensure_tz_aware(ctx.start_time_utc or ctx.event_time_utc or ctx.created_at)
            for ctx in contexts
        ]
        end_times = [
            ensure_tz_aware(ctx.end_time_utc or ctx.event_time_utc or ctx.created_at)
            for ctx in contexts
        ]
        start_time = min(start_times)
        end_time = max(end_times)

        # Count unique source items
        source_ids: set[UUID] = set()
        for ctx in contexts:
            source_ids.update(ctx.source_item_ids)

        time_range = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"

        episodes.append(
            OpenClawEpisode(
                title=primary.title or "Untitled",
                time_range=time_range,
                summary=primary.summary or "",
                item_count=len(source_ids),
            )
        )

        # Extract highlights from summaries
        if primary.summary:
            first_sentence = primary.summary.split(".")[0]
            if first_sentence and len(first_sentence) > 10:
                highlights.append(f"{primary.title}: {first_sentence}.")

    return OpenClawTimelineResponse(
        success=True,
        date=date_str,
        daily_summary=daily_summary_text,
        episode_count=len(episodes),
        episodes=episodes,
        highlights=highlights[:5],  # Limit to 5 highlights
    )


@router.post("/ingest", response_model=OpenClawIngestResponse)
async def ingest_from_openclaw(
    request: OpenClawIngestRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> OpenClawIngestResponse:
    """Ingest media uploaded via OpenClaw.

    Expects the file to already be uploaded to storage with the given storage_key.
    """
    from ..celery_app import celery_app

    # Validate item type
    if request.item_type not in ("photo", "video", "audio"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid item_type: {request.item_type}. Must be photo, video, or audio.",
        )

    # Parse captured_at if provided
    captured_at: Optional[datetime] = None
    if request.captured_at:
        try:
            captured_at = datetime.fromisoformat(request.captured_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    # Create source item
    now = datetime.now(timezone.utc)
    item = SourceItem(
        user_id=user_id,
        storage_key=request.storage_key,
        item_type=request.item_type,
        provider=request.provider,
        captured_at=captured_at,
        event_time_utc=captured_at,
        processing_status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    # Queue processing task
    celery_app.send_task(
        "process_item",
        args=[
            {
                "item_id": str(item.id),
                "user_id": str(user_id),
                "storage_key": request.storage_key,
            }
        ],
    )

    return OpenClawIngestResponse(
        success=True,
        item_id=str(item.id),
        message="Memory saved and queued for processing",
    )


@router.get("/connection/test", response_model=OpenClawConnectionTestResponse)
async def test_connection(
    user_id: UUID = Depends(get_current_user_id),
) -> OpenClawConnectionTestResponse:
    """Test connection from OpenClaw to OmniMemory.

    Returns success if authentication is valid.
    """
    return OpenClawConnectionTestResponse(
        success=True,
        message=f"Connected as user {user_id}",
        version="1.0.0",
    )


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


async def _get_thumbnail_urls(
    session: AsyncSession,
    source_item_ids: list[UUID],
) -> dict[UUID, str]:
    """Get thumbnail URLs for a list of source items."""
    if not source_item_ids:
        return {}

    settings = get_settings()
    storage = get_storage_provider()

    # Fetch preview keys
    preview_keys: dict[UUID, str] = {}
    keyframe_keys: dict[UUID, str] = {}

    preview_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
        DerivedArtifact.source_item_id.in_(source_item_ids),
        DerivedArtifact.artifact_type == "preview_image",
    )
    preview_rows = await session.execute(preview_stmt)
    for row in preview_rows.fetchall():
        payload = row.payload or {}
        if payload.get("status") == "ok" and payload.get("storage_key"):
            preview_keys[row.source_item_id] = payload["storage_key"]

    keyframe_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
        DerivedArtifact.source_item_id.in_(source_item_ids),
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

    # Sign URLs
    async def sign_url(storage_key: str) -> Optional[str]:
        if storage_key.startswith(("http://", "https://")):
            return storage_key
        try:
            signed = await asyncio.to_thread(
                storage.get_presigned_download,
                storage_key,
                settings.presigned_url_ttl_seconds,
            )
            return signed.get("url") if signed else None
        except Exception as exc:
            logger.warning("Failed to sign URL for {}: {}", storage_key, exc)
            return None

    # Combine preview and keyframe keys
    all_keys = {**preview_keys, **keyframe_keys}
    if not all_keys:
        return {}

    # Sign all URLs concurrently
    items = list(all_keys.items())
    signed_urls = await asyncio.gather(
        *(sign_url(key) for _, key in items),
        return_exceptions=True,
    )

    result: dict[UUID, str] = {}
    for (item_id, _), url in zip(items, signed_urls):
        if isinstance(url, str):
            result[item_id] = url

    return result
