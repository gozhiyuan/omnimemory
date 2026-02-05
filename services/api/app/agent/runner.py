"""Runner entrypoint for agent chat."""

from __future__ import annotations

from typing import Optional
from datetime import timedelta
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import select

from ..config import get_settings
from ..db.models import ProcessedContext
from ..pipeline.utils import ensure_tz_aware
from .memory_agent import build_agent
from .tools import AgentMemoryTrace

# TODO: Replace global in-memory session service with a durable per-worker session store.
_SESSION_SERVICE = None
_APP_NAME = "omnimemory_agent"


def _get_session_service():
    global _SESSION_SERVICE
    if _SESSION_SERVICE is None:
        try:
            from google.adk.sessions import InMemorySessionService
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-adk is not installed") from exc
        _SESSION_SERVICE = InMemorySessionService()
    return _SESSION_SERVICE


def _build_runner(agent):
    try:
        from google.adk.runners import Runner
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("google-adk is not installed") from exc
    return Runner(agent=agent, app_name=_APP_NAME, session_service=_get_session_service())


def _build_content(message: str):
    try:
        from google.genai.types import Content, Part
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("google-genai is required for ADK message content") from exc
    return Content(role="user", parts=[Part(text=message)])


async def run_agent_message(
    *,
    message: str,
    user_id: UUID,
    session,
    tz_offset_minutes: Optional[int],
    session_id: Optional[str] = None,
    debug: bool = False,
) -> tuple[str, str, Optional[dict], list]:
    settings = get_settings()
    if not settings.agent_enabled:
        raise RuntimeError("agent mode disabled")

    trace = AgentMemoryTrace()
    agent = build_agent(
        user_id=user_id,
        session=session,
        tz_offset_minutes=tz_offset_minutes,
        settings=settings,
        trace=trace,
    )
    runner = _build_runner(agent)
    session_service = _get_session_service()

    user_key = str(user_id)
    session_id = session_id or str(uuid4())
    try:
        await session_service.create_session(
            app_name=_APP_NAME,
            user_id=user_key,
            session_id=session_id,
        )
    except Exception as exc:
        # Session might already exist; ignore.
        logger.warning("Agent session creation failed user_id={} session_id={} err={}", user_key, session_id, exc)

    content = _build_content(message)
    final_text = ""
    debug_events: list[str] = []

    async for event in runner.run_async(
        user_id=user_key,
        session_id=session_id,
        new_message=content,
    ):
        if debug:
            debug_events.append(str(event))
        try:
            is_final = event.is_final_response()
        except Exception as exc:
            logger.warning("Agent event final check failed user_id={} err={}", user_key, exc)
            is_final = False
        if is_final:
            parts = getattr(event, "content", None)
            if parts and getattr(parts, "parts", None):
                part = parts.parts[0]
                final_text = getattr(part, "text", "") or final_text
    ordered_entries: list[tuple[ProcessedContext, dict]] = []
    sources = []
    if trace.hits:
        try:
            from ..routes.chat import _build_sources  # type: ignore
        except Exception as exc:
            logger.warning("Agent source builder import failed user_id={} err={}", user_key, exc)
            _build_sources = None
        # Preserve hit order while fetching contexts
        context_ids: list[UUID] = []
        seen: set[str] = set()
        for hit in trace.hits:
            context_id = str(hit.get("context_id") or "").strip()
            if context_id and context_id not in seen:
                try:
                    context_uuid = UUID(context_id)
                except Exception as exc:
                    logger.warning("Agent context id invalid user_id={} context_id={} err={}", user_key, context_id, exc)
                    continue
                seen.add(context_id)
                context_ids.append(context_uuid)
        if context_ids:
            stmt = select(ProcessedContext).where(
                ProcessedContext.id.in_(context_ids),
                ProcessedContext.user_id == user_id,
            )
            rows = await session.execute(stmt)
            contexts_by_id = {str(context.id): context for context in rows.scalars().all()}
            for hit in trace.hits:
                context_id = str(hit.get("context_id") or "").strip()
                context = contexts_by_id.get(context_id)
                if context:
                    ordered_entries.append((context, hit))
        if ordered_entries and _build_sources:
            sources = await _build_sources(session, ordered_entries, limit=6)

    if ordered_entries and not _has_memories_section(final_text):
        memories_section = _format_memories_section(
            ordered_entries,
            tz_offset_minutes=tz_offset_minutes,
            max_items=8,
        )
        if memories_section:
            final_text = f"{final_text.rstrip()}\n\n{memories_section}"

    debug_payload = {"events": debug_events} if debug else None
    return (
        final_text.strip() or "I do not have enough information to answer that yet.",
        session_id,
        debug_payload,
        sources,
    )


def _has_memories_section(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return "memories" in lowered and "memories:" in lowered


def _format_memories_section(
    entries: list[tuple[ProcessedContext, dict]],
    tz_offset_minutes: Optional[int],
    max_items: int = 8,
) -> str:
    lines = ["Memories:"]
    offset = timedelta(minutes=tz_offset_minutes or 0)
    for idx, (context, _hit) in enumerate(entries[:max_items], start=1):
        timestamp = "Unknown time"
        if context.event_time_utc:
            local_dt = ensure_tz_aware(context.event_time_utc) - offset
            timestamp = local_dt.strftime("%b %d, %Y %I:%M %p")
        summary = (context.summary or context.title or "Memory").strip()
        if len(summary) > 160:
            summary = summary[:160].rstrip() + "..."
        lines.append(f"- {timestamp}: {summary} [{idx}]")
    return "\n".join(lines)
