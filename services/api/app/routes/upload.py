"""Upload orchestration endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DEFAULT_TEST_USER_ID, SourceItem, User
from ..db.session import get_session
from ..tasks.process_item import process_item


router = APIRouter()


class ItemType(str, Enum):
    photo = "photo"
    video = "video"
    audio = "audio"
    document = "document"


class IngestRequest(BaseModel):
    storage_key: str = Field(..., description="Key/path in object storage")
    item_type: ItemType = Field(..., description="Asset type")
    user_id: UUID = Field(default=DEFAULT_TEST_USER_ID, description="Owner identifier")
    captured_at: Optional[datetime] = Field(default=None, description="Original capture timestamp")
    content_type: Optional[str] = Field(default=None, description="MIME type of the asset")
    original_filename: Optional[str] = Field(default=None, description="Original filename if available")


class IngestResponse(BaseModel):
    item_id: str
    task_id: str
    status: str = "queued"


@router.post("/ingest", response_model=IngestResponse)
async def ingest_item(
    request: IngestRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Persist the source item and enqueue processing."""

    user = await session.get(User, request.user_id)
    if user is None:
        user = User(id=request.user_id)
        session.add(user)

    item_id = uuid4()
    source_item = SourceItem(
        id=item_id,
        user_id=request.user_id,
        storage_key=request.storage_key,
        item_type=request.item_type.value,
        captured_at=request.captured_at,
        content_type=request.content_type,
        original_filename=request.original_filename,
        processing_status="pending",
    )
    session.add(source_item)
    await session.commit()

    task = process_item.delay(
        {
            "item_id": str(item_id),
            "storage_key": request.storage_key,
            "item_type": request.item_type.value,
            "user_id": str(request.user_id),
            "captured_at": request.captured_at.isoformat() if request.captured_at else None,
            "content_type": request.content_type,
            "original_filename": request.original_filename,
        }
    )

    return IngestResponse(item_id=str(item_id), task_id=task.id)

