"""OpenClaw integration endpoints.

Provides optimized API endpoints for OpenClaw tool consumption.
These endpoints return concise, tool-friendly response formats.
"""

from __future__ import annotations

import asyncio
import mimetypes
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..auth import get_current_user_id
from ..config import get_settings
from ..db.models import (
    DerivedArtifact,
    ProcessedContext,
    SourceItem,
    UserSettings,
)
from ..db.session import get_session
from ..storage import get_storage_provider
from ..vectorstore import search_contexts
from ..pipeline.utils import ensure_tz_aware
from ..rag import retrieve_context_hits
from ..ai.prompt_manager import get_prompt_manager
from ..ai.prompt_manifest import get_prompt_names, get_api_updatable_prompts, get_prompt_spec
from ..user_settings import (
    fetch_user_settings,
    resolve_annotation_defaults,
    resolve_user_tz_offset_minutes,
)


router = APIRouter()


_CONTENT_TYPE_OVERRIDES = {
    "heic": "image/heic",
    "heif": "image/heif",
    "heic-sequence": "image/heic-sequence",
    "heif-sequence": "image/heif-sequence",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "m4v": "video/x-m4v",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
}


def _infer_content_type(storage_key: Optional[str], original_filename: Optional[str]) -> Optional[str]:
    """Infer content type from filename or storage key."""
    candidates = [original_filename or "", storage_key or ""]
    for value in candidates:
        if not value or "." not in value:
            continue
        ext = value.rsplit(".", 1)[-1].lower()
        override = _CONTENT_TYPE_OVERRIDES.get(ext)
        if override:
            return override
        guessed, _ = mimetypes.guess_type(value)
        if guessed:
            return guessed
    return None


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
    tz_offset_minutes: Optional[int] = None


class OpenClawMemoryItem(BaseModel):
    """Memory item optimized for tool response."""

    id: str
    title: str
    summary: str  # Truncated to 500 chars
    date: Optional[str]
    local_date: Optional[str] = None
    local_time: Optional[str] = None
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


class LocationInfo(BaseModel):
    """Location data for ingest request."""

    lat: Optional[float] = None
    lng: Optional[float] = None
    name: Optional[str] = None
    address: Optional[str] = None


class OpenClawIngestRequest(BaseModel):
    """Ingest request from OpenClaw.

    Supports optional context fields for user-provided annotations.
    These are stored as user_annotation ProcessedContext records.
    """

    storage_key: str
    item_type: str  # photo, video, audio
    captured_at: Optional[str] = None  # ISO format
    provider: str = "openclaw"
    content_type: Optional[str] = None
    original_filename: Optional[str] = None
    # Optional context fields for user annotations
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    people: Optional[list[str]] = None
    location: Optional[LocationInfo] = None
    openclaw_context: Optional[dict] = None  # Arbitrary client context


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


async def _resolve_openclaw_date_filters(
    request: OpenClawSearchRequest,
    *,
    session: AsyncSession,
    user_id: UUID,
) -> tuple[Optional[datetime], Optional[datetime], timedelta]:
    filter_start: Optional[datetime] = None
    filter_end: Optional[datetime] = None

    local_date: Optional[date] = None
    if request.date_from:
        try:
            start_date = date.fromisoformat(request.date_from)
            local_date = start_date
        except ValueError:
            pass
    if request.date_to:
        try:
            end_date = date.fromisoformat(request.date_to)
            local_date = local_date or end_date
        except ValueError:
            pass

    if local_date or request.tz_offset_minutes is not None:
        offset_minutes = await resolve_user_tz_offset_minutes(
            session,
            user_id,
            tz_offset_minutes=request.tz_offset_minutes,
            local_date=local_date,
        )
    else:
        offset_minutes = 0
    offset = timedelta(minutes=offset_minutes)

    if request.date_from:
        try:
            start_date = date.fromisoformat(request.date_from)
            filter_start = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
        except ValueError:
            filter_start = None

    if request.date_to:
        try:
            end_date = date.fromisoformat(request.date_to)
            filter_end = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)
        except ValueError:
            filter_end = None

    return filter_start, filter_end, offset


async def _build_openclaw_search_items(
    *,
    session: AsyncSession,
    user_id: UUID,
    results: list[dict[str, Any]],
    offset: timedelta,
    context_types: Optional[list[str]] = None,
) -> OpenClawSearchResponse:
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

    if context_types:
        contexts_by_id = {
            cid: ctx
            for cid, ctx in contexts_by_id.items()
            if ctx.context_type in context_types
        }

    all_source_ids: set[UUID] = set()
    for context in contexts_by_id.values():
        all_source_ids.update(context.source_item_ids)

    thumbnail_urls = await _get_thumbnail_urls(session, list(all_source_ids))

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

        thumbnail_url = None
        for source_id in context.source_item_ids:
            if source_id in thumbnail_urls:
                thumbnail_url = thumbnail_urls[source_id]
                break

        event_time = ensure_tz_aware(context.event_time_utc) if context.event_time_utc else None
        local_time = (event_time - offset) if event_time else None
        score = result.get("combined_score") or result.get("score")
        items.append(
            OpenClawMemoryItem(
                id=str(context.id),
                title=context.title or "Untitled",
                summary=(context.summary or "")[:500],
                date=event_time.isoformat() if event_time else None,
                local_date=local_time.date().isoformat() if local_time else None,
                local_time=local_time.isoformat() if local_time else None,
                type=context.context_type or "unknown",
                thumbnail_url=thumbnail_url,
                keywords=(context.keywords or [])[:10],
                score=float(score) if score is not None else None,
            )
        )

    return OpenClawSearchResponse(
        success=True,
        total=len(items),
        items=items,
    )


# ---------------------------------------------------------------------------
# Prompt API Models
# ---------------------------------------------------------------------------


class PromptMetadata(BaseModel):
    """Metadata for a prompt template."""

    name: str
    description: str
    source: str  # "user" | "bundled" | "inline"
    sha256: str
    version: Optional[str] = None
    updated_at: Optional[datetime] = None
    updatable_via_api: bool = False
    output_format: str = "text"
    required_vars: list[str] = Field(default_factory=list)
    optional_vars: list[str] = Field(default_factory=list)


class PromptListResponse(BaseModel):
    """List of available prompts."""

    success: bool = True
    prompts: list[PromptMetadata]


class PromptContentResponse(BaseModel):
    """Full prompt content with metadata."""

    success: bool = True
    name: str
    content: str
    source: str
    sha256: str
    version: Optional[str] = None
    updated_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptUpdateRequest(BaseModel):
    """Request to update a prompt template."""

    content: str
    metadata: Optional[dict[str, Any]] = None
    sha256: Optional[str] = None  # For optimistic concurrency (alternative to If-Match header)


class PromptUpdateResponse(BaseModel):
    """Response after updating a prompt."""

    success: bool
    name: str
    sha256: str
    updated_at: datetime
    message: str


class PromptDeleteResponse(BaseModel):
    """Response after deleting a user prompt override."""

    success: bool
    name: str
    message: str


# ---------------------------------------------------------------------------
# Settings API Models
# ---------------------------------------------------------------------------


# Whitelist of settings keys that can be read/written via OpenClaw API
OPENCLAW_SETTINGS_WHITELIST = {"openclaw", "profile", "preferences"}


class OpenClawSettingsResponse(BaseModel):
    """Settings response for OpenClaw (whitelist-filtered)."""

    success: bool = True
    settings: dict[str, Any]
    updated_at: Optional[datetime] = None


class OpenClawSettingsUpdateRequest(BaseModel):
    """Request to update settings (only whitelisted keys)."""

    settings: dict[str, Any]


class OpenClawSettingsUpdateResponse(BaseModel):
    """Response after updating settings."""

    success: bool
    settings: dict[str, Any]
    updated_at: datetime
    message: str


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
    filter_start, filter_end, offset = await _resolve_openclaw_date_filters(
        request,
        session=session,
        user_id=user_id,
    )

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

    return await _build_openclaw_search_items(
        session=session,
        user_id=user_id,
        results=results,
        offset=offset,
        context_types=request.context_types,
    )


@router.post("/search-advanced", response_model=OpenClawSearchResponse)
async def search_for_openclaw_advanced(
    request: OpenClawSearchRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> OpenClawSearchResponse:
    """Search memories using the same RAG pipeline as chat."""
    settings = get_settings()
    filter_start, filter_end, offset = await _resolve_openclaw_date_filters(
        request,
        session=session,
        user_id=user_id,
    )
    date_range_override = None
    if filter_start and filter_end:
        date_range_override = (filter_start, filter_end)

    _, _, hits = await retrieve_context_hits(
        request.query,
        user_id=user_id,
        top_k=request.limit,
        settings=settings,
        tz_offset_minutes=request.tz_offset_minutes,
        session=session,
        date_range_override=date_range_override,
        start_time_override=filter_start,
        end_time_override=filter_end,
    )

    return await _build_openclaw_search_items(
        session=session,
        user_id=user_id,
        results=hits,
        offset=offset,
        context_types=request.context_types,
    )


@router.get("/timeline/{date_str}", response_model=OpenClawTimelineResponse)
async def timeline_for_openclaw(
    date_str: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    tz_offset_minutes: Optional[int] = None,
) -> OpenClawTimelineResponse:
    """Get day summary with episodes formatted for OpenClaw tools."""
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}")

    offset_minutes = await resolve_user_tz_offset_minutes(
        session,
        user_id,
        tz_offset_minutes=tz_offset_minutes,
        local_date=target_date,
    )
    offset = timedelta(minutes=offset_minutes)
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

        local_start = start_time - offset
        local_end = end_time - offset
        time_range = f"{local_start.strftime('%H:%M')} - {local_end.strftime('%H:%M')}"

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
    Supports optional context fields (description, tags, people, location) that
    create user_annotation ProcessedContext records.
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

    content_type = (request.content_type or "").strip() or None
    if not content_type:
        content_type = _infer_content_type(request.storage_key, request.original_filename)

    # Create source item
    now = datetime.now(timezone.utc)
    item = SourceItem(
        user_id=user_id,
        storage_key=request.storage_key,
        item_type=request.item_type,
        provider=request.provider,
        captured_at=captured_at,
        event_time_utc=captured_at,
        content_type=content_type,
        original_filename=request.original_filename,
        processing_status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    # Load user defaults (preferences)
    user_settings = await fetch_user_settings(session, user_id)
    defaults = resolve_annotation_defaults(user_settings)
    default_tags = defaults.get("tags") if isinstance(defaults.get("tags"), list) else None
    default_people = defaults.get("people") if isinstance(defaults.get("people"), list) else None
    default_desc = defaults.get("description") if isinstance(defaults.get("description"), str) else None
    if not default_desc:
        default_desc = defaults.get("description_prefix") if isinstance(defaults.get("description_prefix"), str) else None

    description = request.description if request.description is not None else default_desc
    tags = request.tags if request.tags is not None else default_tags
    people = request.people if request.people is not None else default_people

    # Build merged openclaw_context (explicit fields override openclaw_context keys)
    merged_context: Optional[dict] = None
    if request.openclaw_context or description or tags or people or request.location:
        # Start with client-supplied openclaw_context (if any)
        merged_context = dict(request.openclaw_context or {})

        # Explicit fields override openclaw_context keys
        if description is not None:
            merged_context["description"] = description
        if tags is not None:
            merged_context["tags"] = tags
        if people is not None:
            merged_context["people"] = people
        if request.location is not None:
            merged_context["location"] = request.location.model_dump(exclude_none=True)

    # Build task payload
    task_payload = {
        "item_id": str(item.id),
        "user_id": str(user_id),
        "storage_key": request.storage_key,
    }
    if content_type:
        task_payload["content_type"] = content_type
    if merged_context:
        task_payload["openclaw_context"] = merged_context

    # Queue processing task
    celery_app.send_task("pipeline.process_item", args=[task_payload])

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
# Prompt API Endpoints
# ---------------------------------------------------------------------------


@router.get("/prompts", response_model=PromptListResponse)
async def list_prompts(
    user_id: UUID = Depends(get_current_user_id),
) -> PromptListResponse:
    """List all available prompts with metadata.

    Returns prompt names, descriptions, sources, and sha256 hashes.
    Use the sha256 for optimistic concurrency when updating prompts.
    """
    manager = get_prompt_manager()
    prompt_names = get_prompt_names()
    updatable_names = set(get_api_updatable_prompts())

    prompts: list[PromptMetadata] = []
    for name in prompt_names:
        spec = get_prompt_spec(name)
        template = manager.get(name, user_id=str(user_id))

        if template:
            prompts.append(
                PromptMetadata(
                    name=name,
                    description=spec.get("description", "") if spec else "",
                    source=template.source,
                    sha256=template.sha256,
                    version=template.metadata.get("version") if template.metadata else None,
                    updated_at=template.updated_at,
                    updatable_via_api=name in updatable_names,
                    output_format=spec.get("output_format", "text") if spec else "text",
                    required_vars=spec.get("required_vars", []) if spec else [],
                    optional_vars=spec.get("optional_vars", []) if spec else [],
                )
            )

    return PromptListResponse(success=True, prompts=prompts)


@router.get("/prompts/{name}", response_model=PromptContentResponse)
async def get_prompt(
    name: str,
    user_id: UUID = Depends(get_current_user_id),
) -> PromptContentResponse:
    """Get prompt content with metadata.

    Returns the full prompt template content, source, sha256, and metadata.
    Use sha256 in the If-Match header or request body when updating.
    """
    manager = get_prompt_manager()
    template = manager.get(name, user_id=str(user_id))

    if not template:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")

    return PromptContentResponse(
        success=True,
        name=name,
        content=template.content,
        source=template.source,
        sha256=template.sha256,
        version=template.metadata.get("version") if template.metadata else None,
        updated_at=template.updated_at,
        metadata=template.metadata or {},
    )


@router.put("/prompts/{name}", response_model=PromptUpdateResponse)
async def update_prompt(
    name: str,
    request: PromptUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
    if_match: Optional[str] = Header(None, alias="If-Match"),
) -> PromptUpdateResponse:
    """Update a prompt template (creates user override).

    Requires optimistic concurrency control via If-Match header or sha256 in body.
    Only prompts marked as updatable_via_api can be updated.

    The user override takes precedence over bundled/inline defaults.
    """
    # Check if prompt is updatable
    updatable_names = set(get_api_updatable_prompts())
    if name not in updatable_names:
        raise HTTPException(
            status_code=403,
            detail=f"Prompt '{name}' cannot be updated via API",
        )

    # Get expected sha256 from header or body
    expected_sha256 = if_match or request.sha256
    if not expected_sha256:
        raise HTTPException(
            status_code=428,  # Precondition Required
            detail="If-Match header or sha256 in body required for optimistic concurrency",
        )

    # Clean up expected_sha256 (remove quotes if present from header)
    expected_sha256 = expected_sha256.strip('"')

    manager = get_prompt_manager()
    success, message, template = manager.update_prompt(
        name=name,
        user_id=str(user_id),
        content=request.content,
        metadata=request.metadata,
        expected_sha256=expected_sha256,
    )

    if not success:
        if "mismatch" in message.lower() or "conflict" in message.lower():
            raise HTTPException(
                status_code=412,  # Precondition Failed
                detail=message,
            )
        raise HTTPException(status_code=400, detail=message)

    return PromptUpdateResponse(
        success=True,
        name=name,
        sha256=template.sha256 if template else "",
        updated_at=template.updated_at if template else datetime.now(timezone.utc),
        message=f"Prompt '{name}' updated successfully",
    )


@router.delete("/prompts/{name}", response_model=PromptDeleteResponse)
async def delete_prompt(
    name: str,
    user_id: UUID = Depends(get_current_user_id),
) -> PromptDeleteResponse:
    """Delete user's prompt override, reverting to bundled/inline default.

    This only removes the user's custom override. The prompt will still be
    available using the bundled default or inline fallback.
    """
    manager = get_prompt_manager()
    success, message = manager.delete_prompt(name, str(user_id))

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"No user override found for prompt '{name}'",
        )

    return PromptDeleteResponse(
        success=True,
        name=name,
        message=f"User override for prompt '{name}' deleted. Now using default.",
    )


# ---------------------------------------------------------------------------
# Settings API Endpoints
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=OpenClawSettingsResponse)
async def get_openclaw_settings(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> OpenClawSettingsResponse:
    """Get user settings (filtered to OpenClaw-relevant keys).

    Only returns whitelisted keys: openclaw, profile, preferences.
    """
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    record = result.scalar_one_or_none()

    if not record or not record.settings:
        return OpenClawSettingsResponse(
            success=True,
            settings={},
            updated_at=None,
        )

    # Filter to whitelisted keys only
    filtered_settings = {
        key: value
        for key, value in record.settings.items()
        if key in OPENCLAW_SETTINGS_WHITELIST
    }

    return OpenClawSettingsResponse(
        success=True,
        settings=filtered_settings,
        updated_at=record.updated_at,
    )


@router.patch("/settings", response_model=OpenClawSettingsUpdateResponse)
async def update_openclaw_settings(
    request: OpenClawSettingsUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> OpenClawSettingsUpdateResponse:
    """Update user settings (only whitelisted keys).

    Only accepts whitelisted keys: openclaw, profile, preferences.
    Merges with existing settings (does not replace entire settings object).
    """
    # Filter incoming settings to whitelisted keys only
    incoming = request.settings or {}
    filtered_incoming = {
        key: value
        for key, value in incoming.items()
        if key in OPENCLAW_SETTINGS_WHITELIST
    }

    if not filtered_incoming:
        raise HTTPException(
            status_code=400,
            detail=f"No valid settings keys provided. Allowed keys: {', '.join(OPENCLAW_SETTINGS_WHITELIST)}",
        )

    # Get existing settings
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    record = result.scalar_one_or_none()

    # Merge with existing settings
    existing_settings = record.settings if record else {}
    merged_settings = {**existing_settings, **filtered_incoming}

    now = datetime.now(timezone.utc)
    table = UserSettings.__table__
    stmt = insert(table).values(
        {
            table.c.user_id: user_id,
            table.c.settings: merged_settings,
            table.c.updated_at: now,
        }
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.user_id],
        set_={
            table.c.settings: merged_settings,
            table.c.updated_at: now,
        },
    )
    await session.execute(stmt)
    await session.commit()

    # Return only whitelisted keys
    filtered_result = {
        key: value
        for key, value in merged_settings.items()
        if key in OPENCLAW_SETTINGS_WHITELIST
    }

    return OpenClawSettingsUpdateResponse(
        success=True,
        settings=filtered_result,
        updated_at=now,
        message="Settings updated successfully",
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
