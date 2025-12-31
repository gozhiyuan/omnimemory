"""VLM providers for image context extraction."""

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
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _build_contents(prompt: str, image_bytes: bytes, mime_type: str) -> list[types.Content]:
    return [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
        )
    ]


async def _call_gemini(
    image_bytes: bytes,
    prompt: str,
    settings: Settings,
    content_type: str | None,
    *,
    user_id: UUID | str | None,
    item_id: UUID | str | None,
    step_name: str,
) -> Dict[str, Any]:
    api_key = settings.gemini_api_key
    if not api_key:
        return {"status": "disabled", "reason": "missing_api_key", "raw_text": ""}

    mime_type = content_type or "image/jpeg"
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        temperature=settings.vlm_temperature,
        max_output_tokens=settings.vlm_max_output_tokens,
    )

    def _generate() -> Any:
        return client.models.generate_content(
            model=settings.gemini_model,
            contents=_build_contents(prompt, image_bytes, mime_type),
            config=config,
        )

    try:
        response = await asyncio.to_thread(_generate)
    except Exception as exc:  # pragma: no cover - network/SDK failures
        logger.warning("VLM request failed: {}", exc)
        return {"status": "error", "error": str(exc), "raw_text": ""}

    raw_text = getattr(response, "text", "") or ""
    log_usage_from_response(
        response,
        user_id=user_id,
        item_id=item_id,
        provider="gemini",
        model=settings.gemini_model,
        step_name=step_name,
    )
    return {"status": "ok", "raw_text": raw_text}


async def analyze_image_with_vlm(
    image_bytes: bytes,
    prompt: str,
    settings: Settings,
    content_type: str | None = None,
    *,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "vlm",
) -> Dict[str, Any]:
    provider = settings.vlm_provider
    if provider == "none":
        return {"status": "disabled", "reason": "provider_disabled", "raw_text": ""}
    if provider != "gemini":
        return {"status": "disabled", "reason": f"unsupported_provider:{provider}", "raw_text": ""}

    response = await _call_gemini(
        image_bytes,
        prompt,
        settings,
        content_type,
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
