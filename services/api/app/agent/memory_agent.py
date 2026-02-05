"""Memory agent definition for ADK runner."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from ..config import Settings, get_settings
from .tools import AgentMemoryTrace, build_memory_tools


def _agent_instruction() -> str:
    return (
        "You are OmniMemory's memory agent. Use tools to search and retrieve memories. "
        "Always cite evidence from tool outputs. If you cannot find evidence, ask a clarifying question. "
        "When you answer, end with a 'Memories' section that lists up to 8 concrete memories with "
        "local timestamps and short descriptions, each with an evidence citation."
    )


def build_agent(
    *,
    user_id: UUID,
    session,
    tz_offset_minutes: Optional[int],
    settings: Optional[Settings] = None,
    trace: Optional[AgentMemoryTrace] = None,
):
    settings = settings or get_settings()
    tools = build_memory_tools(
        user_id=user_id,
        session=session,
        tz_offset_minutes=tz_offset_minutes,
        settings=settings,
        trace=trace,
    )
    try:
        from google.adk.agents import LlmAgent
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("google-adk is not installed") from exc

    return LlmAgent(
        name="omnimemory_agent",
        model=settings.agent_prompt_model,
        instruction=_agent_instruction(),
        tools=tools,
    )
