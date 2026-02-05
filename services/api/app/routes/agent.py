"""Agent chat endpoints (ADK-backed)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import run_agent_message
from .chat import ChatSource
from ..auth import get_current_user_id
from ..config import get_settings
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

    return AgentChatResponse(
        message=message,
        session_id=session_id,
        sources=sources,
        debug=debug_payload,
    )
