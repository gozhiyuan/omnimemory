"""Timeline aggregation endpoints."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import asyncio
import json
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..config import get_settings
from ..db.models import (
    DEFAULT_TEST_USER_ID,
    DataConnection,
    DerivedArtifact,
    ProcessedContent,
    ProcessedContext,
    SourceItem,
)
from ..db.session import get_session
from ..google_photos import get_valid_access_token
from ..storage import get_storage_provider
from ..vectorstore import delete_context_embeddings, upsert_context_embeddings


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


class TimelineDay(BaseModel):
    date: date
    item_count: int
    items: List[TimelineItem]


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


class DeleteResponse(BaseModel):
    item_id: UUID
    status: str


WEB_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@router.get("/", response_model=list[TimelineDay])
async def get_timeline(
    user_id: UUID = DEFAULT_TEST_USER_ID,
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

    offset_minutes = tz_offset_minutes or 0
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
                    if item.item_type == "photo"
                    and (item.content_type or "").lower() not in WEB_IMAGE_TYPES
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
            )
        )

    return timeline


@router.get("/items/{item_id}", response_model=TimelineItemDetail)
async def get_timeline_item_detail(
    item_id: UUID,
    user_id: UUID = DEFAULT_TEST_USER_ID,
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

    if item.item_type == "photo" and (item.content_type or "").lower() not in WEB_IMAGE_TYPES:
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


@router.delete("/items/{item_id}", response_model=DeleteResponse)
async def delete_timeline_item(
    item_id: UUID,
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> DeleteResponse:
    item = await session.get(SourceItem, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    storage = get_storage_provider()
    storage_keys = []
    if item.storage_key and not item.storage_key.startswith(("http://", "https://")):
        storage_keys.append(item.storage_key)

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
        if not remaining:
            deleted_context_ids.append(str(context.id))
            await session.delete(context)
        else:
            context.source_item_ids = remaining
            updated_contexts.append(context)

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
