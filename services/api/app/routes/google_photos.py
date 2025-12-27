"""Google Photos OAuth and sync endpoints."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import DEFAULT_TEST_USER_ID, DataConnection, User
from ..db.session import get_session
from ..google_photos import build_google_photos_auth_url, exchange_google_photos_code
from ..tasks.google_photos import sync_google_photos


router = APIRouter()


class OAuthUrlResponse(BaseModel):
    auth_url: str


class CallbackResponse(BaseModel):
    status: str = "connected"
    connection_id: str
    task_id: str


class SyncRequest(BaseModel):
    connection_id: Optional[UUID] = Field(default=None)
    user_id: UUID = Field(default=DEFAULT_TEST_USER_ID)


class SyncResponse(BaseModel):
    status: str = "queued"
    connection_id: str
    task_id: str


@router.get("/oauth", response_model=OAuthUrlResponse)
async def google_photos_oauth() -> OAuthUrlResponse:
    settings = get_settings()
    auth_url = build_google_photos_auth_url(settings)
    return OAuthUrlResponse(auth_url=auth_url)


@router.get("/callback", response_model=CallbackResponse)
async def google_photos_callback(
    code: str,
    user_id: UUID = DEFAULT_TEST_USER_ID,
    session: AsyncSession = Depends(get_session),
) -> CallbackResponse:
    settings = get_settings()
    token_payload = exchange_google_photos_code(settings, code)

    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id)
        session.add(user)

    result = await session.execute(
        select(DataConnection).where(
            DataConnection.user_id == user_id,
            DataConnection.provider == "google_photos",
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = DataConnection(
            user_id=user_id,
            provider="google_photos",
            status="active",
            config={},
            oauth_token=token_payload,
        )
        session.add(connection)
    else:
        connection.oauth_token = token_payload
        connection.status = "active"

    await session.commit()

    task = sync_google_photos.delay(str(connection.id))

    return CallbackResponse(connection_id=str(connection.id), task_id=task.id)


@router.post("/sync", response_model=SyncResponse)
async def google_photos_sync(
    request: SyncRequest,
    session: AsyncSession = Depends(get_session),
) -> SyncResponse:
    connection: Optional[DataConnection] = None
    if request.connection_id:
        connection = await session.get(DataConnection, request.connection_id)
    else:
        result = await session.execute(
            select(DataConnection).where(
                DataConnection.user_id == request.user_id,
                DataConnection.provider == "google_photos",
            )
        )
        connection = result.scalar_one_or_none()

    if connection is None:
        raise HTTPException(status_code=404, detail="Google Photos connection not found")

    task = sync_google_photos.delay(str(connection.id))
    return SyncResponse(connection_id=str(connection.id), task_id=task.id)
