"""Upload orchestration endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
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
    client_tz_offset_minutes: Optional[int] = Field(
        default=None,
        description="Client timezone offset in minutes (Date.getTimezoneOffset)",
    )
    event_time_window_start: Optional[datetime] = Field(
        default=None,
        description="Optional window start for manual time overrides",
    )
    event_time_window_end: Optional[datetime] = Field(
        default=None,
        description="Optional window end for manual time overrides",
    )
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

class BatchIngestRequest(BaseModel):
    items: list[IngestRequest] = Field(default_factory=list, min_length=1)


class BatchIngestItemResponse(BaseModel):
    index: int
    status: str
    item_id: Optional[str] = None
    task_id: Optional[str] = None
    error: Optional[str] = None


class BatchIngestResponse(BaseModel):
    batch_id: str
    accepted: int
    failed: int
    results: list[BatchIngestItemResponse]


def _validate_ingest_request(request: IngestRequest, settings) -> Optional[tuple[int, str]]:
    if request.size_bytes is not None and request.size_bytes > settings.media_max_bytes:
        return 413, f"File exceeds max size of {settings.media_max_bytes} bytes"
    if request.duration_sec is not None:
        if request.item_type == ItemType.video and request.duration_sec > settings.video_max_duration_sec:
            return 400, f"Video exceeds max duration of {settings.video_max_duration_sec} seconds"
        if request.item_type == ItemType.audio and request.duration_sec > settings.audio_max_duration_sec:
            return 400, f"Audio exceeds max duration of {settings.audio_max_duration_sec} seconds"
    return None


def _resolve_event_time(request: IngestRequest, provider: str) -> tuple[str, float]:
    event_time_source = "provider" if provider != "upload" else "client"
    event_time_confidence = 0.85 if provider != "upload" else 0.7
    if not request.captured_at:
        event_time_source = "server"
        event_time_confidence = 0.4
    elif request.event_time_override:
        event_time_source = "manual"
        event_time_confidence = 0.95
    return event_time_source, event_time_confidence


def _build_payload(request: IngestRequest, item_id: UUID) -> dict:
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
    if request.client_tz_offset_minutes is not None:
        payload["client_tz_offset_minutes"] = request.client_tz_offset_minutes
    if request.event_time_window_start:
        payload["event_time_window_start"] = request.event_time_window_start.isoformat()
    if request.event_time_window_end:
        payload["event_time_window_end"] = request.event_time_window_end.isoformat()
    if request.reprocess_duplicates is not None:
        payload["reprocess_duplicates"] = request.reprocess_duplicates
    if request.event_time_override is not None:
        payload["event_time_override"] = request.event_time_override
    return payload


@router.post("/ingest", response_model=IngestResponse)
async def ingest_item(
    request: IngestRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Persist the source item and enqueue processing."""

    settings = get_settings()
    validation_error = _validate_ingest_request(request, settings)
    if validation_error:
        status_code, detail = validation_error
        raise HTTPException(status_code=status_code, detail=detail)

    user = await session.get(User, request.user_id)
    if user is None:
        user = User(id=request.user_id)
        session.add(user)

    item_id = uuid4()
    provider = request.provider or "upload"
    event_time_source, event_time_confidence = _resolve_event_time(request, provider)
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

    payload = _build_payload(request, item_id)

    task = process_item.delay(payload)

    return IngestResponse(item_id=str(item_id), task_id=task.id)


@router.post("/ingest/batch", response_model=BatchIngestResponse)
async def ingest_batch(
    request: BatchIngestRequest,
    session: AsyncSession = Depends(get_session),
) -> BatchIngestResponse:
    """Persist source items in bulk and enqueue processing per item."""

    settings = get_settings()
    if not request.items:
        raise HTTPException(status_code=400, detail="Batch payload must include at least one item")
    if len(request.items) > settings.ingest_batch_limit:
        raise HTTPException(
            status_code=413,
            detail=f"Batch exceeds max size of {settings.ingest_batch_limit} items",
        )

    results: list[Optional[BatchIngestItemResponse]] = [None] * len(request.items)
    valid_entries: list[tuple[int, IngestRequest]] = []
    valid_user_ids: set[UUID] = set()

    for index, item in enumerate(request.items):
        error = _validate_ingest_request(item, settings)
        if error:
            _, detail = error
            results[index] = BatchIngestItemResponse(index=index, status="rejected", error=detail)
            continue
        valid_entries.append((index, item))
        valid_user_ids.add(item.user_id)

    source_items: list[SourceItem] = []
    if valid_entries:
        existing_users: set[UUID] = set()
        user_rows = await session.execute(select(User).where(User.id.in_(valid_user_ids)))
        existing_users = {user.id for user in user_rows.scalars().all()}
        for user_id in valid_user_ids - existing_users:
            session.add(User(id=user_id))

        for _, item in valid_entries:
            item_id = uuid4()
            provider = item.provider or "upload"
            event_time_source, event_time_confidence = _resolve_event_time(item, provider)
            source_items.append(
                SourceItem(
                    id=item_id,
                    user_id=item.user_id,
                    provider=provider,
                    external_id=item.external_id,
                    storage_key=item.storage_key,
                    item_type=item.item_type.value,
                    captured_at=item.captured_at,
                    event_time_utc=item.captured_at,
                    event_time_source=event_time_source,
                    event_time_confidence=event_time_confidence,
                    content_type=item.content_type,
                    original_filename=item.original_filename,
                    processing_status="pending",
                )
            )
        session.add_all(source_items)
        await session.commit()

        for (index, item), source_item in zip(valid_entries, source_items):
            payload = _build_payload(item, source_item.id)
            task = process_item.delay(payload)
            results[index] = BatchIngestItemResponse(
                index=index,
                status="queued",
                item_id=str(source_item.id),
                task_id=task.id,
            )

    finalized = [entry for entry in results if entry is not None]
    accepted = sum(1 for entry in finalized if entry.status == "queued")
    failed = len(finalized) - accepted

    return BatchIngestResponse(
        batch_id=str(uuid4()),
        accepted=accepted,
        failed=failed,
        results=finalized,
    )
