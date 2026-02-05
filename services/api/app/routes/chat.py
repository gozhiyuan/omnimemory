"""Chat endpoints with RAG context."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import analyze_image_with_vlm, generate_image_with_gemini, summarize_text_with_gemini
from ..ai.prompts import (
    build_lifelog_cartoon_agent_prompt,
    build_lifelog_day_insights_agent_prompt,
    build_lifelog_image_prompt,
    build_lifelog_session_title_prompt,
    build_lifelog_surprise_agent_prompt,
)
from ..auth import get_current_user_id
from ..chat import build_query_plan_with_parsed, plan_retrieval
from ..chat.evidence_builder import build_evidence_hits
from ..chat.query_understanding import plan_to_dict
from ..chat.response_generator import ChatPromptInputs, build_chat_prompt
from ..chat.verifier import verify_response
from ..config import get_settings
from ..db.models import (
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
from ..user_settings import resolve_user_tz_offset_minutes


router = APIRouter()


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


class ChatSource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    context_id: str
    source_item_id: Optional[str] = None
    context_type: Optional[str] = None
    is_episode: Optional[bool] = None
    episode_id: Optional[str] = None
    thumbnail_url: Optional[str] = None
    timestamp: Optional[str] = None
    snippet: Optional[str] = None
    score: Optional[float] = None
    title: Optional[str] = None
    source_index: Optional[int] = None


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
    debug: bool = False


class ChatResponse(BaseModel):
    message: str
    session_id: UUID
    sources: list[ChatSource]
    query_plan: Optional[dict] = None
    debug: Optional[dict] = None


class ChatAttachmentResponse(BaseModel):
    attachment_id: UUID
    session_id: UUID
    url: str


class AgentImageRequest(BaseModel):
    session_id: Optional[UUID] = None
    date: Optional[str] = None
    end_date: Optional[str] = None
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
    sources: list[ChatSource] = []


class AgentSurpriseRequest(BaseModel):
    session_id: Optional[UUID] = None
    date: Optional[str] = None
    end_date: Optional[str] = None
    prompt: Optional[str] = None
    tz_offset_minutes: Optional[int] = None


class AgentTextResponse(BaseModel):
    message: str
    session_id: UUID
    sources: list[ChatSource] = []


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
    telemetry: Optional[dict] = None


class ChatSessionDetail(BaseModel):
    session_id: UUID
    title: Optional[str]
    messages: list[ChatMessageOut]
    has_more: bool = False
    next_before_id: Optional[str] = None


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


def _format_resolved_time_range(
    time_range: Optional["TimeRange"],
    tz_offset_minutes: Optional[int],
) -> Optional[str]:
    if not time_range:
        return None
    offset = timedelta(minutes=tz_offset_minutes or 0)
    start_local = (ensure_tz_aware(time_range.start) - offset).date()
    end_local = (ensure_tz_aware(time_range.end) - offset).date()
    if end_local <= start_local:
        return start_local.isoformat()
    inclusive_end = end_local - timedelta(days=1)
    if inclusive_end <= start_local:
        return start_local.isoformat()
    return f"{start_local.isoformat()} to {inclusive_end.isoformat()}"


DEFAULT_CARTOON_AGENT_INSTRUCTION = (
    "Create a detailed cartoon illustration that captures the most vivid moments, "
    "mood, and setting for the day or range. Include concrete props, lighting, and background details."
)

DEFAULT_INSIGHTS_AGENT_INSTRUCTION = (
    "Create a detailed daily insights summary with key stats, top keywords, labels, and trends. "
    "Also generate an infographic image prompt with clear text callouts."
)

DEFAULT_SURPRISE_AGENT_INSTRUCTION = (
    "Find the most surprising, easy-to-miss detail in the selected time range. "
    "Focus on small visual cues or background moments people might overlook "
    "(clothing, signage, objects, gestures, expressions, odd pairings). "
    "Avoid generic themes like 'outdoors' unless tied to a specific item. "
    "Explain why it stands out and cite concrete supporting details."
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


_NO_INFO_HINTS = (
    "do not have enough information",
    "don't have enough information",
    "dont have enough information",
    "do not have sufficient information",
    "don't have sufficient information",
    "i do not have enough information",
    "i don't have enough information",
    "i do not have any information",
    "i don't have any information",
    "i do not have the information",
    "i don't have the information",
    "i do not have any memories",
    "i don't have any memories",
    "no memories",
    "not enough information",
    "cannot find enough information",
    "can't find enough information",
    "unable to find enough information",
)


def _response_lacks_info(text: str) -> bool:
    if not text:
        return True
    lowered = " ".join(text.lower().split())
    return any(hint in lowered for hint in _NO_INFO_HINTS)


def _format_local_timestamp(value: Optional[datetime], tz_offset_minutes: Optional[int]) -> str:
    if not value:
        return "Unknown time"
    offset = timedelta(minutes=tz_offset_minutes or 0)
    local_dt = ensure_tz_aware(value) - offset
    return local_dt.strftime("%b %d, %Y %I:%M %p")


def _fallback_memory_answer(
    entries: list[tuple[ProcessedContext, dict]],
    tz_offset_minutes: Optional[int],
    resolved_time_range: Optional[str],
    max_items: int = 8,
) -> str:
    if resolved_time_range:
        header = f"Based on your memories for {resolved_time_range}, here is what I found:"
    else:
        header = "Based on your memories, here is what I found:"
    lines: list[str] = []
    for idx, (context, _hit) in enumerate(entries, start=1):
        timestamp = _format_local_timestamp(context.event_time_utc, tz_offset_minutes)
        summary = (context.summary or context.title or "Memory").strip()
        summary = _truncate_text(summary, 180)
        lines.append(f"- {timestamp}: {summary} [{idx}]")
        if len(lines) >= max_items:
            break
    return "\n".join([header, *lines]) if lines else header


def _format_history_block(history: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in history:
        role = msg.role.capitalize()
        content = (msg.content or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _trim_block_lines(block: str, max_lines: int) -> str:
    if not block:
        return ""
    lines = [line for line in block.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return block
    return "\n".join(lines[:max_lines])


def _trim_context_block(block: str, max_items: int) -> str:
    if not block:
        return ""
    entries = [entry for entry in block.split("\n\n") if entry.strip()]
    if len(entries) <= max_items:
        return block
    return "\n\n".join(entries[:max_items])


def _is_followup_query(message: str) -> bool:
    if not message:
        return False
    q = " ".join(message.lower().split())
    if len(q.split()) <= 6 and q.startswith(
        ("who", "what about", "how about", "and", "also", "where", "when")
    ):
        return True
    return any(token in q for token in ("that", "those", "there", "them"))


def _is_recap_query(message: str) -> bool:
    if not message:
        return False
    q = " ".join(message.lower().split())
    recap_hints = (
        "recap",
        "summary",
        "summarize",
        "overview",
        "highlights",
        "how was my day",
        "how was my week",
        "how was my month",
        "weekly summary",
        "daily summary",
        "monthly summary",
    )
    return any(hint in q for hint in recap_hints)


def _build_search_query(
    message: str,
    history: list[ChatMessage],
    image_context: Optional[str],
) -> str:
    search_query = message or ""
    if _is_followup_query(message):
        last_user = next((msg for msg in reversed(history) if msg.role == "user"), None)
        if last_user and last_user.content:
            search_query = f"{last_user.content}\nFollow-up: {message}".strip()
    if image_context:
        search_query = f"{search_query}\nImage description: {image_context}".strip()
    return search_query


async def _compact_history_block(
    history: list[ChatMessage],
    settings,
) -> str:
    if not history:
        return ""
    keep_last = min(4, len(history))
    if len(history) <= keep_last:
        return _format_history_block(history)
    older = history[:-keep_last]
    recent = history[-keep_last:]
    older_block = _format_history_block(older)
    if not older_block:
        return _format_history_block(history)

    prompt = (
        "Summarize the following chat history into 5-8 concise bullet points. "
        "Preserve dates, names, places, and unresolved questions. Do not invent details.\n\n"
        f"{older_block}"
    )
    response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.chat_model,
        temperature=0.2,
        max_output_tokens=settings.chat_history_compact_target_tokens,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="chat_history_compact",
    )
    summary = (response.get("raw_text") or "").strip()
    if not summary:
        return _format_history_block(recent)
    recent_block = _format_history_block(recent)
    if recent_block:
        return f"Conversation summary:\n{summary}\n\nRecent turns:\n{recent_block}"
    return f"Conversation summary:\n{summary}"


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


async def _resolve_request_tz_offset(
    *,
    session: AsyncSession,
    user_id: UUID,
    tz_offset_minutes: Optional[int],
    local_date: Optional[date] = None,
) -> int:
    return await resolve_user_tz_offset_minutes(
        session,
        user_id,
        tz_offset_minutes=tz_offset_minutes,
        local_date=local_date,
    )


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
            f"{(summary.processor_versions or {}).get('daily_summary_date') or ensure_tz_aware(summary.start_time_utc).date().isoformat()}: {summary.summary.strip()}"
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


def _extract_location_name(location: Optional[dict]) -> Optional[str]:
    if not isinstance(location, dict):
        return None
    return location.get("name") or location.get("place_name")


def _extract_context_anchors(
    contexts: list[ProcessedContext],
    *,
    max_items: int = 4,
) -> list[str]:
    anchors: list[str] = []
    for context in contexts:
        if len(anchors) >= max_items:
            break
        parts: list[str] = []
        title = (context.title or "").strip()
        summary = (context.summary or "").strip()
        location_name = _extract_location_name(context.location)
        if title:
            parts.append(title)
        if summary:
            parts.append(summary)
        if location_name:
            parts.append(f"at {location_name}")
        if parts:
            anchors.append(" — ".join(parts))
    return anchors


def _extract_summary_anchors(
    summaries: list[ProcessedContext],
    *,
    max_items: int = 2,
) -> list[str]:
    anchors: list[str] = []
    for summary in summaries:
        if len(anchors) >= max_items:
            break
        if summary.summary:
            anchors.append(summary.summary.strip())
    return anchors


def _merge_anchors(*groups: list[str], max_items: int = 6) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            cleaned = " ".join((item or "").split())
            if not cleaned or cleaned in seen:
                continue
            merged.append(cleaned)
            seen.add(cleaned)
            if len(merged) >= max_items:
                return merged
    return merged


def _format_surprise_evidence_cues(
    contexts: list[ProcessedContext],
    *,
    tz_offset_minutes: Optional[int],
    max_items: int = 4,
) -> list[str]:
    cues: list[str] = []
    offset = timedelta(minutes=tz_offset_minutes or 0)
    ordered_contexts = [ctx for ctx in contexts if ctx.context_type != "entity_context"] + [
        ctx for ctx in contexts if ctx.context_type == "entity_context"
    ]
    for context in ordered_contexts:
        if len(cues) >= max_items:
            break
        timestamp = "Unknown time"
        if context.event_time_utc:
            local_dt = ensure_tz_aware(context.event_time_utc) - offset
            timestamp = local_dt.strftime("%b %d, %Y %I:%M %p")
        title = (context.title or "").strip()
        summary = (context.summary or "").strip()
        label = title or summary or "Memory"
        context_type = (context.context_type or "").strip()
        suffix = f" ({context_type})" if context_type else ""
        cues.append(f"- {timestamp}: {label}{suffix}")
    return cues


_VISUAL_DETAIL_CUES = (
    "sweater",
    "shirt",
    "jacket",
    "coat",
    "hoodie",
    "dress",
    "skirt",
    "pants",
    "jeans",
    "shorts",
    "hat",
    "cap",
    "beanie",
    "scarf",
    "glove",
    "boots",
    "shoes",
    "sneakers",
    "sock",
    "costume",
    "mask",
    "uniform",
    "logo",
    "sign",
    "poster",
    "billboard",
    "banner",
    "menu",
    "label",
    "sticker",
    "neon",
    "badge",
    "ticket",
    "balloon",
    "cake",
    "book",
    "laptop",
    "phone",
    "camera",
    "bottle",
    "mug",
    "cup",
    "backpack",
    "umbrella",
    "bracelet",
    "ring",
    "necklace",
    "earrings",
    "watch",
    "toy",
    "gift",
    "flag",
)


def _split_sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def _find_visual_detail_sentence(text: str) -> Optional[str]:
    if not text:
        return None
    for sentence in _split_sentences(text):
        lowered = sentence.lower()
        if any(cue in lowered for cue in _VISUAL_DETAIL_CUES):
            return sentence
        if "wearing" in lowered or "holding" in lowered or "carrying" in lowered:
            return sentence
    lowered_text = text.lower()
    if any(cue in lowered_text for cue in _VISUAL_DETAIL_CUES):
        return text.strip()
    return None


def _extract_visual_detail_from_contexts(
    contexts: list[ProcessedContext],
    summaries: list[ProcessedContext],
) -> Optional[str]:
    candidates: list[str] = []
    for context in contexts:
        if context.context_type == "entity_context":
            continue
        if context.summary:
            candidates.append(context.summary)
        if context.title:
            candidates.append(context.title)
    for summary in summaries:
        if summary.summary:
            candidates.append(summary.summary)
    for text in candidates:
        detail = _find_visual_detail_sentence(text)
        if detail:
            return detail
    return None


_CONTEXT_PRIORITY = {
    "activity_context": 6,
    "social_context": 5,
    "food_context": 4,
    "emotion_context": 4,
    "knowledge_context": 3,
    "location_context": 2,
    "entity_context": 1,
}


def _context_rank_value(context: ProcessedContext) -> tuple[int, int, int]:
    summary_len = len((context.summary or "").strip())
    title_len = len((context.title or "").strip())
    priority = _CONTEXT_PRIORITY.get(context.context_type, 0)
    return (priority, summary_len, title_len)


def _stable_hash(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _choose_context_for_group(group: list[ProcessedContext]) -> ProcessedContext:
    entity_contexts = [ctx for ctx in group if ctx.context_type == "entity_context"]
    non_entity = [ctx for ctx in group if ctx.context_type != "entity_context"]
    if non_entity:
        best_non_entity = max(non_entity, key=_context_rank_value)
        best_entity = max(
            entity_contexts, key=lambda ctx: len((ctx.summary or "").strip()), default=None
        )
        if best_entity:
            if len((best_entity.summary or "").strip()) > len(
                (best_non_entity.summary or "").strip()
            ) + 60:
                return best_entity
        return best_non_entity
    return max(group, key=_context_rank_value)


def _dedupe_contexts_for_agents(
    contexts: list[ProcessedContext],
    *,
    max_items: int = 24,
    include_entity: bool = False,
) -> list[ProcessedContext]:
    grouped: dict[str, list[ProcessedContext]] = {}
    for context in contexts:
        if not include_entity and context.context_type == "entity_context":
            continue
        source_ids = context.source_item_ids or []
        if source_ids:
            key = ",".join(sorted(str(value) for value in source_ids))
        else:
            key = str(context.id)
        grouped.setdefault(key, []).append(context)

    selected: list[ProcessedContext] = []
    for group in grouped.values():
        selected.append(_choose_context_for_group(group))

    selected.sort(
        key=lambda ctx: ensure_tz_aware(ctx.event_time_utc)
        if ctx.event_time_utc
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return selected[:max_items]


def _context_local_date(
    context: ProcessedContext,
    *,
    tz_offset_minutes: Optional[int],
) -> Optional[date]:
    event_time = context.event_time_utc or context.start_time_utc or context.created_at
    if not event_time:
        return None
    event_time = ensure_tz_aware(event_time)
    if tz_offset_minutes:
        event_time = event_time + timedelta(minutes=tz_offset_minutes)
    return event_time.date()


def _sample_contexts_across_days(
    contexts: list[ProcessedContext],
    *,
    tz_offset_minutes: Optional[int],
    max_items: int,
    seed_key: Optional[str] = None,
) -> list[ProcessedContext]:
    if not contexts:
        return []

    grouped: dict[str, list[ProcessedContext]] = {}
    for context in contexts:
        local_date = _context_local_date(context, tz_offset_minutes=tz_offset_minutes)
        key = local_date.isoformat() if local_date else "unknown"
        grouped.setdefault(key, []).append(context)

    day_keys = sorted(grouped.keys())
    if seed_key and len(day_keys) > 1:
        seed = _stable_hash(seed_key)
        shift = seed % len(day_keys)
        day_keys = day_keys[shift:] + day_keys[:shift]
    per_day = max(1, int(math.ceil(max_items / max(len(day_keys), 1))))

    for day_key in day_keys:
        bucket = grouped[day_key]
        bucket.sort(key=lambda ctx: _context_rank_value(ctx), reverse=True)
        slice_size = min(len(bucket), max(per_day * 2, per_day))
        candidates = bucket[:slice_size]
        if seed_key and candidates:
            seed = _stable_hash(f"{seed_key}:{day_key}")
            shift = seed % len(candidates)
            candidates = candidates[shift:] + candidates[:shift]
        grouped[day_key] = candidates[:per_day]

    sampled: list[ProcessedContext] = []
    while len(sampled) < max_items:
        added = False
        for day_key in day_keys:
            bucket = grouped.get(day_key) or []
            if not bucket:
                continue
            sampled.append(bucket.pop(0))
            added = True
            if len(sampled) >= max_items:
                break
        if not added:
            break
    return sampled


def _collect_visual_details(
    contexts: list[ProcessedContext],
    summaries: list[ProcessedContext],
    *,
    max_items: int = 3,
) -> list[str]:
    details: list[str] = []
    seen: set[str] = set()
    candidates: list[str] = []
    for context in contexts:
        if context.context_type == "entity_context":
            continue
        if context.title:
            candidates.append(context.title)
        if context.summary:
            candidates.append(context.summary)
    for summary in summaries:
        if summary.summary:
            candidates.append(summary.summary)
    for text in candidates:
        sentence = _find_visual_detail_sentence(text)
        if not sentence:
            continue
        cleaned = " ".join(sentence.split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        details.append(cleaned)
        if len(details) >= max_items:
            break
    return details


def _collect_available_dates(
    contexts: list[ProcessedContext],
    summaries: list[ProcessedContext],
    *,
    tz_offset_minutes: Optional[int],
) -> list[str]:
    dates: set[str] = set()
    for context in contexts:
        local_date = _context_local_date(context, tz_offset_minutes=tz_offset_minutes)
        if local_date:
            dates.add(local_date.isoformat())
    for summary in summaries:
        base_time = summary.start_time_utc or summary.event_time_utc or summary.created_at
        if base_time:
            base_time = ensure_tz_aware(base_time)
            if tz_offset_minutes:
                base_time = base_time + timedelta(minutes=tz_offset_minutes)
            dates.add(base_time.date().isoformat())
    return sorted(dates)


_SURPRISE_GENERIC_TERMS = {
    "outdoors",
    "outdoor",
    "indoor",
    "mountains",
    "street",
    "streets",
    "park",
    "parks",
    "walking",
    "hiking",
    "exercise",
    "nature",
    "city",
    "people",
    "person",
    "group",
}


def _filter_surprise_terms(items: list[str]) -> list[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item or "").strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        has_visual_detail = _find_visual_detail_sentence(cleaned) is not None
        if any(term in lowered for term in _SURPRISE_GENERIC_TERMS) and not has_visual_detail:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        filtered.append(cleaned)
    return filtered


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


def _summary_display_date(
    summary: DailySummary,
    tz_offset_minutes: Optional[int],
) -> date:
    metadata = summary.summary_metadata or {}
    if isinstance(metadata, dict) and metadata.get("tz_offset_minutes") is not None:
        return summary.summary_date
    offset_delta = timedelta(minutes=tz_offset_minutes or 0)
    return (datetime.combine(summary.summary_date, time.min, tzinfo=timezone.utc) - offset_delta).date()


async def _load_daily_summaries(
    session: AsyncSession,
    user_id: UUID,
    days: int = 7,
    tz_offset_minutes: Optional[int] = None,
    date_range: Optional[tuple[datetime, datetime]] = None,
) -> list[DailySummary]:
    offset_delta = timedelta(minutes=tz_offset_minutes or 0)
    if date_range:
        start_dt, end_dt = date_range
        start_date = (start_dt - offset_delta).date()
        end_date = (end_dt - offset_delta).date()
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
    else:
        since = date_today = (datetime.now(timezone.utc) - offset_delta).date()
        start_date = date_today - timedelta(days=days - 1)
        end_date = since + timedelta(days=1)
    stmt = (
        select(DailySummary)
        .where(
            DailySummary.user_id == user_id,
            DailySummary.summary_date >= start_date,
            DailySummary.summary_date < end_date,
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
            if content_type not in WEB_IMAGE_TYPES:
                inferred = _infer_image_content_type(item)
                if inferred:
                    content_type = inferred
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


_TELEMETRY_CONTEXT_ID = "__telemetry__"
_TELEMETRY_CONTEXT_TYPE = "__telemetry__"


def _attach_telemetry_payload(
    sources_payload: list[dict],
    telemetry: Optional[dict],
) -> list[dict]:
    if not telemetry:
        return sources_payload
    payload = dict(telemetry)
    telemetry_entry = {
        "context_id": _TELEMETRY_CONTEXT_ID,
        "context_type": _TELEMETRY_CONTEXT_TYPE,
        "title": "telemetry",
        "telemetry": payload,
    }
    return [*sources_payload, telemetry_entry]


def _split_sources_payload(
    sources_payload: list,
) -> tuple[list[dict], Optional[dict]]:
    cleaned: list[dict] = []
    telemetry: Optional[dict] = None
    for entry in sources_payload or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("context_type") == _TELEMETRY_CONTEXT_TYPE:
            telemetry = entry.get("telemetry")
            continue
        cleaned.append(entry)
    return cleaned, telemetry


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
        logger.debug("_build_sources: no entries provided")
        return []

    source_index_by_id = {
        str(context.id): idx + 1 for idx, (context, _) in enumerate(entries)
    }
    filtered_entries = [
        (context, hit)
        for context, hit in entries
        if context.context_type != "daily_summary"
    ]
    if not filtered_entries:
        logger.debug("_build_sources: all entries are daily_summary, returning empty")
        return []

    scored_entries: list[tuple[ProcessedContext, dict, float]] = []
    for context, hit in filtered_entries:
        score = float(hit.get("score") or 0.0)
        scored_entries.append((context, hit, score))

    max_score = max((score for _, _, score in scored_entries), default=0.0)
    threshold = max(0.2, max_score * 0.6)
    logger.debug(f"_build_sources: {len(scored_entries)} entries, max_score={max_score:.3f}, threshold={threshold:.3f}")
    filtered_entries = [
        (context, hit)
        for context, hit, score in scored_entries
        if score >= threshold
    ]
    if not filtered_entries:
        filtered_entries = [
            (context, hit)
            for context, hit, _ in sorted(scored_entries, key=lambda entry: entry[2], reverse=True)[
                : min(3, len(scored_entries))
            ]
        ]
        logger.debug(f"_build_sources: below threshold, using top {len(filtered_entries)} entries")
    scores_by_context_id = {str(context.id): score for context, _, score in scored_entries}
    filtered_entries.sort(
        key=lambda entry: scores_by_context_id.get(str(entry[0].id), 0.0), reverse=True
    )

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
        episode_id = None
        if context.is_episode:
            versions = context.processor_versions or {}
            if isinstance(versions, dict):
                episode_id = versions.get("episode_id")
            if not episode_id:
                episode_id = str(context.id)
        sources.append(
            ChatSource(
                context_id=str(context.id),
                source_item_id=str(item_id) if item_id else None,
                context_type=context.context_type,
                is_episode=bool(context.is_episode),
                episode_id=episode_id,
                thumbnail_url=thumbnail_url,
                timestamp=timestamp,
                snippet=context.summary[:160] if context.summary else None,
                score=scores_by_context_id.get(str(context.id), 0.0),
                title=context.title,
                source_index=source_index_by_id.get(str(context.id)),
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
    debug: bool = False,
) -> ChatResponse:
    settings = get_settings()
    if settings.chat_provider != "gemini" or not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Chat model not configured")

    session_record = await _get_or_create_session(session, user_id, session_id, message or "New chat")

    history_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_record.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(settings.chat_history_limit)
    )
    history_rows = await session.execute(history_stmt)
    history = list(reversed(list(history_rows.scalars().all())))

    search_query = _build_search_query(message or "", history, image_context)

    plan, parsed_plan = await build_query_plan_with_parsed(
        message or search_query,
        history=history,
        tz_offset_minutes=tz_offset_minutes,
        settings=settings,
    )
    retrieval_config = plan_retrieval(plan)

    # Get intent classification along with hits
    intent, parsed, hits = await retrieve_context_hits(
        search_query or message,
        user_id=user_id,
        top_k=retrieval_config.limit or settings.chat_context_limit,
        settings=settings,
        tz_offset_minutes=tz_offset_minutes,
        session=session,
        parsed_override=parsed_plan,
        intent_override=plan.intent,
        query_type=plan.query_type,
        context_types=retrieval_config.context_types,
        allow_rerank=retrieval_config.allow_rerank,
    )

    # For non-memory intents, skip loading contexts
    ordered_entries: list[tuple[ProcessedContext, dict]] = []
    contexts_by_id: dict[UUID, ProcessedContext] = {}

    evidence_hits = hits
    if intent == "memory_query" and hits:
        evidence_hits = build_evidence_hits(
            hits,
            plan,
            max_sources=min(settings.chat_context_limit, 8),
        )

    if intent == "memory_query" and evidence_hits:
        context_ids: list[UUID] = []
        for hit in evidence_hits:
            try:
                context_ids.append(UUID(str(hit.get("context_id"))))
            except Exception:
                continue

        if context_ids:
            context_stmt = select(ProcessedContext).where(ProcessedContext.id.in_(context_ids))
            context_rows = await session.execute(context_stmt)
            contexts_by_id = {context.id: context for context in context_rows.scalars().all()}

        for hit in evidence_hits:
            context_id = hit.get("context_id")
            try:
                context_uuid = UUID(str(context_id))
            except Exception:
                continue
            context = contexts_by_id.get(context_uuid)
            if context:
                ordered_entries.append((context, hit))

    # Load daily summaries for memory queries
    summary_block = ""
    if intent == "memory_query":
        daily_summaries = await _load_daily_summaries(
            session,
            user_id,
            days=7,
            tz_offset_minutes=tz_offset_minutes,
            date_range=parsed.date_range if parsed else None,
        )
        if daily_summaries:
            summary_block = "\n".join(
                f"{_summary_display_date(summary, tz_offset_minutes).isoformat()}: {summary.summary}"
                for summary in daily_summaries
                if summary.summary
            )

    context_block = _format_context_block(ordered_entries)
    history_block = _format_history_block(history)
    include_summary = (
        bool(summary_block)
        and intent == "memory_query"
        and (_is_recap_query(message) or not context_block)
    )
    resolved_time_range = _format_resolved_time_range(plan.time_range, tz_offset_minutes)

    prompt_inputs = ChatPromptInputs(
        intent=intent,
        message=message,
        summary_block=summary_block,
        history_block=history_block,
        image_context=image_context,
        context_block=context_block,
        tz_offset_minutes=tz_offset_minutes,
        include_summary=include_summary,
        resolved_time_range=resolved_time_range,
    )

    prompt = build_chat_prompt(prompt_inputs)
    prompt_tokens = _estimate_tokens(prompt)
    compact_threshold = settings.chat_prompt_budget_tokens * settings.chat_prompt_compact_ratio
    if (
        prompt_tokens > compact_threshold
        and len(history) >= settings.chat_history_compact_min_messages
    ):
        history_block = await _compact_history_block(history, settings)
        prompt_inputs.history_block = history_block
        prompt = build_chat_prompt(prompt_inputs)
        prompt_tokens = _estimate_tokens(prompt)

    if prompt_tokens > settings.chat_prompt_budget_tokens:
        if summary_block:
            summary_block = _trim_block_lines(summary_block, 3)
        if context_block:
            context_block = _trim_context_block(context_block, settings.chat_context_limit)
        prompt_inputs.summary_block = summary_block
        prompt_inputs.context_block = context_block
        prompt = build_chat_prompt(prompt_inputs)

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

    fallback_used = False
    if intent == "memory_query" and ordered_entries and _response_lacks_info(assistant_message):
        assistant_message = _fallback_memory_answer(
            ordered_entries,
            tz_offset_minutes=tz_offset_minutes,
            resolved_time_range=resolved_time_range,
        )
        fallback_used = True

    sources = await _build_sources(session, ordered_entries, limit=5)

    telemetry_payload = {
        "query_plan": plan_to_dict(plan),
        "intent": intent,
        "query_type": plan.query_type,
        "candidate_count": len(hits),
        "evidence_count": len(evidence_hits),
        "context_count": len(ordered_entries),
        "prompt_tokens": prompt_tokens,
        "include_summary": include_summary,
        "fallback_used": fallback_used,
        "retrieval_config": {
            "limit": retrieval_config.limit,
            "context_types": sorted(retrieval_config.context_types or []),
            "allow_rerank": retrieval_config.allow_rerank,
        },
    }

    debug_payload = telemetry_payload if debug else None
    query_plan_payload = telemetry_payload.get("query_plan") if debug else None

    if intent == "memory_query" and settings.chat_verification_enabled:
        verification = await verify_response(
            assistant_message,
            ordered_entries,
            message,
            settings=settings,
        )
        if not verification.is_grounded and verification.suggested_followup:
            assistant_message = verification.suggested_followup

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
    stored_sources = _serialize_sources_for_storage(sources)
    stored_sources = _attach_telemetry_payload(stored_sources, telemetry_payload)
    assistant_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="assistant",
        content=assistant_message,
        sources=stored_sources,
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
        query_plan=query_plan_payload,
        debug=debug_payload,
    )


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=request.tz_offset_minutes,
    )
    return await _run_chat(
        session=session,
        user_id=user_id,
        message=request.message,
        session_id=request.session_id,
        tz_offset_minutes=resolved_offset,
        attachment_ids=request.attachment_ids,
        debug=request.debug,
    )


@router.post("/image", response_model=ChatResponse)
async def chat_with_image(
    image: UploadFile = File(...),
    message: str = Form(""),
    session_id: Optional[UUID] = Form(None),
    tz_offset_minutes: Optional[int] = Form(None),
    debug: bool = Form(False),
    user_id: UUID = Depends(get_current_user_id),
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
    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=tz_offset_minutes,
    )
    return await _run_chat(
        session=session,
        user_id=user_id,
        message=message,
        session_id=session_record.id,
        tz_offset_minutes=resolved_offset,
        image_context=image_context,
        attachments=[attachment_payload],
        debug=debug,
    )


@router.post("/agents/cartoon", response_model=AgentImageResponse)
async def agent_cartoon_day_summary(
    payload: AgentImageRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> AgentImageResponse:
    settings = get_settings()
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="Agent features are disabled")
    if settings.agent_image_provider != "gemini" or not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Image model not configured")

    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=payload.tz_offset_minutes,
        local_date=date.fromisoformat(payload.date) if payload.date else None,
    )
    start_date, end_date = _resolve_agent_date_range(
        payload.date,
        payload.end_date,
        tz_offset_minutes=resolved_offset,
    )
    summaries: list[ProcessedContext] = []
    summary_context: Optional[ProcessedContext] = None
    contexts_all: list[ProcessedContext] = []
    if start_date == end_date:
        summary_context, contexts = await _load_agent_day_context(
            session,
            user_id,
            start_date,
            tz_offset_minutes=resolved_offset,
        )
        raw_contexts = contexts
        contexts_all = _dedupe_contexts_for_agents(raw_contexts, max_items=24, include_entity=False)
        contexts = _sample_contexts_across_days(
            contexts_all,
            tz_offset_minutes=resolved_offset,
            max_items=24,
            seed_key=f"cartoon:{start_date.isoformat()}:{uuid4().hex}",
        )
        memory_context = _build_agent_memory_context(summary_context, contexts)
        if summary_context:
            summaries = [summary_context]
    else:
        summaries, contexts = await _load_agent_range_context(
            session,
            user_id,
            start_date,
            end_date,
            tz_offset_minutes=resolved_offset,
        )
        raw_contexts = contexts
        contexts_all = _dedupe_contexts_for_agents(raw_contexts, max_items=32, include_entity=False)
        if not contexts_all:
            contexts_all = _dedupe_contexts_for_agents(
                raw_contexts, max_items=16, include_entity=True
            )
        contexts = _sample_contexts_across_days(
            contexts_all,
            tz_offset_minutes=resolved_offset,
            max_items=32,
            seed_key=f"cartoon:{start_date.isoformat()}:{end_date.isoformat()}:{uuid4().hex}",
        )
        memory_context = _build_agent_range_memory_context(summaries, contexts)
    instruction = (payload.prompt or "").strip() or DEFAULT_CARTOON_AGENT_INSTRUCTION
    date_label = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()} to {end_date.isoformat()}"
    )

    if not contexts_all and not summaries and not summary_context:
        assistant_content = f"No memories found for {date_label}."
        session_record = await _get_or_create_session(
            session,
            user_id,
            payload.session_id,
            f"Cartoon summary {date_label}",
        )
        now = datetime.now(timezone.utc)
        session_record.updated_at = now
        session_record.last_message_at = now
        session.add_all(
            [
                ChatMessage(
                    session_id=session_record.id,
                    user_id=user_id,
                    role="user",
                    content=f"Cartoon summary for {date_label}",
                    sources=[],
                    created_at=now,
                ),
                ChatMessage(
                    session_id=session_record.id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_content,
                    sources=[],
                    created_at=now + timedelta(milliseconds=1),
                ),
            ]
        )
        await session.commit()
        return AgentImageResponse(
            message=assistant_content,
            session_id=session_record.id,
            attachments=[],
            prompt=None,
            caption=None,
            sources=[],
        )

    available_dates = _collect_available_dates(
        contexts_all if contexts_all else contexts,
        summaries,
        tz_offset_minutes=resolved_offset,
    )
    prompt = build_lifelog_cartoon_agent_prompt(
        instruction=instruction,
        memory_context=memory_context,
        date_label=date_label,
        available_dates=", ".join(available_dates) if available_dates else "None",
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
    anchors = _merge_anchors(
        _extract_summary_anchors(summaries),
        _extract_context_anchors(contexts),
        max_items=6,
    )
    if anchors:
        anchor_text = _truncate_text("; ".join(anchors), limit=400)
        image_prompt = f"{image_prompt}\n\nInclude these concrete memory anchors: {anchor_text}."
    if not caption:
        caption = f"Cartoon summary for {date_label}."

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
        f"Cartoon summary {date_label}",
    )
    now = datetime.now(timezone.utc)
    session_record.updated_at = now
    session_record.last_message_at = now

    user_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="user",
        content=f"Cartoon summary for {date_label}",
        sources=[],
        created_at=now,
    )
    assistant_content = caption.strip()
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

    source_entries = [
        (context, {"score": max(0.01, 1.0 - idx * 0.03)})
        for idx, context in enumerate(contexts[:12])
    ]
    sources = await _build_sources(session, source_entries, limit=5) if source_entries else []
    if sources:
        assistant_msg.sources = _serialize_sources_for_storage(sources)

    file_label = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()}-to-{end_date.isoformat()}"
    )
    attachment_payload = await _store_attachment_bytes(
        user_id=user_id,
        session_id=session_record.id,
        image_bytes=image.data,
        content_type=image.content_type,
        original_filename=f"cartoon-{file_label}.png",
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
        sources=sources,
    )


@router.post("/agents/insights", response_model=AgentImageResponse)
async def agent_day_insights(
    payload: AgentInsightsRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> AgentImageResponse:
    settings = get_settings()
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="Agent features are disabled")

    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=payload.tz_offset_minutes,
        local_date=date.fromisoformat(payload.date) if payload.date else None,
    )
    start_date, end_date = _resolve_agent_date_range(
        payload.date,
        payload.end_date,
        tz_offset_minutes=resolved_offset,
    )
    summaries, contexts = await _load_agent_range_context(
        session,
        user_id,
        start_date,
        end_date,
        tz_offset_minutes=resolved_offset,
    )
    raw_contexts = contexts
    contexts_all = _dedupe_contexts_for_agents(raw_contexts, max_items=40, include_entity=False)
    if not contexts_all:
        contexts_all = _dedupe_contexts_for_agents(
            raw_contexts, max_items=20, include_entity=True
        )
    contexts = _sample_contexts_across_days(
        contexts_all,
        tz_offset_minutes=resolved_offset,
        max_items=40,
        seed_key=f"insights:{start_date.isoformat()}:{end_date.isoformat()}:{uuid4().hex}",
    )
    memory_context = _build_agent_range_memory_context(summaries, contexts)
    instruction = (payload.prompt or "").strip() or DEFAULT_INSIGHTS_AGENT_INSTRUCTION
    date_label = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()} to {end_date.isoformat()}"
    )

    if not contexts_all and not summaries:
        assistant_content = f"No memories found for {date_label}."
        session_record = await _get_or_create_session(
            session,
            user_id,
            payload.session_id,
            f"Day insights {date_label}",
        )
        now = datetime.now(timezone.utc)
        session_record.updated_at = now
        session_record.last_message_at = now
        session.add_all(
            [
                ChatMessage(
                    session_id=session_record.id,
                    user_id=user_id,
                    role="user",
                    content=f"Day insights for {date_label}",
                    sources=[],
                    created_at=now,
                ),
                ChatMessage(
                    session_id=session_record.id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_content,
                    sources=[],
                    created_at=now + timedelta(milliseconds=1),
                ),
            ]
        )
        await session.commit()
        return AgentImageResponse(
            message=assistant_content,
            session_id=session_record.id,
            attachments=[],
            prompt=None,
            caption=None,
            sources=[],
        )

    offset = timedelta(minutes=resolved_offset)
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
    for context in contexts_all:
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

    available_dates = _collect_available_dates(
        contexts_all if contexts_all else contexts,
        summaries,
        tz_offset_minutes=resolved_offset,
    )
    prompt = build_lifelog_day_insights_agent_prompt(
        instruction=instruction,
        memory_context=memory_context,
        date_range_label=date_label,
        stats_json=stats_json,
        available_dates=", ".join(available_dates) if available_dates else "None",
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
    callouts: list[str] = []
    if item_counts:
        callouts.extend([f"{key}: {value}" for key, value in item_counts.items()])
    if keywords:
        callouts.extend([f"keyword: {word}" for word in keywords[:3]])
    anchors = _merge_anchors(
        _extract_summary_anchors(summaries),
        _extract_context_anchors(contexts),
        max_items=6,
    )
    if callouts:
        callout_text = _truncate_text(", ".join(callouts[:6]), limit=260)
        image_prompt = f"{image_prompt}\n\nText callouts to include: {callout_text}."
    if anchors:
        anchor_text = _truncate_text("; ".join(anchors), limit=400)
        image_prompt = f"{image_prompt}\n\nMemory anchors to reflect visually: {anchor_text}."

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

    source_entries = [
        (context, {"score": max(0.01, 1.0 - idx * 0.03)})
        for idx, context in enumerate(contexts[:12])
    ]
    sources = await _build_sources(session, source_entries, limit=5) if source_entries else []
    if sources:
        assistant_msg.sources = _serialize_sources_for_storage(sources)

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
        sources=sources,
    )


@router.post("/agents/surprise", response_model=AgentTextResponse)
async def agent_surprise_highlight(
    payload: AgentSurpriseRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> AgentTextResponse:
    settings = get_settings()
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="Agent features are disabled")

    resolved_offset = await _resolve_request_tz_offset(
        session=session,
        user_id=user_id,
        tz_offset_minutes=payload.tz_offset_minutes,
        local_date=date.fromisoformat(payload.date) if payload.date else None,
    )
    start_date, end_date = _resolve_agent_date_range(
        payload.date,
        payload.end_date,
        tz_offset_minutes=resolved_offset,
    )
    summaries: list[ProcessedContext] = []
    contexts_all: list[ProcessedContext] = []
    summary_context: Optional[ProcessedContext] = None
    if start_date == end_date:
        summary_context, contexts = await _load_agent_day_context(
            session,
            user_id,
            start_date,
            tz_offset_minutes=resolved_offset,
        )
        if summary_context:
            summaries = [summary_context]
        raw_contexts = contexts
        contexts_all = _dedupe_contexts_for_agents(raw_contexts, max_items=24, include_entity=False)
        if not contexts_all:
            contexts_all = _dedupe_contexts_for_agents(
                raw_contexts, max_items=16, include_entity=True
            )
        contexts = _sample_contexts_across_days(
            contexts_all,
            tz_offset_minutes=resolved_offset,
            max_items=24,
            seed_key=f"surprise:{start_date.isoformat()}:{uuid4().hex}",
        )
        memory_context = _build_agent_memory_context(summary_context, contexts)
    else:
        summaries, contexts = await _load_agent_range_context(
            session,
            user_id,
            start_date,
            end_date,
            tz_offset_minutes=resolved_offset,
        )
        raw_contexts = contexts
        contexts_all = _dedupe_contexts_for_agents(raw_contexts, max_items=32, include_entity=False)
        if not contexts_all:
            contexts_all = _dedupe_contexts_for_agents(
                raw_contexts, max_items=20, include_entity=True
            )
        contexts = _sample_contexts_across_days(
            contexts_all,
            tz_offset_minutes=resolved_offset,
            max_items=32,
            seed_key=f"surprise:{start_date.isoformat()}:{end_date.isoformat()}:{uuid4().hex}",
        )
        memory_context = _build_agent_range_memory_context(summaries, contexts)
    instruction = (payload.prompt or "").strip() or DEFAULT_SURPRISE_AGENT_INSTRUCTION
    date_label = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()} to {end_date.isoformat()}"
    )

    if not contexts_all and not summaries and not summary_context:
        assistant_content = f"No memories found for {date_label}."
        session_record = await _get_or_create_session(
            session,
            user_id,
            payload.session_id,
            f"Surprise highlight {date_label}",
        )
        now = datetime.now(timezone.utc)
        session_record.updated_at = now
        session_record.last_message_at = now
        session.add_all(
            [
                ChatMessage(
                    session_id=session_record.id,
                    user_id=user_id,
                    role="user",
                    content=f"Surprise highlight for {date_label}",
                    sources=[],
                    created_at=now,
                ),
                ChatMessage(
                    session_id=session_record.id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_content,
                    sources=[],
                    created_at=now + timedelta(milliseconds=1),
                ),
            ]
        )
        await session.commit()
        return AgentTextResponse(
            message=assistant_content,
            session_id=session_record.id,
            sources=[],
        )

    keyword_counts: dict[str, int] = {}
    location_names: list[str] = []
    for context in contexts_all:
        for keyword in context.keywords or []:
            key = str(keyword or "").strip().lower()
            if not key:
                continue
            keyword_counts[key] = keyword_counts.get(key, 0) + 1
        location_name = _extract_location_name(context.location)
        if location_name:
            location_names.append(str(location_name))
    top_keywords = sorted(keyword_counts.items(), key=lambda pair: pair[1], reverse=True)[:8]

    prompt = build_lifelog_surprise_agent_prompt(
        instruction=instruction,
        memory_context=memory_context,
        date_range_label=date_label,
    )
    prompt_response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.agent_prompt_model,
        temperature=settings.agent_prompt_temperature,
        max_output_tokens=512,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="agent_surprise_prompt",
        user_id=user_id,
    )
    parsed = prompt_response.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {}
    headline = (parsed.get("headline") or f"Surprise highlight for {date_label}").strip()
    surprise = (parsed.get("surprise") or "").strip()
    details = parsed.get("supporting_details") if isinstance(parsed.get("supporting_details"), list) else []
    image_prompt = (parsed.get("image_prompt") or "").strip()
    non_entity_contexts = [ctx for ctx in contexts if ctx.context_type != "entity_context"]
    visual_detail = _extract_visual_detail_from_contexts(non_entity_contexts, summaries)
    visual_details = _collect_visual_details(non_entity_contexts, summaries, max_items=3)

    if not surprise:
        filtered_keywords = _filter_surprise_terms([word for word, _ in top_keywords])
        if filtered_keywords:
            surprise = (
                f"An easy-to-miss detail was '{filtered_keywords[0]}', "
                "popping up in the range."
            )
        elif summaries:
            summary_text = (summaries[0].summary or "").strip()
            surprise = f"One standout moment was: {summary_text}" if summary_text else ""
        if not surprise:
            surprise = "A subtle pattern emerged across the range, but the details were sparse."

    if not details:
        detail_items: list[str] = []
        for word in _filter_surprise_terms([word for word, _ in top_keywords[:6]]):
            detail_items.append(word)
        for location in list(dict.fromkeys(location_names))[:3]:
            detail_items.append(location)
        detail_items.extend(_extract_context_anchors(contexts, max_items=3))
        details = detail_items[:6]

    if visual_details:
        primary = visual_details[0]
        surprise = (
            f"Notable detail: {primary}. This kind of small visual cue is easy to overlook."
        )
        details = visual_details + _filter_surprise_terms(details if isinstance(details, list) else [])
        details = details[:6]
    elif visual_detail:
        surprise = (
            f"Notable detail: {visual_detail}. This kind of small visual cue is easy to overlook."
        )
        if isinstance(details, list) and visual_detail not in details:
            details = [visual_detail] + _filter_surprise_terms(details)
            details = details[:6]

    lines = [headline]
    if surprise:
        lines.append(f"Surprise: {surprise}")
    if details:
        lines.append(f"Details: {', '.join(str(detail) for detail in details)}")
    if image_prompt:
        lines.append(f"Image prompt: {image_prompt}")
    evidence_cues = _format_surprise_evidence_cues(
        contexts,
        tz_offset_minutes=resolved_offset,
        max_items=4,
    )
    if evidence_cues:
        lines.append("Evidence cues:")
        lines.extend(evidence_cues)
    assistant_content = "\n".join(lines).strip()

    session_record = await _get_or_create_session(
        session,
        user_id,
        payload.session_id,
        f"Surprise highlight {date_label}",
    )
    now = datetime.now(timezone.utc)
    session_record.updated_at = now
    session_record.last_message_at = now

    user_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="user",
        content=f"Surprise highlight for {date_label}",
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

    source_entries = [
        (context, {"score": max(0.01, 1.0 - idx * 0.03)})
        for idx, context in enumerate(contexts[:12])
    ]
    sources = await _build_sources(session, source_entries, limit=5) if source_entries else []
    if sources:
        assistant_msg.sources = _serialize_sources_for_storage(sources)
    await session.commit()

    return AgentTextResponse(
        message=assistant_content,
        session_id=session_record.id,
        sources=sources,
    )


@router.post("/attachments", response_model=ChatAttachmentResponse)
async def upload_chat_attachment(
    image: UploadFile = File(...),
    session_id: Optional[UUID] = Form(None),
    user_id: UUID = Depends(get_current_user_id),
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
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ChatSessionSummary]:
    stmt = (
        select(ChatSession, func.count(ChatMessage.id))
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id, isouter=True)
        .where(ChatSession.user_id == user_id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.last_message_at.desc())
        .limit(limit)
        .offset(offset)
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
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    debug: bool = False,
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[UUID] = Query(None),
) -> ChatSessionDetail:
    chat_session = await session.get(ChatSession, session_id)
    if not chat_session or chat_session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    msg_stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
    if before_id:
        before_msg = await session.get(ChatMessage, before_id)
        if before_msg and before_msg.session_id == session_id:
            msg_stmt = msg_stmt.where(
                or_(
                    ChatMessage.created_at < before_msg.created_at,
                    and_(
                        ChatMessage.created_at == before_msg.created_at,
                        ChatMessage.id < before_id,
                    ),
                )
            )
    msg_stmt = msg_stmt.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit + 1)
    rows = await session.execute(msg_stmt)
    message_records = list(rows.scalars().all())
    has_more = len(message_records) > limit
    if has_more:
        message_records = message_records[:limit]
    message_records = list(reversed(message_records))
    message_ids = [msg.id for msg in message_records]
    attachments_by_message = await _load_message_attachments(session, message_ids)
    messages = []
    for msg in message_records:
        sources_payload = msg.sources or []
        cleaned_payload, telemetry = _split_sources_payload(sources_payload)
        sources = [ChatSource(**entry) for entry in cleaned_payload if isinstance(entry, dict)]
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
                telemetry=telemetry if debug else None,
            )
        )
    return ChatSessionDetail(
        session_id=chat_session.id,
        title=chat_session.title,
        messages=messages,
        has_more=has_more,
        next_before_id=str(message_records[0].id) if message_records else None,
    )


@router.post("/feedback")
async def submit_feedback(
    payload: ChatFeedbackRequest,
    user_id: UUID = Depends(get_current_user_id),
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
