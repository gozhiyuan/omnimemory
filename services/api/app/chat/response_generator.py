"""Prompt assembly for chat responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..ai.prompts import build_lifelog_chat_system_prompt


@dataclass
class ChatPromptInputs:
    intent: str
    message: str
    summary_block: str
    history_block: str
    image_context: Optional[str]
    context_block: str
    tz_offset_minutes: Optional[int]
    include_summary: bool
    resolved_time_range: Optional[str] = None


def build_chat_prompt(inputs: ChatPromptInputs) -> str:
    system_prompt = build_lifelog_chat_system_prompt()
    sections = [system_prompt]

    if inputs.intent == "meta_question":
        offset_delta = timedelta(minutes=inputs.tz_offset_minutes or 0)
        local_now = datetime.now(timezone.utc) - offset_delta
        date_info = (
            "Current information (IMPORTANT - use this exact date, do not infer from memories):\n"
            f"- Today's date: {local_now.strftime('%A, %B %d, %Y')}\n"
            f"- Current time: {local_now.strftime('%I:%M %p')}\n"
            f"- Timezone offset: {inputs.tz_offset_minutes or 0} minutes from UTC\n"
            "- You MUST use the date above when answering questions about today's date."
        )
        sections.append(date_info)
    elif inputs.intent == "greeting":
        sections.append("The user is greeting you. Respond warmly and offer to help with their memories.")
    elif inputs.intent == "clarification":
        sections.append("The user is asking for clarification about a previous response.")

    if inputs.resolved_time_range and inputs.intent == "memory_query":
        sections.append(
            "Resolved time range (local time):\n"
            f"- {inputs.resolved_time_range}\n"
            "- Use this range exactly; do not infer dates from memory recency."
        )

    if inputs.intent == "memory_query" and inputs.context_block:
        sections.append(
            "Memory response rules:\n"
            "- Relevant memories are provided below; do not say you lack information.\n"
            "- Answer using those memories and cite them with [#] references."
        )

    if inputs.include_summary and inputs.summary_block:
        sections.append(f"Recent daily summaries:\n{inputs.summary_block}")
    if inputs.history_block:
        sections.append(f"Conversation history:\n{inputs.history_block}")
    if inputs.image_context:
        sections.append(f"User uploaded image:\n{inputs.image_context}")
    if inputs.context_block and inputs.intent == "memory_query":
        sections.append(f"Relevant memories:\n{inputs.context_block}")
    sections.append(f"User question: {inputs.message}")

    return "\n\n".join(section for section in sections if section.strip())
