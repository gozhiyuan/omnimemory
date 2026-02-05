"""Timeline aggregation endpoints."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import asyncio
import json
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..auth import get_current_user_id
from ..celery_app import celery_app
from ..ai import transcribe_media
from ..config import get_settings
from ..db.models import (
    DataConnection,
    DailySummary,
    DerivedArtifact,
    ProcessedContent,
    ProcessedContext,
    SourceItem,
    TimelineDayHighlight,
)
from ..db.session import get_session
from ..google_photos import get_valid_access_token
from ..storage import get_storage_provider
from ..tasks.episodes import _update_daily_summary as refresh_daily_summary
from ..user_settings import (
    fetch_user_settings,
    resolve_language_label,
    resolve_language_code,
    resolve_user_tz_offset_minutes,
)
from ..vectorstore import delete_context_embeddings, upsert_context_embeddings
from ..pipeline.utils import build_vector_text, ensure_tz_aware, extract_keywords


router = APIRouter()


class TimelineItem(BaseModel):
    id: UUID
    item_type: str
    captured_at: Optional[str]
    processed: bool
    processing_status: str
    storage_key: str
    content_type: Optional[str]
    original_filename: Optional[str]
    caption: Optional[str]
    download_url: Optional[str]
    poster_url: Optional[str] = None


class TimelineItemsPage(BaseModel):
    items: list[TimelineItem]
    total: int
    limit: int
    offset: int


class TimelineDay(BaseModel):
    date: date
    item_count: int
    items: List[TimelineItem]
    episodes: list["TimelineEpisode"] = []
    daily_summary: Optional["TimelineDailySummary"] = None
    highlight: Optional["TimelineHighlight"] = None


class TimelineContext(BaseModel):
    context_type: str
    title: str
    summary: str
    keywords: list[str]
    entities: list
    location: dict
    processor_versions: dict


class TranscriptSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    status: Optional[str] = None
    error: Optional[str] = None


class TimelineItemDetail(BaseModel):
    id: UUID
    item_type: str
    captured_at: Optional[str]
    processed: bool
    processing_status: str
    storage_key: str
    content_type: Optional[str]
    original_filename: Optional[str]
    caption: Optional[str]
    download_url: Optional[str]
    poster_url: Optional[str] = None
    contexts: list[TimelineContext]
    transcript_text: Optional[str] = None
    transcript_segments: list[TranscriptSegment] = []


class TimelineEpisode(BaseModel):
    episode_id: str
    title: str
    summary: str
    context_type: str
    start_time_utc: Optional[str]
    end_time_utc: Optional[str]
    item_count: int
    source_item_ids: list[str]
    context_ids: list[str]
    preview_url: Optional[str] = None


class TimelineDailySummary(BaseModel):
    context_id: str
    summary_date: date
    title: str
    summary: str
    keywords: list[str]


class TimelineHighlight(BaseModel):
    item_id: str
    item_type: str
    thumbnail_url: Optional[str] = None


class DailySummaryUpdateRequest(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    keywords: Optional[list[str]] = None
    tz_offset_minutes: Optional[int] = None


class DailySummaryResetRequest(BaseModel):
    tz_offset_minutes: Optional[int] = None


class TimelineEpisodeDetail(BaseModel):
    episode_id: str
    title: str
    summary: str
    context_type: str
    start_time_utc: Optional[str]
    end_time_utc: Optional[str]
    source_item_ids: list[str]
    contexts: list[TimelineContext]
    items: list[TimelineItem]


class EpisodeUpdateRequest(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    keywords: Optional[list[str]] = None
    context_type: str = "activity_context"


class TimelineHighlightRequest(BaseModel):
    highlight_date: date
    item_id: UUID
    tz_offset_minutes: Optional[int] = None


class TimelineHighlightResponse(BaseModel):
    highlight_date: date
    item_id: UUID
    status: str


class TimelineHighlightDeleteResponse(BaseModel):
    highlight_date: date
    status: str


class DeleteResponse(BaseModel):
    item_id: UUID
    status: str


def _resolve_tz_offset_minutes(
    context: ProcessedContext,
    tz_offset_minutes: Optional[int] = None,
) -> int:
    if tz_offset_minutes is not None:
        return int(tz_offset_minutes)
    versions = context.processor_versions or {}
    if isinstance(versions, dict):
        stored = versions.get("tz_offset_minutes")
        if stored is not None:
            try:
                return int(stored)
            except (TypeError, ValueError):
                return 0
    return 0


def _stored_tz_offset_minutes(context: ProcessedContext) -> Optional[int]:
    versions = context.processor_versions or {}
    if isinstance(versions, dict):
        stored = versions.get("tz_offset_minutes")
        if stored is not None:
            try:
                return int(stored)
            except (TypeError, ValueError):
                return None
    return None


async def _resolve_request_tz_offset(
    *,
    session: AsyncSession,
    user_id: UUID,
    tz_offset_minutes: Optional[int],
    context: Optional[ProcessedContext] = None,
    local_date: Optional[date] = None,
    at: Optional[datetime] = None,
) -> int:
    if tz_offset_minutes is not None:
        return int(tz_offset_minutes)
    if context is not None:
        stored = _stored_tz_offset_minutes(context)
        if stored is not None:
            return stored
        if at is None:
            at = context.start_time_utc or context.event_time_utc or context.created_at
    return await resolve_user_tz_offset_minutes(session, user_id, at=at, local_date=local_date)


def _summary_date_from_context(
    context: ProcessedContext,
    tz_offset_minutes: Optional[int] = None,
) -> date:
    summary_time = ensure_tz_aware(context.start_time_utc or context.event_time_utc or context.created_at)
    offset_minutes = _resolve_tz_offset_minutes(context, tz_offset_minutes)
    offset = timedelta(minutes=offset_minutes)
    return (summary_time - offset).date()


def _summary_window(summary_date: date, tz_offset_minutes: int) -> tuple[datetime, datetime]:
    offset = timedelta(minutes=tz_offset_minutes)
    start = datetime.combine(summary_date, time.min, tzinfo=timezone.utc) + offset
    end = start + timedelta(days=1)
    return start, end


async def _load_daily_summary_context(
    context_id: str,
    user_id: UUID,
    session: AsyncSession,
) -> ProcessedContext:
    try:
        context_uuid = UUID(context_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Daily summary not found")
    context = await session.get(ProcessedContext, context_uuid)
    if not context or context.user_id != user_id or context.context_type != "daily_summary":
        raise HTTPException(status_code=404, detail="Daily summary not found")
    return context


async def _persist_daily_summary_update(
    *,
    context: ProcessedContext,
    user_id: UUID,
    session: AsyncSession,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    tz_offset_minutes: Optional[int] = None,
    mark_edited: bool = True,
) -> TimelineDailySummary:
    if title is not None:
        context.title = title
    if summary is not None:
        context.summary = summary
    if summary is not None and keywords is None:
        keywords = extract_keywords(summary)
    if keywords is not None:
        context.keywords = keywords

    processor_versions = context.processor_versions or {}
    existing_summary_date: Optional[date] = None
    existing_edited = False
    existing_offset: Optional[int] = None
    if isinstance(processor_versions, dict):
        date_value = processor_versions.get("daily_summary_date")
        if date_value:
            try:
                existing_summary_date = date.fromisoformat(date_value)
            except ValueError:
                existing_summary_date = None
        existing_edited = bool(processor_versions.get("edited_by_user"))
        stored_offset = processor_versions.get("tz_offset_minutes")
        if stored_offset is not None:
            try:
                existing_offset = int(stored_offset)
            except (TypeError, ValueError):
                existing_offset = None

    resolved_offset = _resolve_tz_offset_minutes(context, tz_offset_minutes)
    should_align_window = tz_offset_minutes is not None or existing_offset is not None
    summary_date = _summary_date_from_context(context, tz_offset_minutes=resolved_offset)
    if should_align_window:
        start, end = _summary_window(summary_date, resolved_offset)
        context.event_time_utc = start
        context.start_time_utc = start
        context.end_time_utc = end

    context.vector_text = build_vector_text(
        context.title,
        context.summary,
        context.keywords or [],
        context_type=context.context_type,
    )
    if isinstance(processor_versions, dict):
        is_user_edit = mark_edited or existing_edited
        if is_user_edit:
            processor_versions["edited_by_user"] = True
        else:
            processor_versions.pop("edited_by_user", None)
        processor_versions["daily_summary_date"] = summary_date.isoformat()
        if should_align_window:
            processor_versions["tz_offset_minutes"] = resolved_offset
    context.processor_versions = processor_versions
    await session.flush()
    try:
        upsert_context_embeddings([context])
    except Exception as exc:  # pragma: no cover - external service dependency
        logger.warning("Daily summary embedding update failed for {}: {}", context.id, exc)

    metadata: dict = {}
    existing_summary = await session.execute(
        select(DailySummary).where(
            DailySummary.user_id == user_id,
            DailySummary.summary_date == summary_date,
        )
    )
    existing = existing_summary.scalar_one_or_none()
    if existing and isinstance(existing.summary_metadata, dict):
        metadata.update(existing.summary_metadata)
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    if mark_edited or existing_edited:
        metadata["edited_by_user"] = True
    else:
        metadata.pop("edited_by_user", None)
    if should_align_window:
        metadata["tz_offset_minutes"] = resolved_offset

    daily_table = DailySummary.__table__
    daily_upsert = insert(daily_table).values(
        {
            daily_table.c.user_id: user_id,
            daily_table.c.summary_date: summary_date,
            daily_table.c.summary: context.summary,
            daily_table.c.metadata: metadata,
        }
    )
    daily_upsert = daily_upsert.on_conflict_do_update(
        index_elements=[daily_table.c.user_id, daily_table.c.summary_date],
        set_={
            daily_table.c.summary: context.summary,
            daily_table.c.metadata: metadata,
        },
    )
    await session.execute(daily_upsert)
    if existing_summary_date and existing_summary_date != summary_date:
        await session.execute(
            delete(DailySummary).where(
                DailySummary.user_id == user_id,
                DailySummary.summary_date == existing_summary_date,
            )
        )
    await session.commit()

    return TimelineDailySummary(
        context_id=str(context.id),
        summary_date=summary_date,
        title=context.title,
        summary=context.summary,
        keywords=context.keywords or [],
    )


WEB_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_IMAGE_EXT_TO_TYPE = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def _infer_image_content_type(item: SourceItem) -> Optional[str]:
    """Infer image content type from filename or storage key."""
    candidates = [item.original_filename or "", item.storage_key or ""]
    for value in candidates:
        if not value or "." not in value:
            continue
        ext = value.rsplit(".", 1)[-1].lower()
        inferred = _IMAGE_EXT_TO_TYPE.get(ext)
        if inferred:
            return inferred
    return None


def _is_web_image(item: SourceItem) -> bool:
    content_type = (item.content_type or "").lower()
    if content_type in WEB_IMAGE_TYPES:
        return True
    inferred = _infer_image_content_type(item)
    return bool(inferred and inferred in WEB_IMAGE_TYPES)


@router.get("/", response_model=list[TimelineDay])
async def get_timeline(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    limit: int = 200,
    provider: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    tz_offset_minutes: Optional[int] = None,
) -> list[TimelineDay]:
    """Return a grouped timeline of items for the user.

    Items are ordered by capture timestamp (falling back to creation time) and
    grouped by their calendar date. A modest limit keeps payloads predictable
    for UI rendering while still surfacing recent activity.
    """

    offset_minutes = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
        local_date=start_date or end_date,
    )
    offset = timedelta(minutes=offset_minutes)

    stmt = select(SourceItem).where(
        SourceItem.user_id == user_id,
        SourceItem.processing_status == "completed",
    )
    event_time_expr = func.coalesce(SourceItem.event_time_utc, SourceItem.created_at)
    if start_date:
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
        stmt = stmt.where(event_time_expr >= start_dt)
    if end_date:
        end_dt = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)
        stmt = stmt.where(event_time_expr < end_dt)
    if provider:
        stmt = (
            stmt.join(DataConnection, SourceItem.connection_id == DataConnection.id)
            .where(DataConnection.provider == provider)
        )
    stmt = stmt.order_by(SourceItem.event_time_utc.desc().nulls_last(), SourceItem.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    items: list[SourceItem] = list(result.scalars().all())

    highlight_stmt = select(TimelineDayHighlight).where(TimelineDayHighlight.user_id == user_id)
    if start_date:
        highlight_stmt = highlight_stmt.where(TimelineDayHighlight.highlight_date >= start_date)
    if end_date:
        highlight_stmt = highlight_stmt.where(TimelineDayHighlight.highlight_date <= end_date)
    highlight_rows = await session.execute(highlight_stmt)
    highlight_entries = list(highlight_rows.scalars().all())
    highlights_by_date = {entry.highlight_date: entry for entry in highlight_entries}
    highlight_item_ids = [entry.source_item_id for entry in highlight_entries]

    item_ids = [item.id for item in items]
    lookup_item_ids = list(dict.fromkeys(item_ids + highlight_item_ids))
    items_by_id = {item.id: item for item in items}
    highlight_items_by_id: dict[UUID, SourceItem] = {}
    missing_highlight_ids = [item_id for item_id in highlight_item_ids if item_id not in items_by_id]
    if missing_highlight_ids:
        highlight_item_rows = await session.execute(
            select(SourceItem).where(SourceItem.id.in_(missing_highlight_ids))
        )
        highlight_items_by_id = {item.id: item for item in highlight_item_rows.scalars().all()}

    captions: dict[UUID, str] = {}
    context_summaries: dict[UUID, str] = {}
    preview_keys: dict[UUID, str] = {}
    keyframe_keys: dict[UUID, str] = {}
    if item_ids:
        caption_stmt = select(ProcessedContent.item_id, ProcessedContent.data).where(
            ProcessedContent.item_id.in_(item_ids),
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
            ProcessedContext.source_item_ids.overlap(item_ids),
        )
        context_rows = await session.execute(context_stmt)
        for context in context_rows.scalars().all():
            for source_id in context.source_item_ids:
                existing = context_summaries.get(source_id)
                if existing and context.context_type != "activity_context":
                    continue
                if source_id not in context_summaries or context.context_type == "activity_context":
                    context_summaries[source_id] = context.summary

    if lookup_item_ids:
        preview_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
            DerivedArtifact.source_item_id.in_(lookup_item_ids),
            DerivedArtifact.artifact_type == "preview_image",
        )
        preview_rows = await session.execute(preview_stmt)
        for row in preview_rows.fetchall():
            payload = row.payload or {}
            if payload.get("status") == "ok" and payload.get("storage_key"):
                preview_keys[row.source_item_id] = payload["storage_key"]

        keyframe_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
            DerivedArtifact.source_item_id.in_(lookup_item_ids),
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

    settings = get_settings()
    storage = get_storage_provider()

    async def sign_url(storage_key: str) -> Optional[str]:
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
    connections: dict[UUID, DataConnection] = {}
    tokens: dict[UUID, str] = {}
    all_items_by_id = dict(items_by_id)
    all_items_by_id.update(highlight_items_by_id)
    items_for_signing = list(all_items_by_id.values())
    if items_for_signing:
        connection_ids = [
            getattr(item, "connection_id", None)
            for item in items_for_signing
            if getattr(item, "connection_id", None)
        ]
        if connection_ids:
            conn_rows = await session.execute(select(DataConnection).where(DataConnection.id.in_(connection_ids)))
            connections = {conn.id: conn for conn in conn_rows.scalars().all()}
            http_connection_ids = {
                item.connection_id
                for item in items_for_signing
                if item.connection_id
                and item.storage_key
                and item.storage_key.startswith(("http://", "https://"))
            }
            google_photos_connections = [
                connections[conn_id]
                for conn_id in http_connection_ids
                if conn_id in connections and connections[conn_id].provider == "google_photos"
            ]
            for conn in google_photos_connections:
                token = await get_valid_access_token(session, conn)
                if token:
                    tokens[conn.id] = token

    async def download_url_for(item: SourceItem, storage_override: Optional[str]) -> Optional[str]:
        storage_key = storage_override or item.storage_key
        if storage_key.startswith("http://") or storage_key.startswith("https://"):
            conn_id = getattr(item, "connection_id", None)
            token = tokens.get(conn_id) if conn_id else None
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

    if items:
        signed_list = await asyncio.gather(
            *(
                download_url_for(
                    item,
                    preview_keys.get(item.id)
                    if item.item_type == "photo" and not _is_web_image(item)
                    else None,
                )
                for item in items
            ),
            return_exceptions=False,
        )
        download_urls = {item.id: url for item, url in zip(items, signed_list)}

    poster_candidates = [
        (item.id, keyframe_keys.get(item.id))
        for item in items
        if item.item_type == "video" and keyframe_keys.get(item.id)
    ]
    if poster_candidates:
        poster_signed = await asyncio.gather(
            *(sign_url(key) for _, key in poster_candidates),
            return_exceptions=False,
        )
        poster_urls = {
            item_id: url for (item_id, _), url in zip(poster_candidates, poster_signed)
        }

    async def build_thumbnail(item: SourceItem) -> Optional[str]:
        if item.item_type == "photo":
            if _is_web_image(item):
                return await download_url_for(item, None)
            preview_key = preview_keys.get(item.id)
            if preview_key:
                return await download_url_for(item, preview_key)
            return None
        if item.item_type == "video":
            key = keyframe_keys.get(item.id)
            if key:
                return await sign_url(key)
        return None

    highlight_thumbnails: dict[UUID, Optional[str]] = {}
    if highlight_item_ids:
        highlight_items = [
            all_items_by_id[item_id]
            for item_id in highlight_item_ids
            if item_id in all_items_by_id
        ]
        if highlight_items:
            highlight_signed = await asyncio.gather(
                *(build_thumbnail(item) for item in highlight_items),
                return_exceptions=False,
            )
            highlight_thumbnails = {
                item.id: url for item, url in zip(highlight_items, highlight_signed)
            }

    item_by_id = {item.id: item for item in items}

    def episode_preview(source_ids: list[str]) -> Optional[str]:
        candidates: list[tuple[datetime, SourceItem]] = []
        for source_id in source_ids:
            try:
                item_id = UUID(source_id)
            except Exception:
                continue
            item = item_by_id.get(item_id)
            if not item:
                continue
            time_value = item.event_time_utc or item.captured_at or item.created_at
            if time_value:
                time_value = ensure_tz_aware(time_value)
            else:
                time_value = datetime.min.replace(tzinfo=timezone.utc)
            candidates.append((time_value, item))
        candidates.sort(key=lambda pair: pair[0])
        for _, item in candidates:
            if item.item_type == "photo":
                url = download_urls.get(item.id)
            elif item.item_type == "video":
                url = poster_urls.get(item.id)
            else:
                url = None
            if url:
                return url
        return None

    episodes_by_date: dict[date, list[TimelineEpisode]] = defaultdict(list)
    episode_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(True),
        ProcessedContext.context_type != "daily_summary",
    )
    episode_time_expr = func.coalesce(ProcessedContext.start_time_utc, ProcessedContext.event_time_utc)
    if start_date:
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
        episode_stmt = episode_stmt.where(episode_time_expr >= start_dt)
    if end_date:
        end_dt = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)
        episode_stmt = episode_stmt.where(episode_time_expr < end_dt)
    episode_rows = await session.execute(episode_stmt)
    episode_contexts = list(episode_rows.scalars().all())
    episode_groups: dict[str, list[ProcessedContext]] = defaultdict(list)
    for context in episode_contexts:
        versions = context.processor_versions or {}
        episode_id = None
        if isinstance(versions, dict):
            episode_id = versions.get("episode_id")
        episode_key = str(episode_id) if episode_id else str(context.id)
        episode_groups[episode_key].append(context)

    for episode_id, contexts in episode_groups.items():
        if not contexts:
            continue
        primary = next((ctx for ctx in contexts if ctx.context_type == "activity_context"), contexts[0])
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
        source_item_ids: list[str] = []
        for ctx in contexts:
            for source_id in ctx.source_item_ids:
                source_item_ids.append(str(source_id))
        source_item_ids = list(dict.fromkeys(source_item_ids))
        preview_url = episode_preview(source_item_ids)
        local_start = start_time - offset
        item_date = local_start.date()
        episodes_by_date[item_date].append(
            TimelineEpisode(
                episode_id=episode_id,
                title=primary.title,
                summary=primary.summary,
                context_type=primary.context_type,
                start_time_utc=start_time.isoformat(),
                end_time_utc=end_time.isoformat(),
                item_count=len(source_item_ids),
                source_item_ids=source_item_ids,
                context_ids=[str(ctx.id) for ctx in contexts],
                preview_url=preview_url,
            )
        )

    daily_summaries_by_date: dict[date, TimelineDailySummary] = {}
    daily_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(True),
        ProcessedContext.context_type == "daily_summary",
    )
    daily_time_expr = func.coalesce(ProcessedContext.start_time_utc, ProcessedContext.event_time_utc)
    if start_date:
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
        daily_stmt = daily_stmt.where(daily_time_expr >= start_dt)
    if end_date:
        end_dt = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)
        daily_stmt = daily_stmt.where(daily_time_expr < end_dt)
    daily_stmt = daily_stmt.order_by(ProcessedContext.created_at.desc())
    daily_rows = await session.execute(daily_stmt)
    for context in daily_rows.scalars().all():
        base_time = ensure_tz_aware(context.start_time_utc or context.event_time_utc or context.created_at)
        local_date = (base_time - offset).date()
        if local_date in daily_summaries_by_date:
            continue
        title = context.title or "Daily summary"
        if title.startswith("Daily summary - "):
            title = f"Daily summary - {local_date.isoformat()}"
        daily_summaries_by_date[local_date] = TimelineDailySummary(
            context_id=str(context.id),
            summary_date=local_date,
            title=title,
            summary=context.summary or "",
            keywords=context.keywords or [],
        )

    grouped: dict[date, list[SourceItem]] = defaultdict(list)
    for item in items:
        event_time = item.event_time_utc or item.captured_at or item.created_at
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        local_time = event_time - offset
        item_date = local_time.date()
        grouped[item_date].append(item)

    timeline: list[TimelineDay] = []
    for day in sorted(grouped.keys(), reverse=True):
        day_items = grouped[day]
        highlight_entry = highlights_by_date.get(day)
        highlight: Optional[TimelineHighlight] = None
        if highlight_entry:
            highlight_item = items_by_id.get(highlight_entry.source_item_id) or highlight_items_by_id.get(
                highlight_entry.source_item_id
            )
            if highlight_item:
                highlight = TimelineHighlight(
                    item_id=str(highlight_item.id),
                    item_type=highlight_item.item_type,
                    thumbnail_url=highlight_thumbnails.get(highlight_item.id),
                )
        timeline.append(
            TimelineDay(
                date=day,
                item_count=len(day_items),
                items=[
                    TimelineItem(
                        id=item.id,
                        item_type=item.item_type,
                        captured_at=(item.event_time_utc or item.captured_at or item.created_at).isoformat(),
                        processed=item.processing_status == "completed",
                        processing_status=item.processing_status,
                        storage_key=item.storage_key,
                        content_type=item.content_type,
                        original_filename=item.original_filename,
                        caption=context_summaries.get(item.id) or captions.get(item.id),
                        download_url=download_urls.get(item.id),
                        poster_url=poster_urls.get(item.id),
                    )
                    for item in day_items
                ],
                episodes=sorted(
                    episodes_by_date.get(day, []),
                    key=lambda episode: episode.start_time_utc or "",
                    reverse=False,
                ),
                daily_summary=daily_summaries_by_date.get(day),
                highlight=highlight,
            )
        )

    return timeline


@router.patch("/daily-summaries/{context_id}", response_model=TimelineDailySummary)
async def update_daily_summary(
    context_id: str,
    payload: DailySummaryUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineDailySummary:
    context = await _load_daily_summary_context(context_id, user_id, session)
    stored_offset = _stored_tz_offset_minutes(context)
    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=payload.tz_offset_minutes,
        context=context,
    )
    if payload.title is None and payload.summary is None and payload.keywords is None:
        if payload.tz_offset_minutes is not None or stored_offset is None:
            return await _persist_daily_summary_update(
                context=context,
                user_id=user_id,
                session=session,
                tz_offset_minutes=resolved_offset,
                mark_edited=False,
            )
        return TimelineDailySummary(
            context_id=str(context.id),
            summary_date=_summary_date_from_context(context, tz_offset_minutes=resolved_offset),
            title=context.title,
            summary=context.summary,
            keywords=context.keywords or [],
        )

    return await _persist_daily_summary_update(
        context=context,
        user_id=user_id,
        session=session,
        title=payload.title,
        summary=payload.summary,
        keywords=payload.keywords,
        tz_offset_minutes=resolved_offset,
    )


@router.post("/daily-summaries/{context_id}/voice", response_model=TimelineDailySummary)
async def update_daily_summary_from_voice(
    context_id: str,
    file: UploadFile = File(...),
    tz_offset_minutes: Optional[int] = Form(None),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineDailySummary:
    context = await _load_daily_summary_context(context_id, user_id, session)
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Empty audio payload")

    settings = get_settings()
    user_settings = await fetch_user_settings(session, user_id)
    language = resolve_language_label(resolve_language_code(user_settings))
    transcript = await transcribe_media(
        blob,
        settings,
        content_type=file.content_type,
        media_kind="audio",
        user_id=user_id,
        item_id=context.id,
        step_name="daily_summary_voice",
        language=language,
    )
    if transcript.get("status") != "ok":
        reason = transcript.get("reason") or transcript.get("error") or "transcription_failed"
        raise HTTPException(status_code=502, detail=f"Voice transcription failed ({reason})")
    text = (transcript.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="No transcript returned")

    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
        context=context,
    )
    return await _persist_daily_summary_update(
        context=context,
        user_id=user_id,
        session=session,
        summary=text,
        tz_offset_minutes=resolved_offset,
    )


@router.post("/daily-summaries/{context_id}/reset", response_model=TimelineDailySummary)
async def reset_daily_summary(
    context_id: str,
    payload: DailySummaryResetRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineDailySummary:
    context = await _load_daily_summary_context(context_id, user_id, session)
    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=payload.tz_offset_minutes,
        context=context,
    )
    summary_date = _summary_date_from_context(context, tz_offset_minutes=resolved_offset)
    await refresh_daily_summary(
        session,
        user_id,
        summary_date,
        tz_offset_minutes=resolved_offset,
        context_id=context.id,
        force_regen=True,
    )
    await session.commit()
    refreshed = await session.get(ProcessedContext, context.id)
    if not refreshed:
        raise HTTPException(status_code=404, detail="Daily summary not found")
    return TimelineDailySummary(
        context_id=str(refreshed.id),
        summary_date=_summary_date_from_context(refreshed, tz_offset_minutes=resolved_offset),
        title=refreshed.title,
        summary=refreshed.summary,
        keywords=refreshed.keywords or [],
    )


@router.post("/highlights", response_model=TimelineHighlightResponse)
async def set_timeline_highlight(
    request: TimelineHighlightRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineHighlightResponse:
    item = await session.get(SourceItem, request.item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    offset_minutes = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=request.tz_offset_minutes,
        local_date=request.highlight_date,
        at=item.event_time_utc or item.captured_at or item.created_at,
    )
    offset = timedelta(minutes=offset_minutes)
    item_time = ensure_tz_aware(item.event_time_utc or item.captured_at or item.created_at)
    local_date = (item_time - offset).date()
    if local_date != request.highlight_date:
        raise HTTPException(status_code=400, detail="Item does not belong to the highlight date")

    stmt = select(TimelineDayHighlight).where(
        TimelineDayHighlight.user_id == user_id,
        TimelineDayHighlight.highlight_date == request.highlight_date,
    )
    rows = await session.execute(stmt)
    highlight = rows.scalars().first()
    now = datetime.now(timezone.utc)
    if highlight:
        highlight.source_item_id = item.id
        highlight.updated_at = now
    else:
        highlight = TimelineDayHighlight(
            user_id=user_id,
            highlight_date=request.highlight_date,
            source_item_id=item.id,
            created_at=now,
            updated_at=now,
        )
        session.add(highlight)
    await session.commit()

    return TimelineHighlightResponse(
        highlight_date=request.highlight_date,
        item_id=item.id,
        status="ok",
    )


@router.delete("/highlights/{highlight_date}", response_model=TimelineHighlightDeleteResponse)
async def clear_timeline_highlight(
    highlight_date: date,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineHighlightDeleteResponse:
    stmt = select(TimelineDayHighlight).where(
        TimelineDayHighlight.user_id == user_id,
        TimelineDayHighlight.highlight_date == highlight_date,
    )
    rows = await session.execute(stmt)
    highlight = rows.scalars().first()
    if highlight:
        await session.delete(highlight)
        await session.commit()
        status = "cleared"
    else:
        status = "missing"

    return TimelineHighlightDeleteResponse(
        highlight_date=highlight_date,
        status=status,
    )


@router.get("/items", response_model=TimelineItemsPage)
async def get_timeline_items(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
    offset: int = 0,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    provider: Optional[str] = None,
    tz_offset_minutes: Optional[int] = None,
) -> TimelineItemsPage:
    offset_minutes = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
        local_date=start_date or end_date,
    )
    offset_delta = timedelta(minutes=offset_minutes)

    event_time_expr = func.coalesce(SourceItem.event_time_utc, SourceItem.created_at)
    filters = [
        SourceItem.user_id == user_id,
        SourceItem.processing_status == "completed",
    ]
    if start_date:
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset_delta
        filters.append(event_time_expr >= start_dt)
    if end_date:
        end_dt = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset_delta + timedelta(days=1)
        filters.append(event_time_expr < end_dt)

    total_stmt = select(func.count(SourceItem.id)).select_from(SourceItem)
    if provider:
        total_stmt = total_stmt.join(DataConnection, SourceItem.connection_id == DataConnection.id).where(
            DataConnection.provider == provider
        )
    total_stmt = total_stmt.where(*filters)
    total = (await session.execute(total_stmt)).scalar_one()

    stmt = select(SourceItem).where(*filters)
    if provider:
        stmt = stmt.join(DataConnection, SourceItem.connection_id == DataConnection.id).where(
            DataConnection.provider == provider
        )
    stmt = (
        stmt.order_by(SourceItem.event_time_utc.desc().nulls_last(), SourceItem.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    items: list[SourceItem] = list(result.scalars().all())

    captions: dict[UUID, str] = {}
    context_summaries: dict[UUID, str] = {}
    preview_keys: dict[UUID, str] = {}
    keyframe_keys: dict[UUID, str] = {}
    if items:
        item_ids = [item.id for item in items]
        caption_stmt = select(ProcessedContent.item_id, ProcessedContent.data).where(
            ProcessedContent.item_id.in_(item_ids),
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
            ProcessedContext.source_item_ids.overlap(item_ids),
        )
        context_rows = await session.execute(context_stmt)
        for context in context_rows.scalars().all():
            for source_id in context.source_item_ids:
                existing = context_summaries.get(source_id)
                if existing and context.context_type != "activity_context":
                    continue
                if source_id not in context_summaries or context.context_type == "activity_context":
                    context_summaries[source_id] = context.summary

        preview_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
            DerivedArtifact.source_item_id.in_(item_ids),
            DerivedArtifact.artifact_type == "preview_image",
        )
        preview_rows = await session.execute(preview_stmt)
        for row in preview_rows.fetchall():
            payload = row.payload or {}
            if payload.get("status") == "ok" and payload.get("storage_key"):
                preview_keys[row.source_item_id] = payload["storage_key"]

        keyframe_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
            DerivedArtifact.source_item_id.in_(item_ids),
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

    settings = get_settings()
    storage = get_storage_provider()

    connections: dict[UUID, DataConnection] = {}
    tokens: dict[UUID, str] = {}
    if items:
        connection_ids = [getattr(item, "connection_id", None) for item in items if getattr(item, "connection_id", None)]
        if connection_ids:
            conn_rows = await session.execute(select(DataConnection).where(DataConnection.id.in_(connection_ids)))
            connections = {conn.id: conn for conn in conn_rows.scalars().all()}
            http_connection_ids = {
                item.connection_id
                for item in items
                if item.connection_id
                and item.storage_key
                and item.storage_key.startswith(("http://", "https://"))
            }
            google_photos_connections = [
                connections[conn_id]
                for conn_id in http_connection_ids
                if conn_id in connections and connections[conn_id].provider == "google_photos"
            ]
            for conn in google_photos_connections:
                token = await get_valid_access_token(session, conn)
                if token:
                    tokens[conn.id] = token

    async def sign_url(storage_key: str) -> Optional[str]:
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

    async def download_url_for(item: SourceItem, storage_override: Optional[str]) -> Optional[str]:
        storage_key = storage_override or item.storage_key
        if storage_key.startswith("http://") or storage_key.startswith("https://"):
            conn_id = getattr(item, "connection_id", None)
            token = tokens.get(conn_id) if conn_id else None
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
    if items:
        signed_list = await asyncio.gather(
            *(
                download_url_for(
                    item,
                    preview_keys.get(item.id)
                    if item.item_type == "photo" and not _is_web_image(item)
                    else None,
                )
                for item in items
            ),
            return_exceptions=False,
        )
        download_urls = {item.id: url for item, url in zip(items, signed_list)}

    poster_urls: dict[UUID, Optional[str]] = {}
    poster_candidates = [
        (item.id, keyframe_keys.get(item.id))
        for item in items
        if item.item_type == "video" and keyframe_keys.get(item.id)
    ]
    if poster_candidates:
        poster_signed = await asyncio.gather(
            *(sign_url(key) for _, key in poster_candidates),
            return_exceptions=False,
        )
        poster_urls = {
            item_id: url for (item_id, _), url in zip(poster_candidates, poster_signed)
        }

    return TimelineItemsPage(
        items=[
            TimelineItem(
                id=item.id,
                item_type=item.item_type,
                captured_at=(item.event_time_utc or item.captured_at or item.created_at).isoformat(),
                processed=item.processing_status == "completed",
                processing_status=item.processing_status,
                storage_key=item.storage_key,
                content_type=item.content_type,
                original_filename=item.original_filename,
                caption=context_summaries.get(item.id) or captions.get(item.id),
                download_url=download_urls.get(item.id),
                poster_url=poster_urls.get(item.id),
            )
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/items/{item_id}", response_model=TimelineItemDetail)
async def get_timeline_item_detail(
    item_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineItemDetail:
    item = await session.get(SourceItem, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    settings = get_settings()
    storage = get_storage_provider()

    async def sign_url(storage_key: str) -> Optional[str]:
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

    download_url: Optional[str] = None
    storage_key = item.storage_key
    if storage_key.startswith(("http://", "https://")):
        token = None
        if item.connection_id:
            connection = await session.get(DataConnection, item.connection_id)
            if connection and connection.provider == "google_photos":
                token = await get_valid_access_token(session, connection)
        if token:
            sep = "&" if "?" in storage_key else "?"
            download_url = f"{storage_key}{sep}access_token={token}"
        else:
            download_url = storage_key
    else:
        download_url = await sign_url(storage_key)

    if item.item_type == "photo" and not _is_web_image(item):
        preview_stmt = (
            select(DerivedArtifact.payload)
            .where(
                DerivedArtifact.source_item_id == item.id,
                DerivedArtifact.artifact_type == "preview_image",
            )
            .order_by(DerivedArtifact.created_at.desc())
            .limit(1)
        )
        preview_row = await session.execute(preview_stmt)
        preview_payload = preview_row.scalar_one_or_none()
        if isinstance(preview_payload, dict):
            preview_key = preview_payload.get("storage_key")
            if preview_payload.get("status") == "ok" and preview_key:
                preview_url = await sign_url(preview_key)
                if preview_url:
                    download_url = preview_url

    poster_url: Optional[str] = None
    keyframe_stmt = (
        select(DerivedArtifact.payload)
        .where(
            DerivedArtifact.source_item_id == item.id,
            DerivedArtifact.artifact_type == "keyframes",
        )
        .order_by(DerivedArtifact.created_at.desc())
        .limit(1)
    )
    keyframe_row = await session.execute(keyframe_stmt)
    keyframe_payload = keyframe_row.scalar_one_or_none()
    if isinstance(keyframe_payload, dict):
        poster = keyframe_payload.get("poster")
        if isinstance(poster, dict) and poster.get("storage_key"):
            poster_url = await sign_url(poster["storage_key"])
        elif keyframe_payload.get("frames"):
            frames = keyframe_payload.get("frames") or []
            first = frames[0] if frames else None
            if isinstance(first, dict) and first.get("storage_key"):
                poster_url = await sign_url(first["storage_key"])

    caption = None
    caption_stmt = select(ProcessedContent.data).where(
        ProcessedContent.item_id == item.id,
        ProcessedContent.content_role == "caption",
    )
    caption_row = await session.execute(caption_stmt)
    caption_payload = caption_row.scalar_one_or_none()
    if isinstance(caption_payload, dict):
        caption = caption_payload.get("text")

    context_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(False),
        ProcessedContext.source_item_ids.contains([item.id]),
    )
    context_rows = await session.execute(context_stmt)
    context_records = list(context_rows.scalars().all())

    def context_sort_key(context: ProcessedContext) -> tuple[int, int]:
        versions = context.processor_versions or {}
        if isinstance(versions, dict) and versions.get("media_summary"):
            return (0, -1)
        chunk_index = None
        if isinstance(versions, dict):
            chunk_index = versions.get("chunk_index")
        if isinstance(chunk_index, int):
            return (1, chunk_index)
        return (2, 0)

    context_records.sort(key=context_sort_key)
    contexts = [
        TimelineContext(
            context_type=context.context_type,
            title=context.title,
            summary=context.summary,
            keywords=context.keywords or [],
            entities=context.entities or [],
            location=context.location or {},
            processor_versions=context.processor_versions or {},
        )
        for context in context_records
    ]

    transcript_text: Optional[str] = None
    transcript_segments: list[TranscriptSegment] = []
    transcript_stmt = (
        select(DerivedArtifact)
        .where(
            DerivedArtifact.source_item_id == item.id,
            DerivedArtifact.artifact_type == "transcription",
        )
        .order_by(DerivedArtifact.created_at.desc())
        .limit(1)
    )
    transcript_row = await session.execute(transcript_stmt)
    transcript_artifact = transcript_row.scalar_one_or_none()
    transcript_payload = transcript_artifact.payload if transcript_artifact else None
    if isinstance(transcript_payload, dict):
        storage_key = transcript_payload.get("storage_key")
        if storage_key:
            try:
                raw = await asyncio.to_thread(storage.fetch, storage_key)
                transcript_payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:  # pragma: no cover - external storage dependency
                logger.warning("Transcript fetch failed for item {}: {}", item.id, exc)
        transcript_text = transcript_payload.get("text") if isinstance(transcript_payload, dict) else None
        segments = transcript_payload.get("segments") if isinstance(transcript_payload, dict) else None
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                transcript_segments.append(
                    TranscriptSegment(
                        start_ms=int(segment.get("start_ms") or 0),
                        end_ms=int(segment.get("end_ms") or 0),
                        text=str(segment.get("text") or ""),
                        status=segment.get("status"),
                        error=segment.get("error"),
                    )
                )

    return TimelineItemDetail(
        id=item.id,
        item_type=item.item_type,
        captured_at=(item.event_time_utc or item.captured_at or item.created_at).isoformat(),
        processed=item.processing_status == "completed",
        processing_status=item.processing_status,
        storage_key=item.storage_key,
        content_type=item.content_type,
        original_filename=item.original_filename,
        caption=caption,
        download_url=download_url,
        poster_url=poster_url,
        contexts=contexts,
        transcript_text=transcript_text,
        transcript_segments=transcript_segments,
    )


@router.get("/episodes/{episode_id}", response_model=TimelineEpisodeDetail)
async def get_timeline_episode_detail(
    episode_id: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineEpisodeDetail:
    episode_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(True),
        ProcessedContext.processor_versions["episode_id"].astext == episode_id,
    )
    episode_rows = await session.execute(episode_stmt)
    episode_contexts = list(episode_rows.scalars().all())
    if not episode_contexts:
        try:
            episode_uuid = UUID(episode_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=404, detail="Episode not found")
        fallback = await session.get(ProcessedContext, episode_uuid)
        if not fallback or not fallback.is_episode or fallback.user_id != user_id:
            raise HTTPException(status_code=404, detail="Episode not found")
        episode_contexts = [fallback]

    primary = next(
        (ctx for ctx in episode_contexts if ctx.context_type == "activity_context"),
        episode_contexts[0],
    )
    start_time = min(
        ensure_tz_aware(ctx.start_time_utc or ctx.event_time_utc or ctx.created_at)
        for ctx in episode_contexts
    )
    end_time = max(
        ensure_tz_aware(ctx.end_time_utc or ctx.event_time_utc or ctx.created_at)
        for ctx in episode_contexts
    )
    source_item_ids: list[UUID] = []
    for ctx in episode_contexts:
        for source_id in ctx.source_item_ids:
            source_item_ids.append(source_id)
    source_item_ids = list(dict.fromkeys(source_item_ids))

    settings = get_settings()
    storage = get_storage_provider()

    async def sign_url(storage_key: str) -> Optional[str]:
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

    items: list[SourceItem] = []
    if source_item_ids:
        item_stmt = select(SourceItem).where(SourceItem.id.in_(source_item_ids))
        item_rows = await session.execute(item_stmt)
        items = list(item_rows.scalars().all())

    download_urls: dict[UUID, Optional[str]] = {}
    poster_urls: dict[UUID, Optional[str]] = {}
    preview_keys: dict[UUID, str] = {}
    keyframe_keys: dict[UUID, str] = {}

    if items:
        item_ids = [item.id for item in items]
        preview_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
            DerivedArtifact.source_item_id.in_(item_ids),
            DerivedArtifact.artifact_type == "preview_image",
        )
        preview_rows = await session.execute(preview_stmt)
        for row in preview_rows.fetchall():
            payload = row.payload or {}
            if payload.get("status") == "ok" and payload.get("storage_key"):
                preview_keys[row.source_item_id] = payload["storage_key"]

        keyframe_stmt = select(DerivedArtifact.source_item_id, DerivedArtifact.payload).where(
            DerivedArtifact.source_item_id.in_(item_ids),
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

    connections: dict[UUID, DataConnection] = {}
    tokens: dict[UUID, str] = {}
    if items:
        connection_ids = [getattr(item, "connection_id", None) for item in items if getattr(item, "connection_id", None)]
        if connection_ids:
            conn_rows = await session.execute(select(DataConnection).where(DataConnection.id.in_(connection_ids)))
            connections = {conn.id: conn for conn in conn_rows.scalars().all()}
            http_connection_ids = {
                item.connection_id
                for item in items
                if item.connection_id
                and item.storage_key
                and item.storage_key.startswith(("http://", "https://"))
            }
            google_photos_connections = [
                connections[conn_id]
                for conn_id in http_connection_ids
                if conn_id in connections and connections[conn_id].provider == "google_photos"
            ]
            for conn in google_photos_connections:
                token = await get_valid_access_token(session, conn)
                if token:
                    tokens[conn.id] = token

    async def download_url_for(item: SourceItem) -> Optional[str]:
        if item.item_type == "photo" and not _is_web_image(item):
            preview_key = preview_keys.get(item.id)
            if preview_key:
                preview_url = await sign_url(preview_key)
                if preview_url:
                    return preview_url
        storage_key = item.storage_key
        if storage_key.startswith("http://") or storage_key.startswith("https://"):
            conn_id = getattr(item, "connection_id", None)
            token = tokens.get(conn_id) if conn_id else None
            if token:
                sep = "&" if "?" in storage_key else "?"
                return f"{storage_key}{sep}access_token={token}"
            return storage_key
        return await sign_url(storage_key)

    if items:
        signed_list = await asyncio.gather(*(download_url_for(item) for item in items))
        download_urls = {item.id: url for item, url in zip(items, signed_list)}

    poster_candidates = [
        (item.id, keyframe_keys.get(item.id))
        for item in items
        if item.item_type == "video" and keyframe_keys.get(item.id)
    ]
    if poster_candidates:
        poster_signed = await asyncio.gather(*(sign_url(key) for _, key in poster_candidates))
        poster_urls = {item_id: url for (item_id, _), url in zip(poster_candidates, poster_signed)}

    contexts = [
        TimelineContext(
            context_type=context.context_type,
            title=context.title,
            summary=context.summary,
            keywords=context.keywords or [],
            entities=context.entities or [],
            location=context.location or {},
            processor_versions=context.processor_versions or {},
        )
        for context in episode_contexts
    ]

    item_payloads = [
        TimelineItem(
            id=item.id,
            item_type=item.item_type,
            captured_at=(item.event_time_utc or item.captured_at or item.created_at).isoformat(),
            processed=item.processing_status == "completed",
            processing_status=item.processing_status,
            storage_key=item.storage_key,
            content_type=item.content_type,
            original_filename=item.original_filename,
            caption=None,
            download_url=download_urls.get(item.id),
            poster_url=poster_urls.get(item.id),
        )
        for item in items
    ]

    return TimelineEpisodeDetail(
        episode_id=episode_id,
        title=primary.title,
        summary=primary.summary,
        context_type=primary.context_type,
        start_time_utc=start_time.isoformat(),
        end_time_utc=end_time.isoformat(),
        source_item_ids=[str(value) for value in source_item_ids],
        contexts=contexts,
        items=item_payloads,
    )


@router.patch("/episodes/{episode_id}", response_model=TimelineEpisodeDetail)
async def update_episode_detail(
    episode_id: str,
    payload: EpisodeUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineEpisodeDetail:
    context_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(True),
        ProcessedContext.context_type == payload.context_type,
        ProcessedContext.processor_versions["episode_id"].astext == episode_id,
    )
    context_rows = await session.execute(context_stmt)
    context = context_rows.scalar_one_or_none()
    if context is None:
        raise HTTPException(status_code=404, detail="Episode context not found")

    if payload.title is not None:
        context.title = payload.title
    if payload.summary is not None:
        context.summary = payload.summary
    if payload.keywords is not None:
        context.keywords = payload.keywords
    context.vector_text = build_vector_text(
        context.title,
        context.summary,
        context.keywords or [],
        context_type=context.context_type,
    )
    processor_versions = context.processor_versions or {}
    if isinstance(processor_versions, dict):
        processor_versions["edited_by_user"] = True
    context.processor_versions = processor_versions
    await session.flush()
    try:
        upsert_context_embeddings([context])
    except Exception as exc:  # pragma: no cover - external service dependency
        logger.warning("Episode embedding update failed for {}: {}", episode_id, exc)
    await session.commit()

    summary_time = context.start_time_utc or context.event_time_utc or context.created_at
    if summary_time:
        summary_date = ensure_tz_aware(summary_time).date()
        celery_app.send_task(
            "episodes.update_daily_summary",
            args=[str(user_id), summary_date.isoformat()],
        )

    return await get_timeline_episode_detail(episode_id, user_id, session)


@router.delete("/items/{item_id}", response_model=DeleteResponse)
async def delete_timeline_item(
    item_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> DeleteResponse:
    item = await session.get(SourceItem, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    storage = get_storage_provider()
    storage_keys = []
    if item.storage_key and not item.storage_key.startswith(("http://", "https://")):
        storage_keys.append(item.storage_key)

    affected_episode_items: dict[str, UUID] = {}
    affected_dates: set[date] = set()
    event_time = item.event_time_utc or item.captured_at or item.created_at
    if event_time:
        affected_dates.add(ensure_tz_aware(event_time).date())

    preview_stmt = select(DerivedArtifact).where(
        DerivedArtifact.source_item_id == item.id,
        DerivedArtifact.artifact_type.in_(["preview_image", "keyframes", "video_preview"]),
    )
    preview_rows = await session.execute(preview_stmt)
    for preview in preview_rows.scalars().all():
        if preview.storage_key:
            storage_keys.append(preview.storage_key)
        payload = preview.payload or {}
        if preview.artifact_type == "keyframes":
            frames = payload.get("frames") if isinstance(payload, dict) else None
            if isinstance(frames, list):
                for frame in frames:
                    if isinstance(frame, dict) and frame.get("storage_key"):
                        storage_keys.append(frame["storage_key"])
            poster = payload.get("poster") if isinstance(payload, dict) else None
            if isinstance(poster, dict) and poster.get("storage_key"):
                storage_keys.append(poster["storage_key"])

    updated_contexts: list[ProcessedContext] = []
    deleted_context_ids: list[str] = []
    context_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.source_item_ids.contains([item.id]),
    )
    context_rows = await session.execute(context_stmt)
    for context in context_rows.scalars().all():
        remaining = [value for value in context.source_item_ids if value != item.id]
        if context.is_episode and context.start_time_utc:
            affected_dates.add(ensure_tz_aware(context.start_time_utc).date())
        if not remaining:
            deleted_context_ids.append(str(context.id))
            await session.delete(context)
        else:
            context.source_item_ids = remaining
            updated_contexts.append(context)
            if context.is_episode and context.context_type != "daily_summary":
                versions = context.processor_versions or {}
                episode_id = versions.get("episode_id") if isinstance(versions, dict) else None
                episode_key = str(episode_id) if episode_id else str(context.id)
                affected_episode_items.setdefault(episode_key, remaining[0])

    canonical_stmt = select(SourceItem).where(
        SourceItem.user_id == user_id,
        SourceItem.canonical_item_id == item.id,
        SourceItem.id != item.id,
    )
    canonical_rows = await session.execute(canonical_stmt)
    canonical_items = canonical_rows.scalars().all()

    dup_items = []
    if item.content_hash:
        dup_stmt = (
            select(SourceItem)
            .where(
                SourceItem.user_id == user_id,
                SourceItem.content_hash == item.content_hash,
                SourceItem.id != item.id,
            )
            .order_by(SourceItem.created_at.asc())
        )
        dup_rows = await session.execute(dup_stmt)
        dup_items = dup_rows.scalars().all()
        if dup_items:
            canonical = dup_items[0].canonical_item_id or dup_items[0].id
            for dup_item in dup_items:
                dup_item.canonical_item_id = canonical

    dup_ids = {dup_item.id for dup_item in dup_items}
    for canonical_item in canonical_items:
        if canonical_item.id in dup_ids:
            continue
        canonical_item.canonical_item_id = canonical_item.id

    await session.delete(item)
    await session.commit()

    for remaining_item_id in affected_episode_items.values():
        celery_app.send_task("episodes.update_for_item", args=[str(remaining_item_id)])
    for summary_date in affected_dates:
        celery_app.send_task(
            "episodes.update_daily_summary",
            args=[str(user_id), summary_date.isoformat()],
        )

    for key in storage_keys:
        try:
            storage.delete(key)
        except Exception as exc:  # pragma: no cover - external storage dependency
            logger.warning("Failed to delete storage key {}: {}", key, exc)

    if deleted_context_ids:
        try:
            delete_context_embeddings(deleted_context_ids)
        except Exception as exc:  # pragma: no cover - external service dependency
            logger.warning("Failed to delete embeddings: {}", exc)
    if updated_contexts:
        try:
            upsert_context_embeddings(updated_contexts)
        except Exception as exc:  # pragma: no cover - external service dependency
            logger.warning("Failed to refresh embeddings: {}", exc)

    return DeleteResponse(item_id=item_id, status="deleted")
