"""Storage-related endpoints (presigned URLs)."""

from __future__ import annotations

from datetime import date
from typing import Optional
import re

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..config import get_settings
from ..storage import get_storage_provider


router = APIRouter(dependencies=[Depends(get_current_user)])


def sanitize_filename(filename: str) -> str:
    name = filename.strip().split("/")[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    safe = safe.strip("._-")
    return safe or "upload"


class UploadUrlRequest(BaseModel):
    filename: str = Field(..., description="Original filename for key generation")
    content_type: str = Field(..., description="MIME type")
    prefix: Optional[str] = Field(default=None, description="Optional path prefix")
    path_date: Optional[date] = Field(default=None, description="Optional date for path prefix (YYYY-MM-DD)")


class UploadUrlResponse(BaseModel):
    key: str
    url: str
    headers: dict[str, str]


class DownloadUrlRequest(BaseModel):
    key: str


class DownloadUrlResponse(BaseModel):
    key: str
    url: str


@router.post("/upload-url", response_model=UploadUrlResponse)
def create_upload_url(request: UploadUrlRequest) -> UploadUrlResponse:
    settings = get_settings()
    storage = get_storage_provider()

    prefix = (request.prefix or "uploads").strip("/")
    safe_name = sanitize_filename(request.filename)
    if request.path_date:
        prefix = f"{prefix}/{request.path_date:%Y/%m/%d}"
    key = f"{prefix}/{uuid.uuid4()}-{safe_name}"
    signed = storage.get_presigned_upload(key, request.content_type, settings.presigned_url_ttl_seconds)
    if not signed.get("url"):
        raise HTTPException(status_code=501, detail="Presigned upload URL not implemented for current provider")

    return UploadUrlResponse(key=signed.get("key", key), url=signed["url"], headers=signed.get("headers", {}))


@router.post("/download-url", response_model=DownloadUrlResponse)
def create_download_url(request: DownloadUrlRequest) -> DownloadUrlResponse:
    settings = get_settings()
    storage = get_storage_provider()

    signed = storage.get_presigned_download(request.key, settings.presigned_url_ttl_seconds)
    if not signed.get("url"):
        raise HTTPException(status_code=404, detail="Unable to sign download URL for key")

    return DownloadUrlResponse(key=signed.get("key", request.key), url=signed["url"])
