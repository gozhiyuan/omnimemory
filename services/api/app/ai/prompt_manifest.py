"""Prompt manifest defining allowed prompts and their validation rules.

This module provides the source of truth for:
- Allowed prompt names (whitelist)
- Required and optional variables per prompt
- Size limits and validation rules
"""

from __future__ import annotations

from typing import TypedDict


class PromptSpec(TypedDict, total=False):
    """Specification for a prompt template."""

    required_vars: list[str]
    optional_vars: list[str]
    max_size_bytes: int
    output_format: str  # "json" or "text"
    description: str
    updatable_via_api: bool  # Whether this prompt can be updated via OpenClaw API


# Default max size for prompts (32KB)
DEFAULT_MAX_SIZE_BYTES = 32768


PROMPT_MANIFEST: dict[str, PromptSpec] = {
    "image_analysis": {
        "required_vars": ["ocr_text", "language_guidance"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Analyze lifelog photos for context extraction",
        "updatable_via_api": True,
    },
    "video_chunk": {
        "required_vars": ["language_guidance"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Analyze video segment for transcript and contexts",
        "updatable_via_api": True,
    },
    "audio_chunk": {
        "required_vars": ["language_guidance"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Analyze audio segment for transcript and contexts",
        "updatable_via_api": True,
    },
    "transcription": {
        "required_vars": ["media_kind", "language_guidance"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "text",
        "description": "Verbatim transcription of audio/video",
        "updatable_via_api": False,
    },
    "episode_summary": {
        "required_vars": ["items_json", "item_count", "time_range", "language_guidance"],
        "optional_vars": ["omitted_count"],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Generate episode-level summary from multiple items",
        "updatable_via_api": True,
    },
    "chat_system": {
        "required_vars": [],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "text",
        "description": "Chat assistant system prompt",
        "updatable_via_api": True,
    },
    "query_entities": {
        "required_vars": ["query"],
        "optional_vars": [],
        "max_size_bytes": 8192,
        "output_format": "json",
        "description": "Extract entity names from user queries",
        "updatable_via_api": False,
    },
    "date_range": {
        "required_vars": ["query", "now_iso", "tz_offset_minutes"],
        "optional_vars": [],
        "max_size_bytes": 8192,
        "output_format": "json",
        "description": "Infer local date range from user queries",
        "updatable_via_api": False,
    },
    "query_intent": {
        "required_vars": ["query"],
        "optional_vars": [],
        "max_size_bytes": 8192,
        "output_format": "json",
        "description": "Classify user query intent for routing",
        "updatable_via_api": False,
    },
    "rerank": {
        "required_vars": ["query", "candidates"],
        "optional_vars": [],
        "max_size_bytes": 16384,
        "output_format": "json",
        "description": "Rerank memory candidates by relevance to query",
        "updatable_via_api": False,
    },
    "session_title": {
        "required_vars": ["first_message"],
        "optional_vars": [],
        "max_size_bytes": 8192,
        "output_format": "text",
        "description": "Generate chat session title",
        "updatable_via_api": False,
    },
    "cartoon_agent": {
        "required_vars": ["instruction", "memory_context", "date_label"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Generate cartoon illustration prompt and caption",
        "updatable_via_api": False,
    },
    "day_insights_agent": {
        "required_vars": ["instruction", "memory_context", "date_range_label", "stats_json"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Generate daily insights summary",
        "updatable_via_api": False,
    },
    "surprise_agent": {
        "required_vars": ["instruction", "memory_context", "date_range_label"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Generate surprise highlight summary",
        "updatable_via_api": False,
    },
    # Agent prompts (for Memory Agent mode)
    "agent_system": {
        "required_vars": ["session_state"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "text",
        "description": "Memory Agent system prompt for multi-turn conversations",
        "updatable_via_api": True,
    },
    "agent_action": {
        "required_vars": ["conversation_history", "message"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "json",
        "description": "Determine agent action from user message",
        "updatable_via_api": True,
    },
    "agent_response": {
        "required_vars": ["conversation_history", "action_result", "session_state"],
        "optional_vars": [],
        "max_size_bytes": DEFAULT_MAX_SIZE_BYTES,
        "output_format": "text",
        "description": "Generate agent response after action execution",
        "updatable_via_api": True,
    },
}


def get_prompt_names() -> list[str]:
    """Return all valid prompt names."""
    return list(PROMPT_MANIFEST.keys())


def get_api_updatable_prompts() -> list[str]:
    """Return prompt names that can be updated via OpenClaw API."""
    return [
        name
        for name, spec in PROMPT_MANIFEST.items()
        if spec.get("updatable_via_api", False)
    ]


def is_valid_prompt_name(name: str) -> bool:
    """Check if a prompt name is in the allowlist."""
    return name in PROMPT_MANIFEST


def get_prompt_spec(name: str) -> PromptSpec | None:
    """Get the specification for a prompt."""
    return PROMPT_MANIFEST.get(name)


def get_required_vars(name: str) -> list[str]:
    """Get required variables for a prompt."""
    spec = PROMPT_MANIFEST.get(name)
    if spec:
        return spec.get("required_vars", [])
    return []


def get_max_size(name: str, global_max: int = DEFAULT_MAX_SIZE_BYTES) -> int:
    """Get max size for a prompt (min of global cap and per-prompt override)."""
    spec = PROMPT_MANIFEST.get(name)
    if spec:
        per_prompt_max = spec.get("max_size_bytes", global_max)
        return min(global_max, per_prompt_max)
    return global_max


def validate_prompt_vars(name: str, provided_vars: set[str]) -> tuple[bool, list[str]]:
    """Validate that all required variables are provided.

    Args:
        name: The prompt name
        provided_vars: Set of variable names that will be provided

    Returns:
        Tuple of (is_valid, list of missing variable names)
    """
    spec = PROMPT_MANIFEST.get(name)
    if not spec:
        return False, [f"Unknown prompt: {name}"]

    required = set(spec.get("required_vars", []))
    missing = required - provided_vars
    return len(missing) == 0, list(missing)
