"""Upload orchestration endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
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
    provider: Optional[str] = Field(default="upload", description="Source provider name")
    external_id: Optional[str] = Field(default=None, description="Source provider item id")
    captured_at: Optional[datetime] = Field(default=None, description="Original capture timestamp")
    content_type: Optional[str] = Field(default=None, description="MIME type of the asset")
    original_filename: Optional[str] = Field(default=None, description="Original filename if available")
    size_bytes: Optional[int] = Field(default=None, ge=1, description="Asset size in bytes")
    duration_sec: Optional[float] = Field(default=None, ge=0, description="Duration in seconds")
    reprocess_duplicates: Optional[bool] = Field(
        default=None,
        description="Re-run expensive steps even if the item is a duplicate",
    )
    event_time_override: Optional[bool] = Field(
        default=None,
        description="Use the provided captured_at as the event time even if metadata exists",
    )


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

    settings = get_settings()
    if request.size_bytes is not None and request.size_bytes > settings.media_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds max size of {settings.media_max_bytes} bytes",
        )
    if request.duration_sec is not None:
        if request.item_type == ItemType.video and request.duration_sec > settings.video_max_duration_sec:
            raise HTTPException(
                status_code=400,
                detail=f"Video exceeds max duration of {settings.video_max_duration_sec} seconds",
            )
        if request.item_type == ItemType.audio and request.duration_sec > settings.audio_max_duration_sec:
            raise HTTPException(
                status_code=400,
                detail=f"Audio exceeds max duration of {settings.audio_max_duration_sec} seconds",
            )

    user = await session.get(User, request.user_id)
    if user is None:
        user = User(id=request.user_id)
        session.add(user)

    item_id = uuid4()
    provider = request.provider or "upload"
    event_time_source = "provider" if provider != "upload" else "client"
    event_time_confidence = 0.85 if provider != "upload" else 0.7
    if not request.captured_at:
        event_time_source = "server"
        event_time_confidence = 0.4
    elif request.event_time_override:
        event_time_source = "manual"
        event_time_confidence = 0.95
    source_item = SourceItem(
        id=item_id,
        user_id=request.user_id,
        provider=provider,
        external_id=request.external_id,
        storage_key=request.storage_key,
        item_type=request.item_type.value,
        captured_at=request.captured_at,
        event_time_utc=request.captured_at,
        event_time_source=event_time_source,
        event_time_confidence=event_time_confidence,
        content_type=request.content_type,
        original_filename=request.original_filename,
        processing_status="pending",
    )
    session.add(source_item)
    await session.commit()

    payload = {
        "item_id": str(item_id),
        "storage_key": request.storage_key,
        "item_type": request.item_type.value,
        "user_id": str(request.user_id),
        "captured_at": request.captured_at.isoformat() if request.captured_at else None,
        "content_type": request.content_type,
        "original_filename": request.original_filename,
        "size_bytes": request.size_bytes,
        "duration_sec": request.duration_sec,
    }
    if request.reprocess_duplicates is not None:
        payload["reprocess_duplicates"] = request.reprocess_duplicates
    if request.event_time_override is not None:
        payload["event_time_override"] = request.event_time_override

    task = process_item.delay(payload)

    return IngestResponse(item_id=str(item_id), task_id=task.id)
