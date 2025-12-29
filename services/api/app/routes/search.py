"""Simple search endpoint backed by Qdrant."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DEFAULT_TEST_USER_ID, ProcessedContext
from ..db.session import get_session
from ..vectorstore import search_contexts


router = APIRouter()


@router.get("/", summary="Search processed contexts")
async def search_items(
    q: str = Query(..., description="Search query"),
    limit: int = Query(5, ge=1, le=50),
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    results = search_contexts(q, limit=limit, user_id=str(user_id))
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

    enriched_results = []
    for result in results:
        context_id_raw = result.get("context_id")
        context_id: Optional[UUID] = None
        try:
            context_id = UUID(context_id_raw)
        except (TypeError, ValueError):
            context_id = None
        context = contexts_by_id.get(context_id) if context_id else None
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
