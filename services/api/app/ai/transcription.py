"""Transcription providers for audio/video."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from google import genai
from google.genai import types
from loguru import logger

from ..config import Settings
from .prompts import build_lifelog_transcription_prompt


def _build_contents(prompt: str, media_bytes: bytes, mime_type: str) -> list[types.Content]:
    return [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
            ],
        )
    ]


async def _call_gemini(
    media_bytes: bytes,
    prompt: str,
    settings: Settings,
    content_type: str | None,
    model: str,
    temperature: float,
    timeout_seconds: int,
) -> Dict[str, Any]:
    api_key = settings.gemini_api_key
    if not api_key:
        return {"status": "disabled", "reason": "missing_api_key", "text": ""}

    mime_type = content_type or "audio/mpeg"
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=settings.transcription_max_output_tokens,
    )

    def _generate() -> Any:
        return client.models.generate_content(
            model=model,
            contents=_build_contents(prompt, media_bytes, mime_type),
            config=config,
        )

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_generate),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:  # pragma: no cover - network/SDK failures
        return {"status": "error", "error": "timeout", "text": ""}
    except Exception as exc:  # pragma: no cover - network/SDK failures
        logger.warning("Transcription request failed: {}", exc)
        return {"status": "error", "error": str(exc), "text": ""}

    raw_text = getattr(response, "text", "") or ""
    return {"status": "ok", "text": raw_text.strip()}


async def transcribe_media(
    media_bytes: bytes,
    settings: Settings,
    content_type: str | None = None,
    media_kind: str = "audio",
) -> Dict[str, Any]:
    if media_kind == "video":
        provider = settings.video_understanding_provider
        model = settings.video_understanding_model
        temperature = settings.video_understanding_temperature
        timeout_seconds = settings.video_understanding_timeout_seconds
    else:
        provider = settings.audio_understanding_provider
        model = settings.audio_understanding_model
        temperature = settings.audio_understanding_temperature
        timeout_seconds = settings.audio_understanding_timeout_seconds
    if provider == "none":
        return {"status": "disabled", "reason": "provider_disabled", "text": ""}
    if provider != "gemini":
        return {"status": "disabled", "reason": f"unsupported_provider:{provider}", "text": ""}

    prompt = build_lifelog_transcription_prompt(media_kind)
    return await _call_gemini(
        media_bytes,
        prompt,
        settings,
        content_type,
        model,
        temperature,
        timeout_seconds,
    )
