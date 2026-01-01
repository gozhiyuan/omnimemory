"""Image generation helpers for agent workflows."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Optional
from uuid import UUID

from google import genai
from loguru import logger

from .usage import log_usage_from_response
from ..config import Settings

try:  # Optional, only used to normalize output to PNG.
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None


@dataclass
class GeneratedImage:
    data: bytes
    content_type: str


def _extract_parts(response: Any) -> list[Any]:
    parts = getattr(response, "parts", None)
    if parts:
        return list(parts)
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        if content and getattr(content, "parts", None):
            return list(content.parts)
    return []


def _normalize_inline_data(part: Any) -> Optional[GeneratedImage]:
    inline = getattr(part, "inline_data", None)
    if not inline:
        return None
    data = getattr(inline, "data", None)
    if not data:
        return None
    if isinstance(data, str):
        try:
            data_bytes = base64.b64decode(data)
        except Exception:
            return None
    else:
        data_bytes = data if isinstance(data, (bytes, bytearray)) else None
    if not data_bytes:
        return None
    mime_type = getattr(inline, "mime_type", None) or "image/png"
    return GeneratedImage(data=bytes(data_bytes), content_type=mime_type)


def _normalize_part_image(part: Any) -> Optional[GeneratedImage]:
    if Image is None:
        return _normalize_inline_data(part)
    try:
        image = part.as_image()
    except Exception:
        return _normalize_inline_data(part)
    if not image:
        return _normalize_inline_data(part)
    buffer = BytesIO()
    try:
        image.save(buffer, format="PNG")
    except Exception:
        return _normalize_inline_data(part)
    return GeneratedImage(data=buffer.getvalue(), content_type="image/png")


async def generate_image_with_gemini(
    prompt: str,
    settings: Settings,
    model: str,
    timeout_seconds: int,
    *,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "agent_image",
) -> dict[str, Any]:
    api_key = settings.gemini_api_key
    if not api_key:
        return {"status": "disabled", "reason": "missing_api_key", "images": []}

    client = genai.Client(api_key=api_key)

    def _generate() -> Any:
        return client.models.generate_content(model=model, contents=prompt)

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_generate),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:  # pragma: no cover - network/SDK failures
        return {"status": "error", "error": "timeout", "images": []}
    except Exception as exc:  # pragma: no cover - network/SDK failures
        logger.warning("Gemini image generation failed: {}", exc)
        return {"status": "error", "error": str(exc), "images": []}

    log_usage_from_response(
        response,
        user_id=user_id,
        item_id=item_id,
        provider="gemini",
        model=model,
        step_name=step_name,
    )

    images: list[GeneratedImage] = []
    for part in _extract_parts(response):
        image = _normalize_part_image(part)
        if image:
            images.append(image)

    return {"status": "ok" if images else "error", "images": images}
