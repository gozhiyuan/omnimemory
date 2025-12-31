"""Gemini helpers for video/audio understanding and text summaries."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, Optional
from uuid import UUID

from google import genai
from google.genai import types
from loguru import logger

from ..config import Settings
from .usage import log_usage_from_response


def _extract_json(text: str) -> Optional[dict]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _build_text_contents(prompt: str) -> list[types.Content]:
    return [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]


def _build_media_contents(prompt: str, media_bytes: bytes, mime_type: str) -> list[types.Content]:
    return [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
            ],
        )
    ]


async def _generate_content(
    *,
    prompt: str,
    settings: Settings,
    model: str,
    temperature: float,
    max_output_tokens: int,
    timeout_seconds: int,
    media_bytes: bytes | None = None,
    content_type: str | None = None,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "media_understanding",
) -> Dict[str, Any]:
    api_key = settings.gemini_api_key
    if not api_key:
        return {"status": "disabled", "reason": "missing_api_key", "raw_text": ""}

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )

    if media_bytes is None:
        contents = _build_text_contents(prompt)
    else:
        mime_type = content_type or "application/octet-stream"
        contents = _build_media_contents(prompt, media_bytes, mime_type)

    def _generate() -> Any:
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_generate),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:  # pragma: no cover - network/SDK failures
        return {"status": "error", "error": "timeout", "raw_text": ""}
    except Exception as exc:  # pragma: no cover - network/SDK failures
        logger.warning("Gemini request failed: {}", exc)
        return {"status": "error", "error": str(exc), "raw_text": ""}

    raw_text = getattr(response, "text", "") or ""
    log_usage_from_response(
        response,
        user_id=user_id,
        item_id=item_id,
        provider="gemini",
        model=model,
        step_name=step_name,
    )
    return {"status": "ok", "raw_text": raw_text}


async def analyze_video_with_gemini(
    video_bytes: bytes,
    prompt: str,
    settings: Settings,
    content_type: str | None = None,
    *,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "video_understanding",
) -> Dict[str, Any]:
    provider = settings.video_understanding_provider
    if provider == "none":
        return {"status": "disabled", "reason": "provider_disabled", "raw_text": ""}
    if provider != "gemini":
        return {"status": "disabled", "reason": f"unsupported_provider:{provider}", "raw_text": ""}

    response = await _generate_content(
        prompt=prompt,
        settings=settings,
        model=settings.video_understanding_model,
        temperature=settings.video_understanding_temperature,
        max_output_tokens=settings.transcription_max_output_tokens,
        timeout_seconds=settings.video_understanding_timeout_seconds,
        media_bytes=video_bytes,
        content_type=content_type or "video/mp4",
        user_id=user_id,
        item_id=item_id,
        step_name=step_name,
    )
    raw_text = response.get("raw_text", "")
    parsed = _extract_json(raw_text) if raw_text else None
    return {
        "status": response.get("status", "error"),
        "error": response.get("error"),
        "raw_text": raw_text,
        "parsed": parsed,
    }


async def analyze_audio_with_gemini(
    audio_bytes: bytes,
    prompt: str,
    settings: Settings,
    content_type: str | None = None,
    *,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "audio_understanding",
) -> Dict[str, Any]:
    provider = settings.audio_understanding_provider
    if provider == "none":
        return {"status": "disabled", "reason": "provider_disabled", "raw_text": ""}
    if provider != "gemini":
        return {"status": "disabled", "reason": f"unsupported_provider:{provider}", "raw_text": ""}

    response = await _generate_content(
        prompt=prompt,
        settings=settings,
        model=settings.audio_understanding_model,
        temperature=settings.audio_understanding_temperature,
        max_output_tokens=settings.transcription_max_output_tokens,
        timeout_seconds=settings.audio_understanding_timeout_seconds,
        media_bytes=audio_bytes,
        content_type=content_type or "audio/mpeg",
        user_id=user_id,
        item_id=item_id,
        step_name=step_name,
    )
    raw_text = response.get("raw_text", "")
    parsed = _extract_json(raw_text) if raw_text else None
    return {
        "status": response.get("status", "error"),
        "error": response.get("error"),
        "raw_text": raw_text,
        "parsed": parsed,
    }


async def summarize_text_with_gemini(
    prompt: str,
    settings: Settings,
    model: str,
    temperature: float,
    max_output_tokens: int,
    timeout_seconds: int,
    *,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "text_summary",
) -> Dict[str, Any]:
    response = await _generate_content(
        prompt=prompt,
        settings=settings,
        model=model,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        media_bytes=None,
        user_id=user_id,
        item_id=item_id,
        step_name=step_name,
    )
    raw_text = response.get("raw_text", "")
    parsed = _extract_json(raw_text) if raw_text else None
    return {
        "status": response.get("status", "error"),
        "error": response.get("error"),
        "raw_text": raw_text,
        "parsed": parsed,
    }
