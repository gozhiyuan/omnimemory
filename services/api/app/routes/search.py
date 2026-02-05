"""Simple search endpoint backed by Qdrant."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user_id
from ..db.models import ProcessedContext, SourceItem
from ..db.session import get_session
from ..vectorstore import search_contexts
from ..user_settings import resolve_user_tz_offset_minutes


router = APIRouter()


@router.get("/", summary="Search processed contexts")
async def search_items(
    q: str = Query(..., description="Search query"),
    limit: int = Query(5, ge=1, le=50),
    start_date: Optional[date] = Query(default=None, description="Filter start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(default=None, description="Filter end date (YYYY-MM-DD)"),
    provider: Optional[str] = Query(default=None, description="Filter by source provider"),
    tz_offset_minutes: Optional[int] = Query(default=None, description="Timezone offset minutes"),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    filter_start: Optional[datetime] = None
    filter_end: Optional[datetime] = None
    if start_date or end_date:
        offset_now = await resolve_user_tz_offset_minutes(
            session,
            user_id,
            tz_offset_minutes=tz_offset_minutes,
        )
        local_today = (datetime.now(timezone.utc) - timedelta(minutes=offset_now)).date()
        range_end = end_date or local_today
        range_start = start_date or range_end
        if range_start > range_end:
            range_start, range_end = range_end, range_start
        offset_minutes = await resolve_user_tz_offset_minutes(
            session,
            user_id,
            tz_offset_minutes=tz_offset_minutes,
            local_date=range_start,
        )
        offset = timedelta(minutes=offset_minutes)
        filter_start = datetime.combine(range_start, time.min, tzinfo=timezone.utc) + offset
        filter_end = datetime.combine(range_end, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)

    provider_filter = provider.strip() if provider else None

    results = search_contexts(
        q,
        limit=limit,
        user_id=str(user_id),
        is_episode=True,
        start_time=filter_start,
        end_time=filter_end,
    )
    if len(results) < limit:
        fallback = search_contexts(
            q,
            limit=limit * 2,
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
            if len(results) >= limit:
                break
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

    provider_source_ids: Optional[set[UUID]] = None
    if provider_filter and contexts_by_id:
        source_ids: set[UUID] = set()
        for context in contexts_by_id.values():
            source_ids.update(context.source_item_ids)
        if source_ids:
            provider_stmt = select(SourceItem.id).where(
                SourceItem.id.in_(source_ids),
                SourceItem.provider == provider_filter,
            )
            provider_rows = await session.execute(provider_stmt)
            provider_source_ids = {row[0] for row in provider_rows.fetchall()}
        else:
            provider_source_ids = set()

    enriched_results = []
    for result in results:
        context_id_raw = result.get("context_id")
        context_id: Optional[UUID] = None
        try:
            context_id = UUID(context_id_raw)
        except (TypeError, ValueError):
            context_id = None
        context = contexts_by_id.get(context_id) if context_id else None
        if provider_source_ids is not None:
            if not context:
                continue
            if not any(source_id in provider_source_ids for source_id in context.source_item_ids):
                continue
        enriched_results.append(
            {
                "context_id": context_id_raw,
                "score": result.get("score"),
                "context_type": context.context_type if context else None,
                "title": context.title if context else None,
                "summary": context.summary if context else None,
                "event_time_utc": context.event_time_utc.isoformat() if context else None,
                "source_item_ids": [str(value) for value in context.source_item_ids]
                if context
                else [],
                "payload": result.get("payload"),
            }
        )

    return {"query": q, "results": enriched_results}
