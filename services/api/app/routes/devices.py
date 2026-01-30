"""Device (ESP32) pairing and ingest endpoints."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user_id
from ..config import get_settings
from ..db.models import Device, SourceItem, User
from ..db.session import get_session
from ..routes.storage import sanitize_filename
from ..storage import get_storage_provider
from ..tasks.process_item import process_item


router = APIRouter()


def _hash_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _derive_device_token(device_id: UUID, token_salt: str, secret: str) -> str:
    raw = f"{device_id}:{token_salt}"
    digest = hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _split_upload_url(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise HTTPException(status_code=500, detail="Presigned upload URL is missing host.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return parsed.hostname, port, path


async def _get_current_device(
    x_device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    session: AsyncSession = Depends(get_session),
) -> Device:
    if not x_device_token:
        raise HTTPException(status_code=401, detail="Missing X-Device-Token header.")

    settings = get_settings()
    token_hash = _hash_token(x_device_token, settings.device_token_secret)
    result = await session.execute(
        select(Device).where(Device.device_token_hash == token_hash, Device.revoked_at.is_(None))
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token.")

    device.last_seen_at = datetime.now(timezone.utc)
    device.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return device


def _resolve_event_time(captured_at: Optional[datetime], ntp_synced: Optional[bool]) -> tuple[str, float]:
    if not captured_at:
        return "server", 0.4
    if ntp_synced:
        return "client", 0.9
    return "client", 0.6


class PairRequest(BaseModel):
    name: Optional[str] = Field(default=None, description="Optional device name")


class PairResponse(BaseModel):
    device_id: UUID
    pairing_code: str
    expires_at: datetime


class ActivateRequest(BaseModel):
    pairing_code: str = Field(..., min_length=6, max_length=6)


class ActivateResponse(BaseModel):
    device_id: UUID
    device_token: str


class DeviceItemType(str, Enum):
    photo = "photo"
    audio = "audio"
    video = "video"
    document = "document"


class UploadUrlRequest(BaseModel):
    filename: str = Field(..., description="Original filename for key generation")
    content_type: str = Field(default="image/jpeg", description="MIME type")
    seq: Optional[int] = Field(default=None, ge=0, description="Optional sequence id for stable object_key")
    path_date: Optional[date] = Field(default=None, description="Optional date for path prefix (YYYY-MM-DD)")


class UploadUrlResponse(BaseModel):
    upload_host: str
    upload_port: int
    upload_path: str
    object_key: str
    upload_headers: dict[str, str] = Field(default_factory=dict)


class DeviceIngestRequest(BaseModel):
    object_key: str = Field(..., description="Key/path in object storage")
    seq: int = Field(..., ge=0, description="Monotonic device sequence id")
    captured_at: Optional[datetime] = Field(default=None, description="Original capture timestamp")
    ntp_synced: Optional[bool] = Field(default=None, description="True if NTP sync succeeded")
    content_type: Optional[str] = Field(default=None, description="MIME type")
    original_filename: Optional[str] = Field(default=None, description="Original filename if available")
    item_type: Optional["DeviceItemType"] = Field(default=None, description="Asset type")


class DeviceIngestResponse(BaseModel):
    status: str
    item_id: Optional[str] = None
    task_id: Optional[str] = None


class TelemetryRequest(BaseModel):
    uptime_seconds: Optional[int] = None
    sd_used_mb: Optional[int] = None
    sd_free_mb: Optional[int] = None
    backlog_count: Optional[int] = None
    battery_mv: Optional[int] = None
    wifi_rssi: Optional[int] = None
    firmware_version: Optional[str] = None


class TelemetryResponse(BaseModel):
    status: str = "ok"


class DeviceConfigResponse(BaseModel):
    version: int = 1
    capture_interval_sec: int = 30
    upload_batch_interval_min: int = 15
    sd_min_free_percent: int = 15
    burst_enabled: bool = False
    burst_interval_sec: int = 180


@router.post("/pair", response_model=PairResponse)
async def pair_device(
    request: PairRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> PairResponse:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.device_pairing_code_ttl_minutes)

    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id)
        session.add(user)

    token_salt = secrets.token_urlsafe(16)
    pairing_code = ""
    pairing_code_hash = ""

    for _ in range(8):
        candidate = f"{secrets.randbelow(1_000_000):06d}"
        candidate_hash = _hash_token(candidate, settings.device_token_secret)
        existing = await session.execute(
            select(Device).where(
                Device.pairing_code_hash == candidate_hash,
                Device.pairing_code_expires_at > now,
            )
        )
        if existing.scalar_one_or_none() is None:
            pairing_code = candidate
            pairing_code_hash = candidate_hash
            break

    if not pairing_code:
        raise HTTPException(status_code=503, detail="Unable to generate pairing code.")

    device = Device(
        user_id=user_id,
        name=request.name,
        token_salt=token_salt,
        pairing_code_hash=pairing_code_hash,
        pairing_code_expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)

    return PairResponse(device_id=device.id, pairing_code=pairing_code, expires_at=expires_at)


@router.post("/activate", response_model=ActivateResponse)
async def activate_device(
    request: ActivateRequest,
    session: AsyncSession = Depends(get_session),
) -> ActivateResponse:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    code_hash = _hash_token(request.pairing_code, settings.device_token_secret)

    result = await session.execute(
        select(Device).where(
            Device.pairing_code_hash == code_hash,
            Device.pairing_code_expires_at > now,
            Device.revoked_at.is_(None),
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Invalid or expired pairing code.")

    device_token = _derive_device_token(device.id, device.token_salt, settings.device_token_secret)
    if device.device_token_hash is None:
        device.device_token_hash = _hash_token(device_token, settings.device_token_secret)
        device.updated_at = now
        await session.commit()

    return ActivateResponse(device_id=device.id, device_token=device_token)


@router.post("/upload-url", response_model=UploadUrlResponse)
async def get_device_upload_url(
    request: UploadUrlRequest,
    device: Device = Depends(_get_current_device),
) -> UploadUrlResponse:
    settings = get_settings()
    storage = get_storage_provider()

    safe_name = sanitize_filename(request.filename)
    prefix = f"devices/{device.id}"
    path_date = request.path_date or datetime.now(timezone.utc).date()
    prefix = f"{prefix}/{path_date:%Y/%m/%d}"

    if request.seq is not None:
        key = f"{prefix}/{request.seq}-{safe_name}"
    else:
        key = f"{prefix}/{uuid4()}-{safe_name}"

    signed = storage.get_presigned_upload(key, request.content_type, settings.presigned_url_ttl_seconds)
    if not signed.get("url"):
        raise HTTPException(status_code=501, detail="Presigned upload URL not implemented for current provider.")

    upload_host, upload_port, upload_path = _split_upload_url(signed["url"])
    object_key = signed.get("key", key)

    return UploadUrlResponse(
        upload_host=upload_host,
        upload_port=upload_port,
        upload_path=upload_path,
        object_key=object_key,
        upload_headers=signed.get("headers", {}),
    )


@router.post("/ingest", response_model=DeviceIngestResponse)
async def ingest_device_item(
    request: DeviceIngestRequest,
    device: Device = Depends(_get_current_device),
    session: AsyncSession = Depends(get_session),
) -> DeviceIngestResponse:
    settings = get_settings()
    existing = await session.execute(
        select(SourceItem).where(SourceItem.device_id == device.id, SourceItem.device_seq == request.seq)
    )
    existing_item = existing.scalar_one_or_none()
    if existing_item:
        return DeviceIngestResponse(status="duplicate", item_id=str(existing_item.id))

    user = await session.get(User, device.user_id)
    if user is None:
        user = User(id=device.user_id)
        session.add(user)

    content_type = request.content_type
    if not content_type:
        if request.object_key.lower().endswith(".wav"):
            content_type = "audio/wav"
        else:
            content_type = "image/jpeg"

    item_type = request.item_type.value if request.item_type else None
    if content_type.startswith("audio/") or request.object_key.lower().endswith(".wav"):
        item_type = "audio"
    elif content_type.startswith("video/"):
        item_type = "video"
    elif not item_type:
        item_type = "photo"

    event_time_source, event_time_confidence = _resolve_event_time(request.captured_at, request.ntp_synced)
    original_filename = request.original_filename or request.object_key.split("/")[-1]

    item_id = uuid4()
    source_item = SourceItem(
        id=item_id,
        user_id=device.user_id,
        device_id=device.id,
        device_seq=request.seq,
        provider="device",
        external_id=f"{device.id}:{request.seq}",
        storage_key=request.object_key,
        item_type=item_type,
        captured_at=request.captured_at,
        event_time_utc=request.captured_at,
        event_time_source=event_time_source,
        event_time_confidence=event_time_confidence,
        content_type=content_type,
        original_filename=original_filename,
        processing_status="pending",
    )
    session.add(source_item)
    await session.commit()

    payload = {
        "item_id": str(item_id),
        "storage_key": request.object_key,
        "item_type": item_type,
        "user_id": str(device.user_id),
        "captured_at": request.captured_at.isoformat() if request.captured_at else None,
        "content_type": content_type,
        "original_filename": original_filename,
    }

    task = process_item.delay(payload)
    return DeviceIngestResponse(status="queued", item_id=str(item_id), task_id=task.id)


@router.post("/telemetry", response_model=TelemetryResponse)
async def ingest_telemetry(
    _request: TelemetryRequest,
    _device: Device = Depends(_get_current_device),
) -> TelemetryResponse:
    return TelemetryResponse()


@router.get("/config", response_model=DeviceConfigResponse)
async def get_device_config(
    _device: Device = Depends(_get_current_device),
) -> DeviceConfigResponse:
    return DeviceConfigResponse()
