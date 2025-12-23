"""Timeline aggregation endpoints."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DEFAULT_TEST_USER_ID, SourceItem
from ..db.session import get_session


router = APIRouter()


class TimelineItem(BaseModel):
    item_id: UUID = Field(..., alias="id")
    item_type: str
    captured_at: Optional[str]
    processed: bool

    class Config:
        allow_population_by_field_name = True


class TimelineDay(BaseModel):
    date: date
    item_count: int
    items: List[TimelineItem]


@router.get("/", response_model=list[TimelineDay])
async def get_timeline(
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
    limit: int = 200,
) -> list[TimelineDay]:
    """Return a grouped timeline of items for the user.

    Items are ordered by capture timestamp (falling back to creation time) and
    grouped by their calendar date. A modest limit keeps payloads predictable
    for UI rendering while still surfacing recent activity.
    """

    stmt = (
        select(SourceItem)
        .where(SourceItem.user_id == user_id)
        .order_by(SourceItem.captured_at.desc().nulls_last(), SourceItem.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    items: list[SourceItem] = list(result.scalars().all())

    grouped: dict[date, list[SourceItem]] = defaultdict(list)
    for item in items:
        item_date = (item.captured_at or item.created_at).date()
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
                        captured_at=(item.captured_at or item.created_at).isoformat(),
                        processed=item.processing_status == "completed",
                    )
                    for item in day_items
                ],
            )
        )

    return timeline
