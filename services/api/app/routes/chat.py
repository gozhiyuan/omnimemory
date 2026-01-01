"""Chat endpoints with RAG context."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import analyze_image_with_vlm, generate_image_with_gemini, summarize_text_with_gemini
from ..ai.prompts import (
    build_lifelog_cartoon_agent_prompt,
    build_lifelog_chat_system_prompt,
    build_lifelog_day_insights_agent_prompt,
    build_lifelog_image_prompt,
    build_lifelog_session_title_prompt,
)
from ..config import get_settings
from ..db.models import (
    DEFAULT_TEST_USER_ID,
    ChatAttachment,
    ChatFeedback,
    ChatMessage,
    ChatSession,
    DailySummary,
    DataConnection,
    DerivedArtifact,
    ProcessedContext,
    SourceItem,
)
from ..db.session import get_session
from ..google_photos import get_valid_access_token
from ..pipeline.utils import ensure_tz_aware
from ..rag import retrieve_context_hits
from ..storage import get_storage_provider


router = APIRouter()


WEB_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ChatSource(BaseModel):
    context_id: str
    source_item_id: Optional[str] = None
    thumbnail_url: Optional[str] = None
    timestamp: Optional[str] = None
    snippet: Optional[str] = None
    score: Optional[float] = None
    title: Optional[str] = None


class ChatAttachmentOut(BaseModel):
    id: UUID
    url: str
    content_type: Optional[str] = None
    created_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[UUID] = None
    tz_offset_minutes: Optional[int] = None
    attachment_ids: Optional[list[UUID]] = None


class ChatResponse(BaseModel):
    message: str
    session_id: UUID
    sources: list[ChatSource]


class ChatAttachmentResponse(BaseModel):
    attachment_id: UUID
    session_id: UUID
    url: str


class AgentImageRequest(BaseModel):
    session_id: Optional[UUID] = None
    date: Optional[str] = None
    prompt: Optional[str] = None
    tz_offset_minutes: Optional[int] = None


class AgentInsightsRequest(BaseModel):
    session_id: Optional[UUID] = None
    date: Optional[str] = None
    end_date: Optional[str] = None
    prompt: Optional[str] = None
    tz_offset_minutes: Optional[int] = None
    include_image: bool = True


class AgentImageResponse(BaseModel):
    message: str
    session_id: UUID
    attachments: list[ChatAttachmentOut] = []
    prompt: Optional[str] = None
    caption: Optional[str] = None


class ChatSessionSummary(BaseModel):
    session_id: UUID
    title: Optional[str] = None
    created_at: datetime
    last_message_at: datetime
    message_count: int


class ChatMessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    sources: list[ChatSource] = []
    attachments: list[ChatAttachmentOut] = []
    created_at: datetime


class ChatSessionDetail(BaseModel):
    session_id: UUID
    title: Optional[str]
    messages: list[ChatMessageOut]


class ChatFeedbackRequest(BaseModel):
    message_id: UUID
    rating: int = Field(..., ge=-1, le=1)
    comment: Optional[str] = None


def _sanitize_filename(filename: str) -> str:
    name = (filename or "").strip().split("/")[-1]
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    safe = safe.strip("._-")
    return safe or "attachment"


def _format_timestamp(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return ensure_tz_aware(value).isoformat()


def _truncate_text(value: str, limit: int = 1200) -> str:
    cleaned = (value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


DEFAULT_CARTOON_AGENT_INSTRUCTION = (
    "Create a whimsical cartoon illustration that summarizes the day. "
    "Highlight the main activity, mood, and setting."
)

DEFAULT_INSIGHTS_AGENT_INSTRUCTION = (
    "Create a daily insights summary with key stats, top keywords, and a surprise moment. "
    "Also generate an infographic image prompt."
)


def _dedupe_sources(sources: list[ChatSource]) -> list[ChatSource]:
    seen_items: set[str] = set()
    seen_contexts: set[str] = set()
    deduped: list[ChatSource] = []
    for source in sources:
        if source.source_item_id:
            if source.source_item_id in seen_items:
                continue
            seen_items.add(source.source_item_id)
        else:
            if source.context_id in seen_contexts:
                continue
            seen_contexts.add(source.context_id)
        deduped.append(source)
    return deduped


def _build_image_context_text(parsed: dict) -> Optional[str]:
    if not isinstance(parsed, dict):
        return None
    image_payload = parsed.get("image_0") or {}
    contexts = image_payload.get("contexts") if isinstance(image_payload, dict) else None
    if not isinstance(contexts, list):
        return None
    lines: list[str] = []
    for context in contexts[:4]:
        if not isinstance(context, dict):
            continue
        title = (context.get("title") or "").strip()
        summary = (context.get("summary") or "").strip()
        keywords = context.get("keywords") or []
        parts = [part for part in [title, summary] if part]
        if keywords:
            keywords_text = ", ".join(str(word).strip() for word in keywords if str(word).strip())
            if keywords_text:
                parts.append(f"keywords: {keywords_text}")
        if parts:
            lines.append(" - ".join(parts))
    if not lines:
        return None
    return "\n".join(lines)


async def _describe_image_bytes(
    image_bytes: bytes,
    *,
    content_type: Optional[str],
    user_id: UUID,
    settings,
) -> Optional[str]:
    if not image_bytes:
        return None
    prompt = build_lifelog_image_prompt(None)
    response = await analyze_image_with_vlm(
        image_bytes,
        prompt=prompt,
        settings=settings,
        content_type=content_type,
        user_id=user_id,
        step_name="chat_image",
    )
    parsed = response.get("parsed")
    if isinstance(parsed, dict):
        context_text = _build_image_context_text(parsed)
        if context_text:
            return _truncate_text(context_text)
    raw_text = response.get("raw_text") or ""
    return _truncate_text(raw_text) if raw_text else None


async def _describe_uploaded_image(
    image: UploadFile,
    *,
    user_id: UUID,
    settings,
) -> Optional[str]:
    image_bytes = await image.read()
    return await _describe_image_bytes(
        image_bytes,
        content_type=image.content_type,
        user_id=user_id,
        settings=settings,
    )


def _format_context_block(entries: list[tuple[ProcessedContext, dict]]) -> str:
    lines: list[str] = []
    for idx, (context, hit) in enumerate(entries, start=1):
        timestamp = (
            ensure_tz_aware(context.event_time_utc).isoformat()
            if context.event_time_utc
            else "unknown"
        )
        location = context.location or {}
        location_name = None
        if isinstance(location, dict):
            location_name = location.get("name") or location.get("place_name")
        location_text = f" at {location_name}" if location_name else ""
        summary = context.summary.strip() if context.summary else ""
        title = context.title.strip() if context.title else ""
        lines.append(
            f"[{idx}] {timestamp}{location_text}\nTitle: {title}\nSummary: {summary}".strip()
        )
    return "\n\n".join(lines)


def _format_history_block(history: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in history:
        role = msg.role.capitalize()
        content = (msg.content or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _resolve_agent_date(
    date_value: Optional[str],
    *,
    tz_offset_minutes: Optional[int],
) -> date:
    offset = timedelta(minutes=tz_offset_minutes or 0)
    if date_value:
        return date.fromisoformat(date_value)
    return (datetime.now(timezone.utc) - offset).date()


def _resolve_agent_date_range(
    start_value: Optional[str],
    end_value: Optional[str],
    *,
    tz_offset_minutes: Optional[int],
) -> tuple[date, date]:
    start = _resolve_agent_date(start_value, tz_offset_minutes=tz_offset_minutes)
    end = date.fromisoformat(end_value) if end_value else start
    if end < start:
        return end, start
    return start, end


async def _load_agent_day_context(
    session: AsyncSession,
    user_id: UUID,
    local_date: date,
    *,
    tz_offset_minutes: Optional[int],
    limit: int = 24,
) -> tuple[Optional[ProcessedContext], list[ProcessedContext]]:
    offset = timedelta(minutes=tz_offset_minutes or 0)
    start = datetime.combine(local_date, time.min, tzinfo=timezone.utc) + offset
    end = start + timedelta(days=1)

    summary_stmt = (
        select(ProcessedContext)
        .where(
            ProcessedContext.user_id == user_id,
            ProcessedContext.context_type == "daily_summary",
            ProcessedContext.start_time_utc >= start,
            ProcessedContext.start_time_utc < end,
        )
        .order_by(ProcessedContext.created_at.desc())
        .limit(1)
    )
    summary_row = await session.execute(summary_stmt)
    summary_context = summary_row.scalar_one_or_none()

    ctx_stmt = (
        select(ProcessedContext)
        .where(
            ProcessedContext.user_id == user_id,
            ProcessedContext.context_type != "daily_summary",
            ProcessedContext.event_time_utc >= start,
            ProcessedContext.event_time_utc < end,
        )
        .order_by(ProcessedContext.event_time_utc.asc())
        .limit(limit)
    )
    ctx_rows = await session.execute(ctx_stmt)
    contexts = list(ctx_rows.scalars().all())
    return summary_context, contexts


async def _load_agent_range_context(
    session: AsyncSession,
    user_id: UUID,
    start_date: date,
    end_date: date,
    *,
    tz_offset_minutes: Optional[int],
    limit: int = 60,
) -> tuple[list[ProcessedContext], list[ProcessedContext]]:
    offset = timedelta(minutes=tz_offset_minutes or 0)
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
    end = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)

    summary_stmt = (
        select(ProcessedContext)
        .where(
            ProcessedContext.user_id == user_id,
            ProcessedContext.context_type == "daily_summary",
            ProcessedContext.start_time_utc >= start,
            ProcessedContext.start_time_utc < end,
        )
        .order_by(ProcessedContext.start_time_utc.asc())
    )
    summary_rows = await session.execute(summary_stmt)
    summaries = list(summary_rows.scalars().all())

    ctx_stmt = (
        select(ProcessedContext)
        .where(
            ProcessedContext.user_id == user_id,
            ProcessedContext.context_type != "daily_summary",
            ProcessedContext.event_time_utc >= start,
            ProcessedContext.event_time_utc < end,
        )
        .order_by(ProcessedContext.event_time_utc.asc())
        .limit(limit)
    )
    ctx_rows = await session.execute(ctx_stmt)
    contexts = list(ctx_rows.scalars().all())
    return summaries, contexts


def _build_agent_range_memory_context(
    summaries: list[ProcessedContext],
    contexts: list[ProcessedContext],
) -> str:
    sections: list[str] = []
    if summaries:
        summary_lines = [
            f"{ensure_tz_aware(summary.start_time_utc).date().isoformat()}: {summary.summary.strip()}"
            for summary in summaries
            if summary.summary and summary.start_time_utc
        ]
        if summary_lines:
            sections.append("Daily summaries:\n" + "\n".join(summary_lines))
    if contexts:
        block = _format_context_block([(ctx, {}) for ctx in contexts])
        if block:
            sections.append(f"Memories:\n{block}")
    if not sections:
        return "No memories available for this date range."
    return "\n\n".join(sections)


def _build_agent_memory_context(
    summary_context: Optional[ProcessedContext],
    contexts: list[ProcessedContext],
) -> str:
    sections: list[str] = []
    if summary_context and summary_context.summary:
        sections.append(f"Daily summary: {summary_context.summary.strip()}")
    if contexts:
        block = _format_context_block([(ctx, {}) for ctx in contexts])
        if block:
            sections.append(f"Memories:\n{block}")
    if not sections:
        return "No memories available for this date."
    return "\n\n".join(sections)


async def _generate_session_title(first_message: str) -> str:
    settings = get_settings()
    cleaned = " ".join(first_message.strip().split())
    if not cleaned:
        return "New chat"
    if settings.chat_provider == "gemini" and settings.gemini_api_key:
        prompt = build_lifelog_session_title_prompt(cleaned)
        response = await summarize_text_with_gemini(
            prompt=prompt,
            settings=settings,
            model=settings.chat_model,
            temperature=0.2,
            max_output_tokens=24,
            timeout_seconds=settings.chat_timeout_seconds,
            step_name="chat_session_title",
        )
        title = (response.get("raw_text") or "").strip().strip('"').strip("'")
        if title:
            return title[:80]
    words = cleaned.split()
    return " ".join(words[:8]) if words else "New chat"


async def _get_or_create_session(
    session: AsyncSession,
    user_id: UUID,
    session_id: Optional[UUID],
    first_message: str,
) -> ChatSession:
    if session_id:
        existing = await session.get(ChatSession, session_id)
        if existing and existing.user_id == user_id:
            return existing
    title = await _generate_session_title(first_message)
    record = ChatSession(user_id=user_id, title=title)
    session.add(record)
    await session.flush()
    return record


async def _load_daily_summaries(session: AsyncSession, user_id: UUID, days: int = 7) -> list[DailySummary]:
    since = date_today = datetime.now(timezone.utc).date()
    start_date = date_today - timedelta(days=days - 1)
    stmt = (
        select(DailySummary)
        .where(
            DailySummary.user_id == user_id,
            DailySummary.summary_date >= start_date,
            DailySummary.summary_date <= since,
        )
        .order_by(DailySummary.summary_date.desc())
    )
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def _collect_preview_keys(
    session: AsyncSession, item_ids: list[UUID]
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    if not item_ids:
        return {}, {}
    stmt = select(DerivedArtifact).where(
        DerivedArtifact.source_item_id.in_(item_ids),
        DerivedArtifact.artifact_type.in_(["preview_image", "keyframes"]),
    )
    rows = await session.execute(stmt)
    preview_keys: dict[UUID, str] = {}
    keyframe_keys: dict[UUID, str] = {}
    for artifact in rows.scalars().all():
        if artifact.artifact_type == "preview_image" and artifact.storage_key:
            preview_keys[artifact.source_item_id] = artifact.storage_key
        elif artifact.artifact_type == "keyframes":
            payload = artifact.payload or {}
            poster = payload.get("poster")
            if isinstance(poster, dict) and poster.get("storage_key"):
                keyframe_keys[artifact.source_item_id] = poster["storage_key"]
                continue
            frames = payload.get("frames")
            if isinstance(frames, list) and frames:
                first = frames[0]
                if isinstance(first, dict) and first.get("storage_key"):
                    keyframe_keys[artifact.source_item_id] = first["storage_key"]
    return preview_keys, keyframe_keys


async def _sign_storage_url(storage, settings, storage_key: str) -> Optional[str]:
    if storage_key.startswith(("http://", "https://")):
        return storage_key
    try:
        signed = await asyncio.to_thread(
            storage.get_presigned_download, storage_key, settings.presigned_url_ttl_seconds
        )
    except Exception as exc:  # pragma: no cover - external service dependency
        logger.warning("Failed to sign download URL for {}: {}", storage_key, exc)
        return None
    url = signed.get("url") if signed else None
    return url or None


async def _build_thumbnail_urls(
    session: AsyncSession,
    items_by_id: dict[UUID, SourceItem],
    preview_keys: dict[UUID, str],
    keyframe_keys: dict[UUID, str],
) -> dict[UUID, Optional[str]]:
    settings = get_settings()
    storage = get_storage_provider()

    connections: dict[UUID, DataConnection] = {}
    tokens: dict[UUID, str] = {}
    connection_ids = [
        item.connection_id for item in items_by_id.values() if item.connection_id
    ]
    if connection_ids:
        conn_rows = await session.execute(select(DataConnection).where(DataConnection.id.in_(connection_ids)))
        connections = {conn.id: conn for conn in conn_rows.scalars().all()}
        http_connection_ids = {
            item.connection_id
            for item in items_by_id.values()
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
        if storage_key.startswith(("http://", "https://")):
            conn_id = item.connection_id
            token = tokens.get(conn_id) if conn_id else None
            if token:
                sep = "&" if "?" in storage_key else "?"
                return f"{storage_key}{sep}access_token={token}"
            return storage_key
        return await _sign_storage_url(storage, settings, storage_key)

    thumbnail_urls: dict[UUID, Optional[str]] = {}
    for item_id, item in items_by_id.items():
        thumbnail_url: Optional[str] = None
        if item.item_type == "photo":
            content_type = (item.content_type or "").lower()
            if content_type in WEB_IMAGE_TYPES:
                thumbnail_url = await download_url_for(item, None)
            else:
                preview_key = preview_keys.get(item.id)
                if preview_key:
                    thumbnail_url = await download_url_for(item, preview_key)
        elif item.item_type == "video":
            key = keyframe_keys.get(item.id)
            if key:
                thumbnail_url = await _sign_storage_url(storage, settings, key)
        thumbnail_urls[item_id] = thumbnail_url
    return thumbnail_urls


def _serialize_sources_for_storage(sources: list[ChatSource]) -> list[dict]:
    payloads: list[dict] = []
    for source in sources:
        data = source.model_dump(exclude={"thumbnail_url"}, exclude_none=True)
        payloads.append(data)
    return payloads


async def _rehydrate_sources(
    session: AsyncSession,
    sources: list[ChatSource],
) -> list[ChatSource]:
    source_item_ids: list[UUID] = []
    for source in sources:
        if not source.source_item_id:
            continue
        try:
            source_item_ids.append(UUID(source.source_item_id))
        except Exception:
            continue

    if not source_item_ids:
        return sources
    item_rows = await session.execute(select(SourceItem).where(SourceItem.id.in_(source_item_ids)))
    items_by_id = {item.id: item for item in item_rows.scalars().all()}
    preview_keys, keyframe_keys = await _collect_preview_keys(session, list(items_by_id.keys()))
    thumbnail_urls = await _build_thumbnail_urls(session, items_by_id, preview_keys, keyframe_keys)
    hydrated: list[ChatSource] = []
    for source in sources:
        url = None
        timestamp = source.timestamp
        if source.source_item_id:
            try:
                item_id = UUID(source.source_item_id)
            except Exception:
                item_id = None
            if item_id:
                url = thumbnail_urls.get(item_id)
                item = items_by_id.get(item_id)
                if item:
                    item_time = item.event_time_utc or item.captured_at or item.created_at
                    timestamp = _format_timestamp(item_time) or timestamp
        hydrated.append(source.model_copy(update={"thumbnail_url": url, "timestamp": timestamp}))
    return _dedupe_sources(hydrated)


async def _store_attachment_bytes(
    *,
    user_id: UUID,
    session_id: UUID,
    image_bytes: bytes,
    content_type: Optional[str],
    original_filename: Optional[str],
) -> dict:
    storage = get_storage_provider()
    safe_name = _sanitize_filename(original_filename or "image")
    attachment_id = uuid4()
    storage_key = f"chat/attachments/{user_id}/{attachment_id}-{safe_name}"
    await asyncio.to_thread(storage.store, storage_key, image_bytes, content_type or "application/octet-stream")
    return {
        "storage_key": storage_key,
        "content_type": content_type,
        "original_filename": original_filename,
        "size_bytes": len(image_bytes),
        "session_id": session_id,
    }


async def _load_message_attachments(
    session: AsyncSession,
    message_ids: list[UUID],
) -> dict[UUID, list[ChatAttachmentOut]]:
    if not message_ids:
        return {}
    settings = get_settings()
    storage = get_storage_provider()
    stmt = select(ChatAttachment).where(ChatAttachment.message_id.in_(message_ids))
    rows = await session.execute(stmt)
    attachments_by_message: dict[UUID, list[ChatAttachmentOut]] = {}
    for attachment in rows.scalars().all():
        if not attachment.message_id:
            continue
        url = await _sign_storage_url(storage, settings, attachment.storage_key)
        if not url:
            continue
        attachments_by_message.setdefault(attachment.message_id, []).append(
            ChatAttachmentOut(
                id=attachment.id,
                url=url,
                content_type=attachment.content_type,
                created_at=attachment.created_at,
            )
        )
    return attachments_by_message


async def _build_sources(
    session: AsyncSession,
    entries: list[tuple[ProcessedContext, dict]],
    limit: int = 5,
) -> list[ChatSource]:
    if not entries:
        return []

    filtered_entries = [
        (context, hit)
        for context, hit in entries
        if context.context_type != "daily_summary"
    ]
    if not filtered_entries:
        return []

    source_item_ids: list[UUID] = []
    for context, _ in filtered_entries:
        if context.source_item_ids:
            source_item_ids.append(context.source_item_ids[0])
    source_item_ids = list(dict.fromkeys(source_item_ids))

    items_by_id: dict[UUID, SourceItem] = {}
    if source_item_ids:
        item_rows = await session.execute(select(SourceItem).where(SourceItem.id.in_(source_item_ids)))
        items_by_id = {item.id: item for item in item_rows.scalars().all()}

    preview_keys, keyframe_keys = await _collect_preview_keys(session, source_item_ids)
    thumbnail_urls = await _build_thumbnail_urls(session, items_by_id, preview_keys, keyframe_keys)
    sources: list[ChatSource] = []
    seen_item_ids: set[UUID] = set()
    seen_context_ids: set[str] = set()
    for context, hit in filtered_entries:
        if len(sources) >= limit:
            break
        item_id: Optional[UUID] = None
        if context.source_item_ids:
            item_id = context.source_item_ids[0]
        if item_id and item_id in seen_item_ids:
            continue
        if not item_id and str(context.id) in seen_context_ids:
            continue
        item = items_by_id.get(item_id) if item_id else None
        item_time = None
        if item:
            item_time = item.event_time_utc or item.captured_at or item.created_at
        timestamp = _format_timestamp(item_time) or _format_timestamp(context.event_time_utc)
        thumbnail_url = thumbnail_urls.get(item_id) if item_id else None
        sources.append(
            ChatSource(
                context_id=str(context.id),
                source_item_id=str(item_id) if item_id else None,
                thumbnail_url=thumbnail_url,
                timestamp=timestamp,
                snippet=context.summary[:160] if context.summary else None,
                score=float(hit.get("score") or 0.0),
                title=context.title,
            )
        )
        if item_id:
            seen_item_ids.add(item_id)
        else:
            seen_context_ids.add(str(context.id))
    return sources


async def _run_chat(
    *,
    session: AsyncSession,
    user_id: UUID,
    message: str,
    session_id: Optional[UUID],
    tz_offset_minutes: Optional[int],
    image_context: Optional[str] = None,
    attachments: Optional[list[dict]] = None,
    attachment_ids: Optional[list[UUID]] = None,
) -> ChatResponse:
    settings = get_settings()
    if settings.chat_provider != "gemini" or not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Chat model not configured")

    offset_delta = timedelta(minutes=tz_offset_minutes or 0)
    session_record = await _get_or_create_session(session, user_id, session_id, message or "New chat")

    history_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_record.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(settings.chat_history_limit)
    )
    history_rows = await session.execute(history_stmt)
    history = list(reversed(list(history_rows.scalars().all())))

    search_query = message or ""
    if image_context:
        search_query = f"{search_query}\nImage description: {image_context}".strip()

    _, hits = await retrieve_context_hits(
        search_query or message,
        user_id=user_id,
        top_k=settings.chat_context_limit,
        settings=settings,
        tz_offset_minutes=tz_offset_minutes,
    )
    context_ids: list[UUID] = []
    for hit in hits:
        try:
            context_ids.append(UUID(str(hit.get("context_id"))))
        except Exception:
            continue

    contexts_by_id: dict[UUID, ProcessedContext] = {}
    if context_ids:
        context_stmt = select(ProcessedContext).where(ProcessedContext.id.in_(context_ids))
        context_rows = await session.execute(context_stmt)
        contexts_by_id = {context.id: context for context in context_rows.scalars().all()}

    ordered_entries: list[tuple[ProcessedContext, dict]] = []
    for hit in hits:
        context_id = hit.get("context_id")
        try:
            context_uuid = UUID(str(context_id))
        except Exception:
            continue
        context = contexts_by_id.get(context_uuid)
        if context:
            ordered_entries.append((context, hit))

    daily_summaries = await _load_daily_summaries(session, user_id, days=7)
    summary_block = ""
    if daily_summaries:
        summary_block = "\n".join(
            f"{(datetime.combine(summary.summary_date, time.min, tzinfo=timezone.utc) - offset_delta).date().isoformat()}: {summary.summary}"
            for summary in daily_summaries
            if summary.summary
        )

    context_block = _format_context_block(ordered_entries)
    history_block = _format_history_block(history)
    system_prompt = build_lifelog_chat_system_prompt()

    sections = [system_prompt]
    if summary_block:
        sections.append(f"Recent daily summaries:\n{summary_block}")
    if history_block:
        sections.append(f"Conversation history:\n{history_block}")
    if image_context:
        sections.append(f"User uploaded image:\n{image_context}")
    if context_block:
        sections.append(f"Relevant memories:\n{context_block}")
    sections.append(f"User question: {message}")
    prompt = "\n\n".join(section for section in sections if section.strip())

    response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.chat_model,
        temperature=settings.chat_temperature,
        max_output_tokens=settings.chat_max_output_tokens,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="chat_response",
        user_id=user_id,
    )
    assistant_message = (response.get("raw_text") or "").strip()
    if not assistant_message:
        assistant_message = "I do not have enough information to answer that yet."

    sources = await _build_sources(session, ordered_entries, limit=5)

    now = datetime.now(timezone.utc)
    session_record.updated_at = now
    session_record.last_message_at = now

    messages_to_add: list[ChatMessage] = []
    created_at = now
    if image_context:
        messages_to_add.append(
            ChatMessage(
                session_id=session_record.id,
                user_id=user_id,
                role="system",
                content=f"Image analysis: {_truncate_text(image_context, 800)}",
                sources=[],
                created_at=created_at,
            )
        )
        created_at = created_at + timedelta(milliseconds=1)

    user_content = message.strip() or "Image query"
    user_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="user",
        content=user_content,
        sources=[],
        created_at=created_at,
    )
    messages_to_add.append(user_msg)
    created_at = created_at + timedelta(milliseconds=1)
    assistant_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="assistant",
        content=assistant_message,
        sources=_serialize_sources_for_storage(sources),
        created_at=created_at,
    )
    messages_to_add.append(assistant_msg)
    session.add_all(messages_to_add)
    await session.flush()
    if attachments:
        for attachment in attachments:
            session.add(
                ChatAttachment(
                    user_id=user_id,
                    session_id=session_record.id,
                    message_id=user_msg.id,
                    storage_key=attachment.get("storage_key"),
                    content_type=attachment.get("content_type"),
                    original_filename=attachment.get("original_filename"),
                    size_bytes=attachment.get("size_bytes"),
                )
            )
    if attachment_ids:
        await session.execute(
            update(ChatAttachment)
            .where(
                ChatAttachment.user_id == user_id,
                ChatAttachment.id.in_(attachment_ids),
            )
            .values(message_id=user_msg.id, session_id=session_record.id)
        )
    await session.commit()

    return ChatResponse(
        message=assistant_message,
        session_id=session_record.id,
        sources=sources,
    )


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    return await _run_chat(
        session=session,
        user_id=user_id,
        message=request.message,
        session_id=request.session_id,
        tz_offset_minutes=request.tz_offset_minutes,
        attachment_ids=request.attachment_ids,
    )


@router.post("/image", response_model=ChatResponse)
async def chat_with_image(
    image: UploadFile = File(...),
    message: str = Form(""),
    session_id: Optional[UUID] = Form(None),
    tz_offset_minutes: Optional[int] = Form(None),
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    settings = get_settings()
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty upload")
    if not message.strip():
        message = "Find memories related to this photo."
    image_context = await _describe_image_bytes(
        image_bytes,
        content_type=image.content_type,
        user_id=user_id,
        settings=settings,
    )

    session_record = await _get_or_create_session(session, user_id, session_id, message or "Image chat")
    attachment_payload = await _store_attachment_bytes(
        user_id=user_id,
        session_id=session_record.id,
        image_bytes=image_bytes,
        content_type=image.content_type,
        original_filename=image.filename,
    )
    return await _run_chat(
        session=session,
        user_id=user_id,
        message=message,
        session_id=session_record.id,
        tz_offset_minutes=tz_offset_minutes,
        image_context=image_context,
        attachments=[attachment_payload],
    )


@router.post("/agents/cartoon", response_model=AgentImageResponse)
async def agent_cartoon_day_summary(
    payload: AgentImageRequest,
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> AgentImageResponse:
    settings = get_settings()
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="Agent features are disabled")
    if settings.agent_image_provider != "gemini" or not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Image model not configured")

    local_date = _resolve_agent_date(payload.date, tz_offset_minutes=payload.tz_offset_minutes)
    summary_context, contexts = await _load_agent_day_context(
        session,
        user_id,
        local_date,
        tz_offset_minutes=payload.tz_offset_minutes,
    )
    memory_context = _build_agent_memory_context(summary_context, contexts)
    instruction = (payload.prompt or "").strip() or DEFAULT_CARTOON_AGENT_INSTRUCTION

    prompt = build_lifelog_cartoon_agent_prompt(
        instruction=instruction,
        memory_context=memory_context,
        date_label=local_date.isoformat(),
    )
    prompt_response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.agent_prompt_model,
        temperature=settings.agent_prompt_temperature,
        max_output_tokens=512,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="agent_cartoon_prompt",
        user_id=user_id,
    )
    parsed = prompt_response.get("parsed")
    image_prompt = None
    caption = None
    if isinstance(parsed, dict):
        image_prompt = parsed.get("image_prompt")
        caption = parsed.get("caption")
    image_prompt = (image_prompt or instruction).strip()
    if not caption:
        caption = f"Cartoon day summary for {local_date.isoformat()}."

    model = settings.agent_image_model or "gemini-2.5-flash-image"
    image_result = await generate_image_with_gemini(
        image_prompt,
        settings,
        model,
        settings.agent_image_timeout_seconds,
        user_id=user_id,
        step_name="agent_cartoon_image",
    )
    images = image_result.get("images") or []
    if not images:
        raise HTTPException(status_code=502, detail="Image generation failed")
    image = images[0]

    session_record = await _get_or_create_session(
        session,
        user_id,
        payload.session_id,
        f"Cartoon day summary {local_date.isoformat()}",
    )
    now = datetime.now(timezone.utc)
    session_record.updated_at = now
    session_record.last_message_at = now

    user_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="user",
        content=f"Cartoon day summary for {local_date.isoformat()}",
        sources=[],
        created_at=now,
    )
    assistant_content = f"{caption}\n\nPrompt:\n{image_prompt}".strip()
    assistant_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="assistant",
        content=assistant_content,
        sources=[],
        created_at=now + timedelta(milliseconds=1),
    )
    session.add_all([user_msg, assistant_msg])
    await session.flush()

    attachment_payload = await _store_attachment_bytes(
        user_id=user_id,
        session_id=session_record.id,
        image_bytes=image.data,
        content_type=image.content_type,
        original_filename=f"cartoon-{local_date.isoformat()}.png",
    )
    attachment = ChatAttachment(
        user_id=user_id,
        session_id=session_record.id,
        message_id=assistant_msg.id,
        storage_key=attachment_payload.get("storage_key"),
        content_type=attachment_payload.get("content_type"),
        original_filename=attachment_payload.get("original_filename"),
        size_bytes=attachment_payload.get("size_bytes"),
    )
    session.add(attachment)
    await session.flush()
    await session.commit()

    storage = get_storage_provider()
    url = await _sign_storage_url(storage, settings, attachment.storage_key)
    attachments = []
    if url:
        attachments.append(
            ChatAttachmentOut(
                id=attachment.id,
                url=url,
                content_type=attachment.content_type,
                created_at=attachment.created_at,
            )
        )

    return AgentImageResponse(
        message=assistant_content,
        session_id=session_record.id,
        attachments=attachments,
        prompt=image_prompt,
        caption=caption,
    )


@router.post("/agents/insights", response_model=AgentImageResponse)
async def agent_day_insights(
    payload: AgentInsightsRequest,
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> AgentImageResponse:
    settings = get_settings()
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="Agent features are disabled")

    start_date, end_date = _resolve_agent_date_range(
        payload.date,
        payload.end_date,
        tz_offset_minutes=payload.tz_offset_minutes,
    )
    summaries, contexts = await _load_agent_range_context(
        session,
        user_id,
        start_date,
        end_date,
        tz_offset_minutes=payload.tz_offset_minutes,
    )
    memory_context = _build_agent_range_memory_context(summaries, contexts)
    instruction = (payload.prompt or "").strip() or DEFAULT_INSIGHTS_AGENT_INSTRUCTION
    date_label = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()} to {end_date.isoformat()}"
    )

    offset = timedelta(minutes=payload.tz_offset_minutes or 0)
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
    end = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)
    event_time_expr = func.coalesce(SourceItem.event_time_utc, SourceItem.created_at)
    item_stmt = (
        select(SourceItem.item_type, func.count(SourceItem.id))
        .where(
            SourceItem.user_id == user_id,
            SourceItem.processing_status == "completed",
            event_time_expr >= start,
            event_time_expr < end,
        )
        .group_by(SourceItem.item_type)
    )
    item_rows = await session.execute(item_stmt)
    item_counts = {row[0]: int(row[1]) for row in item_rows.fetchall() if row and row[0]}

    keyword_counts: dict[str, int] = {}
    context_type_counts: dict[str, int] = {}
    entity_names: list[str] = []
    for context in contexts:
        context_type_counts[context.context_type] = context_type_counts.get(context.context_type, 0) + 1
        for keyword in context.keywords or []:
            key = str(keyword or "").strip().lower()
            if not key:
                continue
            keyword_counts[key] = keyword_counts.get(key, 0) + 1
        for entity in context.entities or []:
            if isinstance(entity, dict):
                name = entity.get("name")
            else:
                name = None
            if name:
                entity_names.append(str(name))

    top_keywords = sorted(keyword_counts.items(), key=lambda pair: pair[1], reverse=True)[:10]
    stats_payload = {
        "date_range": date_label,
        "item_counts": item_counts,
        "context_type_counts": context_type_counts,
        "top_keywords": [word for word, _ in top_keywords],
        "entities": list(dict.fromkeys(entity_names))[:20],
    }
    stats_json = json.dumps(stats_payload, indent=2)

    prompt = build_lifelog_day_insights_agent_prompt(
        instruction=instruction,
        memory_context=memory_context,
        date_range_label=date_label,
        stats_json=stats_json,
    )
    prompt_response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.agent_prompt_model,
        temperature=settings.agent_prompt_temperature,
        max_output_tokens=512,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="agent_insights_prompt",
        user_id=user_id,
    )
    parsed = prompt_response.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {}
    headline = (parsed.get("headline") or f"Daily insights for {date_label}").strip()
    summary = (parsed.get("summary") or "").strip()
    labels = parsed.get("labels") if isinstance(parsed.get("labels"), list) else []
    keywords = parsed.get("top_keywords") if isinstance(parsed.get("top_keywords"), list) else []
    surprise = (parsed.get("surprise_moment") or "").strip()
    image_prompt = (parsed.get("image_prompt") or "").strip()

    if not keywords:
        keywords = [word for word, _ in top_keywords][:8]
    if not labels:
        labels = list(context_type_counts.keys())[:6]
    if not image_prompt:
        image_prompt = (
            "Create a clean infographic poster summarizing the day. "
            f"Include the title '{headline}' and 3-5 stat callouts."
        )

    lines = [headline]
    if summary:
        lines.append(f"Summary: {summary}")
    if item_counts:
        counts_text = ", ".join(f"{key}: {value}" for key, value in item_counts.items())
        lines.append(f"Stats: {counts_text}")
    if keywords:
        lines.append(f"Top keywords: {', '.join(str(word) for word in keywords)}")
    if labels:
        lines.append(f"Labels: {', '.join(str(label) for label in labels)}")
    if surprise:
        lines.append(f"Surprise: {surprise}")
    assistant_content = "\n".join(lines).strip()

    attachments: list[ChatAttachmentOut] = []
    prompt_used: Optional[str] = None
    caption = headline
    attachment_payload = None
    image_bytes: Optional[bytes] = None
    image_content_type: Optional[str] = None
    if payload.include_image:
        if settings.agent_image_provider != "gemini" or not settings.gemini_api_key:
            raise HTTPException(status_code=503, detail="Image model not configured")
        model = settings.agent_image_model or "gemini-2.5-flash-image"
        image_result = await generate_image_with_gemini(
            image_prompt,
            settings,
            model,
            settings.agent_image_timeout_seconds,
            user_id=user_id,
            step_name="agent_insights_image",
        )
        images = image_result.get("images") or []
        if not images:
            raise HTTPException(status_code=502, detail="Image generation failed")
        image = images[0]
        image_bytes = image.data
        image_content_type = image.content_type
        prompt_used = image_prompt

    session_record = await _get_or_create_session(
        session,
        user_id,
        payload.session_id,
        f"Day insights {date_label}",
    )
    now = datetime.now(timezone.utc)
    session_record.updated_at = now
    session_record.last_message_at = now

    user_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="user",
        content=f"Day insights for {date_label}",
        sources=[],
        created_at=now,
    )
    assistant_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="assistant",
        content=assistant_content,
        sources=[],
        created_at=now + timedelta(milliseconds=1),
    )
    session.add_all([user_msg, assistant_msg])
    await session.flush()

    if image_bytes:
        attachment_payload = await _store_attachment_bytes(
            user_id=user_id,
            session_id=session_record.id,
            image_bytes=image_bytes,
            content_type=image_content_type,
            original_filename=f"insights-{start_date.isoformat()}-{end_date.isoformat()}.png",
        )
        attachment = ChatAttachment(
            user_id=user_id,
            session_id=session_record.id,
            message_id=assistant_msg.id,
            storage_key=attachment_payload.get("storage_key"),
            content_type=attachment_payload.get("content_type"),
            original_filename=attachment_payload.get("original_filename"),
            size_bytes=attachment_payload.get("size_bytes"),
        )
        session.add(attachment)
        await session.flush()

        storage = get_storage_provider()
        url = await _sign_storage_url(storage, settings, attachment.storage_key)
        if url:
            attachments.append(
                ChatAttachmentOut(
                    id=attachment.id,
                    url=url,
                    content_type=attachment.content_type,
                    created_at=attachment.created_at,
                )
            )

    await session.commit()

    return AgentImageResponse(
        message=assistant_content,
        session_id=session_record.id,
        attachments=attachments,
        prompt=prompt_used,
        caption=caption,
    )


@router.post("/attachments", response_model=ChatAttachmentResponse)
async def upload_chat_attachment(
    image: UploadFile = File(...),
    session_id: Optional[UUID] = Form(None),
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> ChatAttachmentResponse:
    settings = get_settings()
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty upload")
    session_record = await _get_or_create_session(session, user_id, session_id, "New chat")
    attachment_payload = await _store_attachment_bytes(
        user_id=user_id,
        session_id=session_record.id,
        image_bytes=image_bytes,
        content_type=image.content_type,
        original_filename=image.filename,
    )
    attachment = ChatAttachment(
        user_id=user_id,
        session_id=session_record.id,
        storage_key=attachment_payload["storage_key"],
        content_type=attachment_payload.get("content_type"),
        original_filename=attachment_payload.get("original_filename"),
        size_bytes=attachment_payload.get("size_bytes"),
    )
    session.add(attachment)
    await session.commit()
    url = await _sign_storage_url(
        get_storage_provider(),
        settings,
        attachment.storage_key,
    )
    if not url:
        raise HTTPException(status_code=500, detail="Unable to sign attachment URL")
    return ChatAttachmentResponse(
        attachment_id=attachment.id,
        session_id=session_record.id,
        url=url,
    )


@router.get("/sessions", response_model=list[ChatSessionSummary])
async def list_sessions(
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> list[ChatSessionSummary]:
    stmt = (
        select(ChatSession, func.count(ChatMessage.id))
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id, isouter=True)
        .where(ChatSession.user_id == user_id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.last_message_at.desc())
    )
    rows = await session.execute(stmt)
    summaries: list[ChatSessionSummary] = []
    for chat_session, count in rows.all():
        summaries.append(
            ChatSessionSummary(
                session_id=chat_session.id,
                title=chat_session.title,
                created_at=chat_session.created_at,
                last_message_at=chat_session.last_message_at,
                message_count=count or 0,
            )
        )
    return summaries


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_session(
    session_id: UUID,
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> ChatSessionDetail:
    chat_session = await session.get(ChatSession, session_id)
    if not chat_session or chat_session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    msg_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    rows = await session.execute(msg_stmt)
    message_records = list(rows.scalars().all())
    message_ids = [msg.id for msg in message_records]
    attachments_by_message = await _load_message_attachments(session, message_ids)
    messages = []
    for msg in message_records:
        sources_payload = msg.sources or []
        sources = [ChatSource(**entry) for entry in sources_payload if isinstance(entry, dict)]
        sources = await _rehydrate_sources(session, sources)
        attachments = attachments_by_message.get(msg.id, [])
        messages.append(
            ChatMessageOut(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                sources=sources,
                attachments=attachments,
                created_at=msg.created_at,
            )
        )
    return ChatSessionDetail(
        session_id=chat_session.id,
        title=chat_session.title,
        messages=messages,
    )


@router.post("/feedback")
async def submit_feedback(
    payload: ChatFeedbackRequest,
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="Rating must be -1 or 1")
    record = ChatFeedback(
        user_id=user_id,
        message_id=payload.message_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    session.add(record)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return {"status": "ok"}
