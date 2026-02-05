"""Agent chat endpoints (ADK-backed)."""

from __future__ import annotations

from typing import Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import run_agent_message
from .chat import ChatSource, _serialize_sources_for_storage
from ..auth import get_current_user_id
from ..config import get_settings
from ..db.models import ChatMessage, ChatSession
from ..db.session import get_session
from ..user_settings import resolve_user_tz_offset_minutes


router = APIRouter()


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    tz_offset_minutes: Optional[int] = None
    debug: bool = False


class AgentChatResponse(BaseModel):
    message: str
    session_id: str
    sources: list[ChatSource] = []
    debug: Optional[dict] = None


def _build_agent_session_title(message: str) -> str:
    cleaned = (message or "").strip()
    if not cleaned:
        return "Agent chat"
    words = cleaned.split()
    return " ".join(words[:8]) if words else "Agent chat"


async def _get_or_create_agent_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    session_id: str,
    first_message: str,
) -> ChatSession:
    try:
        session_uuid = UUID(session_id)
    except Exception:
        session_uuid = uuid4()
    existing = await session.get(ChatSession, session_uuid)
    if existing and existing.user_id == user_id:
        return existing
    title = _build_agent_session_title(first_message)
    record = ChatSession(id=session_uuid, user_id=user_id, title=title)
    session.add(record)
    await session.flush()
    return record


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    request: AgentChatRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> AgentChatResponse:
    settings = get_settings()
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="Agent mode disabled")

    tz_offset = await resolve_user_tz_offset_minutes(
        session,
        user_id,
        tz_offset_minutes=request.tz_offset_minutes,
        local_date=None,
    )

    try:
        message, session_id, debug_payload, sources = await run_agent_message(
            message=request.message,
            user_id=user_id,
            session=session,
            tz_offset_minutes=tz_offset,
            session_id=request.session_id,
            debug=request.debug,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    session_record = await _get_or_create_agent_session(
        session,
        user_id=user_id,
        session_id=session_id,
        first_message=request.message,
    )
    now = datetime.now(timezone.utc)
    session_record.updated_at = now
    session_record.last_message_at = now

    user_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="user",
        content=request.message,
        sources=[],
        created_at=now,
    )
    assistant_msg = ChatMessage(
        session_id=session_record.id,
        user_id=user_id,
        role="assistant",
        content=message,
        sources=_serialize_sources_for_storage(sources),
        created_at=now + timedelta(milliseconds=1),
    )
    session.add_all([user_msg, assistant_msg])
    await session.commit()

    return AgentChatResponse(
        message=message,
        session_id=str(session_record.id),
        sources=sources,
        debug=debug_payload,
    )
