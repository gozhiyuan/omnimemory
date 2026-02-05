"""Memory API endpoints for external tools (OpenClaw, agents)."""

from __future__ import annotations

from datetime import date as Date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user_id
from ..chat import build_query_plan_with_parsed, plan_retrieval
from ..chat.query_understanding import plan_to_dict
from ..config import get_settings
from ..db.models import ProcessedContext
from ..db.session import get_session
from ..pipeline.utils import ensure_tz_aware
from ..rag import retrieve_context_hits
from ..user_settings import resolve_user_tz_offset_minutes
from .timeline import TimelineDay, TimelineEpisodeDetail, get_timeline, get_timeline_episode_detail


router = APIRouter()


class MemoryTimeRange(BaseModel):
    start: Optional[Date] = None
    end: Optional[Date] = None


class MemorySearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: Optional[int] = Field(default=None, ge=1)
    time_range: Optional[MemoryTimeRange] = None
    context_types: Optional[list[str]] = None
    tz_offset_minutes: Optional[int] = None
    debug: bool = False
    query_plan: Optional[dict] = None


class MemoryHit(BaseModel):
    context_id: str
    episode_id: Optional[str] = None
    context_type: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    event_time_utc: Optional[str] = None
    score: Optional[float] = None


class MemorySearchResponse(BaseModel):
    hits: list[MemoryHit]
    query_plan: Optional[dict] = None
    debug: Optional[dict] = None


class MemoryContextDetail(BaseModel):
    context_id: str
    context_type: str
    title: Optional[str] = None
    summary: Optional[str] = None
    event_time_utc: Optional[str] = None
    start_time_utc: Optional[str] = None
    end_time_utc: Optional[str] = None
    is_episode: bool = False
    source_item_ids: list[str] = []


def _local_dates_to_utc_range(
    start_date: Date,
    end_date: Date,
    offset: timedelta,
) -> tuple[datetime, datetime]:
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
    end = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset
    return start, end


async def _resolve_tz_offset(
    session: AsyncSession,
    user_id: UUID,
    tz_offset_minutes: Optional[int],
    local_date: Optional[Date] = None,
) -> int:
    return await resolve_user_tz_offset_minutes(
        session,
        user_id,
        tz_offset_minutes=tz_offset_minutes,
        local_date=local_date,
    )


def _extract_time_range(
    time_range: Optional[MemoryTimeRange],
    tz_offset_minutes: Optional[int],
) -> tuple[Optional[datetime], Optional[datetime]]:
    if not time_range or not time_range.start:
        return None, None
    start_date = time_range.start
    end_date = time_range.end or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    offset = timedelta(minutes=tz_offset_minutes or 0)
    start_time, end_time = _local_dates_to_utc_range(start_date, end_date + timedelta(days=1), offset)
    return start_time, end_time


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories(
    request: MemorySearchRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> MemorySearchResponse:
    settings = get_settings()
    tz_offset = await _resolve_tz_offset(
        session,
        user_id,
        tz_offset_minutes=request.tz_offset_minutes,
        local_date=request.time_range.start if request.time_range else None,
    )

    plan, parsed = await build_query_plan_with_parsed(
        request.query,
        history=[],
        tz_offset_minutes=tz_offset,
        settings=settings,
    )
    retrieval = plan_retrieval(plan)

    start_time, end_time = _extract_time_range(request.time_range, tz_offset)
    context_types = set(request.context_types) if request.context_types else retrieval.context_types
    top_k = request.limit or retrieval.limit or settings.chat_context_limit

    intent, _parsed_query, hits = await retrieve_context_hits(
        request.query,
        user_id=user_id,
        top_k=top_k,
        settings=settings,
        tz_offset_minutes=tz_offset,
        session=session,
        parsed_override=parsed,
        intent_override=plan.intent,
        query_type=plan.query_type,
        context_types=context_types,
        allow_rerank=retrieval.allow_rerank,
        start_time_override=start_time,
        end_time_override=end_time,
    )

    output_hits: list[MemoryHit] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        context_id = str(hit.get("context_id") or payload.get("context_id") or "")
        if not context_id:
            continue
        context_type = payload.get("context_type")
        is_episode = bool(payload.get("is_episode"))
        event_time = payload.get("event_time_utc")
        output_hits.append(
            MemoryHit(
                context_id=context_id,
                episode_id=context_id if is_episode else None,
                context_type=context_type,
                title=payload.get("title"),
                summary=payload.get("summary"),
                event_time_utc=event_time,
                score=float(hit.get("combined_score") or hit.get("score") or 0.0),
            )
        )

    debug = None
    if request.debug:
        debug = {
            "intent": intent,
            "query_type": plan.query_type,
            "candidate_count": len(hits),
        }

    return MemorySearchResponse(
        hits=output_hits,
        query_plan=plan_to_dict(plan),
        debug=debug,
    )


@router.get("/timeline/{date}", response_model=list[TimelineDay])
async def get_memory_timeline(
    date: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
    tz_offset_minutes: Optional[int] = None,
    limit: int = 200,
) -> list[TimelineDay]:
    try:
        local_date = Date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")
    tz_offset = await _resolve_tz_offset(session, user_id, tz_offset_minutes, local_date)
    return await get_timeline(
        user_id=user_id,
        session=session,
        limit=limit,
        provider=None,
        start_date=local_date,
        end_date=local_date,
        tz_offset_minutes=tz_offset,
    )


@router.get("/episode/{episode_id}", response_model=TimelineEpisodeDetail)
async def get_memory_episode(
    episode_id: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TimelineEpisodeDetail:
    return await get_timeline_episode_detail(
        episode_id=episode_id,
        user_id=user_id,
        session=session,
    )


@router.get("/context/{context_id}", response_model=MemoryContextDetail)
async def get_memory_context(
    context_id: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> MemoryContextDetail:
    try:
        context_uuid = UUID(context_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid context ID")

    context = await session.get(ProcessedContext, context_uuid)
    if not context or context.user_id != user_id:
        raise HTTPException(status_code=404, detail="Context not found")

    event_time = context.event_time_utc or context.created_at
    start_time = context.start_time_utc
    end_time = context.end_time_utc

    return MemoryContextDetail(
        context_id=str(context.id),
        context_type=context.context_type,
        title=context.title,
        summary=context.summary,
        event_time_utc=ensure_tz_aware(event_time).isoformat() if event_time else None,
        start_time_utc=ensure_tz_aware(start_time).isoformat() if start_time else None,
        end_time_utc=ensure_tz_aware(end_time).isoformat() if end_time else None,
        is_episode=bool(context.is_episode),
        source_item_ids=[str(value) for value in context.source_item_ids or []],
    )
