"""Upload orchestration endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

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
    user_id: str = Field(default="test-user", description="Owner identifier")
    captured_at: Optional[datetime] = Field(default=None, description="Original capture timestamp")


class IngestResponse(BaseModel):
    item_id: str
    task_id: str
    status: str = "queued"


@router.post("/ingest", response_model=IngestResponse)
def ingest_item(request: IngestRequest) -> IngestResponse:
    item_id = str(uuid4())
    task = process_item.delay(
        {
            "item_id": item_id,
            "storage_key": request.storage_key,
            "item_type": request.item_type.value,
            "user_id": request.user_id,
            "captured_at": request.captured_at.isoformat() if request.captured_at else None,
        }
    )

    return IngestResponse(item_id=item_id, task_id=task.id)

