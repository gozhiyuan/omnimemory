"""Prompt templates for VLM analysis and transcription.

Uses PromptManager with fallback chain: user override → bundled → inline.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.ai.prompt_manager import get_prompt_manager

logger = logging.getLogger(__name__)


DEFAULT_LANGUAGE = "English"


def _resolve_language_label(language: str | None) -> str:
    if language and language.strip():
        return language.strip()
    return DEFAULT_LANGUAGE


def _image_language_guidance(language: str | None, extra_guidance: str | None = None) -> str:
    label = _resolve_language_label(language)
    guidance = (
        "\n\nLanguage guidance:\n"
        f"- Use {label} for all title/summary/keywords fields.\n"
        "- Keep JSON keys and enum values in English.\n"
    )
    if extra_guidance:
        guidance += extra_guidance
    return guidance


def _media_chunk_language_guidance(language: str | None, extra_guidance: str | None = None) -> str:
    label = _resolve_language_label(language)
    guidance = (
        "\n\nLanguage guidance:\n"
        f"- Transcript stays in the original spoken language; do not translate.\n"
        f"- Use {label} for titles/summaries/keywords in contexts.\n"
        "- Keep JSON keys and enum values in English.\n"
    )
    if extra_guidance:
        guidance += extra_guidance
    return guidance


def _transcription_language_guidance(language: str | None, extra_guidance: str | None = None) -> str:
    label = _resolve_language_label(language)
    guidance = (
        "\nLanguage guidance:\n"
        f"- User language: {label}.\n"
        "- Transcribe verbatim in the original spoken language; do not translate.\n"
    )
    if extra_guidance:
        guidance += extra_guidance
    return guidance


def _summary_language_guidance(language: str | None, extra_guidance: str | None = None) -> str:
    label = _resolve_language_label(language)
    guidance = (
        "\n\nLanguage guidance:\n"
        f"- Use {label} for title/summary/keywords.\n"
    )
    if extra_guidance:
        guidance += extra_guidance
    return guidance


def build_lifelog_image_prompt(
    ocr_text: str | None,
    language: str | None = None,
    user_id: Optional[str] = None,
    extra_guidance: str | None = None,
) -> str:
    """Build image analysis prompt with optional per-user customization."""
    cleaned = (ocr_text or "").strip()
    if len(cleaned) > 2000:
        cleaned = cleaned[:2000] + "..."

    manager = get_prompt_manager()
    try:
        return manager.render(
            "image_analysis",
            user_id=user_id,
            ocr_text=cleaned or "None",
            language_guidance=_image_language_guidance(language, extra_guidance),
        )
    except Exception as e:
        logger.warning(f"Failed to render image_analysis prompt: {e}")
        # Fallback to inline rendering (should not happen with proper setup)
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["image_analysis"])
        return template.render(
            ocr_text=cleaned or "None",
            language_guidance=_image_language_guidance(language, extra_guidance),
        )


def build_lifelog_transcription_prompt(
    media_kind: str,
    language: str | None = None,
    user_id: Optional[str] = None,
    extra_guidance: str | None = None,
) -> str:
    """Build transcription prompt with optional per-user customization."""
    kind = (media_kind or "audio").strip().lower()
    if kind not in {"audio", "video"}:
        kind = "audio"

    manager = get_prompt_manager()
    try:
        return manager.render(
            "transcription",
            user_id=user_id,
            media_kind=kind,
            language_guidance=_transcription_language_guidance(language, extra_guidance),
        )
    except Exception as e:
        logger.warning(f"Failed to render transcription prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["transcription"])
        return template.render(
            media_kind=kind,
            language_guidance=_transcription_language_guidance(language, extra_guidance),
        )


def build_lifelog_video_chunk_prompt(
    language: str | None = None,
    user_id: Optional[str] = None,
    extra_guidance: str | None = None,
) -> str:
    """Build video chunk analysis prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "video_chunk",
            user_id=user_id,
            language_guidance=_media_chunk_language_guidance(language, extra_guidance),
        )
    except Exception as e:
        logger.warning(f"Failed to render video_chunk prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["video_chunk"])
        return template.render(
            language_guidance=_media_chunk_language_guidance(language, extra_guidance),
        )


def build_lifelog_audio_chunk_prompt(
    language: str | None = None,
    user_id: Optional[str] = None,
    extra_guidance: str | None = None,
) -> str:
    """Build audio chunk analysis prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "audio_chunk",
            user_id=user_id,
            language_guidance=_media_chunk_language_guidance(language, extra_guidance),
        )
    except Exception as e:
        logger.warning(f"Failed to render audio_chunk prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["audio_chunk"])
        return template.render(
            language_guidance=_media_chunk_language_guidance(language, extra_guidance),
        )


def build_lifelog_episode_summary_prompt(
    items_json: str,
    *,
    item_count: int,
    time_range: str,
    omitted_count: int = 0,
    language: str | None = None,
    user_id: Optional[str] = None,
    extra_guidance: str | None = None,
) -> str:
    """Build episode summary prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "episode_summary",
            user_id=user_id,
            items_json=items_json.strip() or "[]",
            item_count=str(item_count),
            time_range=time_range,
            omitted_count=str(omitted_count),
            language_guidance=_summary_language_guidance(language, extra_guidance),
        )
    except Exception as e:
        logger.warning(f"Failed to render episode_summary prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["episode_summary"])
        return template.render(
            items_json=items_json.strip() or "[]",
            item_count=str(item_count),
            time_range=time_range,
            omitted_count=str(omitted_count),
            language_guidance=_summary_language_guidance(language, extra_guidance),
        )


def build_lifelog_chat_system_prompt(user_id: Optional[str] = None) -> str:
    """Build chat system prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render("chat_system", user_id=user_id)
    except Exception as e:
        logger.warning(f"Failed to render chat_system prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        return INLINE_DEFAULTS["chat_system"]


def build_lifelog_cartoon_agent_prompt(
    instruction: str,
    memory_context: str,
    date_label: str,
    user_id: Optional[str] = None,
) -> str:
    """Build cartoon agent prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "cartoon_agent",
            user_id=user_id,
            instruction=instruction.strip(),
            memory_context=memory_context.strip() or "None",
            date_label=date_label,
        )
    except Exception as e:
        logger.warning(f"Failed to render cartoon_agent prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["cartoon_agent"])
        return template.render(
            instruction=instruction.strip(),
            memory_context=memory_context.strip() or "None",
            date_label=date_label,
        )


def build_lifelog_day_insights_agent_prompt(
    instruction: str,
    memory_context: str,
    date_range_label: str,
    stats_json: str,
    user_id: Optional[str] = None,
) -> str:
    """Build day insights agent prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "day_insights_agent",
            user_id=user_id,
            instruction=instruction.strip(),
            memory_context=memory_context.strip() or "None",
            date_range_label=date_range_label,
            stats_json=stats_json.strip() or "{}",
        )
    except Exception as e:
        logger.warning(f"Failed to render day_insights_agent prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["day_insights_agent"])
        return template.render(
            instruction=instruction.strip(),
            memory_context=memory_context.strip() or "None",
            date_range_label=date_range_label,
            stats_json=stats_json.strip() or "{}",
        )


def build_lifelog_query_entities_prompt(
    query: str,
    user_id: Optional[str] = None,
) -> str:
    """Build query entities extraction prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "query_entities",
            user_id=user_id,
            query=query.strip(),
        )
    except Exception as e:
        logger.warning(f"Failed to render query_entities prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["query_entities"])
        return template.render(query=query.strip())


def build_lifelog_session_title_prompt(
    first_message: str,
    user_id: Optional[str] = None,
) -> str:
    """Build session title prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "session_title",
            user_id=user_id,
            first_message=first_message.strip(),
        )
    except Exception as e:
        logger.warning(f"Failed to render session_title prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["session_title"])
        return template.render(first_message=first_message.strip())


# Agent prompts for Memory Agent mode
def build_agent_system_prompt(
    session_state: str,
    user_id: Optional[str] = None,
) -> str:
    """Build Memory Agent system prompt with optional per-user customization."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "agent_system",
            user_id=user_id,
            session_state=session_state,
        )
    except Exception as e:
        logger.warning(f"Failed to render agent_system prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["agent_system"])
        return template.render(session_state=session_state)


def build_agent_action_prompt(
    conversation_history: str,
    message: str,
    user_id: Optional[str] = None,
) -> str:
    """Build Memory Agent action determination prompt."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "agent_action",
            user_id=user_id,
            conversation_history=conversation_history,
            message=message,
        )
    except Exception as e:
        logger.warning(f"Failed to render agent_action prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["agent_action"])
        return template.render(
            conversation_history=conversation_history,
            message=message,
        )


def build_agent_response_prompt(
    conversation_history: str,
    action_result: str,
    session_state: str,
    user_id: Optional[str] = None,
) -> str:
    """Build Memory Agent response generation prompt."""
    manager = get_prompt_manager()
    try:
        return manager.render(
            "agent_response",
            user_id=user_id,
            conversation_history=conversation_history,
            action_result=action_result,
            session_state=session_state,
        )
    except Exception as e:
        logger.warning(f"Failed to render agent_response prompt: {e}")
        from app.ai.prompt_templates import INLINE_DEFAULTS
        from jinja2 import Template
        template = Template(INLINE_DEFAULTS["agent_response"])
        return template.render(
            conversation_history=conversation_history,
            action_result=action_result,
            session_state=session_state,
        )


def build_vector_text(
    title: str,
    summary: str,
    keywords: list[str],
) -> str:
    """Build vector text for embedding from context fields."""
    parts = []
    if title:
        parts.append(title)
    if summary:
        parts.append(summary)
    if keywords:
        parts.append(" ".join(keywords))
    return " ".join(parts)
