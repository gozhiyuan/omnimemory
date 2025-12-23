"""Dashboard summary endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DEFAULT_TEST_USER_ID, DataConnection, SourceItem
from ..db.session import get_session


router = APIRouter()


class DashboardStats(BaseModel):
    total_items: int
    processed_items: int
    failed_items: int
    active_connections: int


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> DashboardStats:
    """Return aggregate counts used by the dashboard cards."""

    total_items_stmt = select(func.count(SourceItem.id)).where(SourceItem.user_id == user_id)
    processed_items_stmt = total_items_stmt.where(SourceItem.processing_status == "completed")
    failed_items_stmt = total_items_stmt.where(SourceItem.processing_status == "failed")
    connections_stmt = select(func.count(DataConnection.id)).where(
        DataConnection.user_id == user_id, DataConnection.status == "active"
    )

    total_items = (await session.execute(total_items_stmt)).scalar_one()
    processed_items = (await session.execute(processed_items_stmt)).scalar_one()
    failed_items = (await session.execute(failed_items_stmt)).scalar_one()
    active_connections = (await session.execute(connections_stmt)).scalar_one()

    return DashboardStats(
        total_items=total_items,
        processed_items=processed_items,
        failed_items=failed_items,
        active_connections=active_connections,
    )
