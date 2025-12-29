"""OCR providers for image text extraction."""

from __future__ import annotations

import base64
from typing import Any, Dict

import httpx
from loguru import logger

from ..config import Settings


async def _run_google_cloud_vision(
    image_bytes: bytes, settings: Settings, content_type: str | None
) -> Dict[str, Any]:
    api_key = settings.ocr_google_api_key
    if not api_key:
        return {"text": "", "status": "disabled", "reason": "missing_api_key"}

    encoded = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "requests": [
            {
                "image": {"content": encoded},
                "features": [{"type": "TEXT_DETECTION", "maxResults": 1}],
                "imageContext": {"languageHints": settings.ocr_language_hints},
            }
        ]
    }
    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    timeout = settings.ocr_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
    if response.status_code >= 400:
        logger.warning("OCR request failed status={} body={}", response.status_code, response.text)
        return {
            "text": "",
            "status": "error",
            "error": f"vision_api_status_{response.status_code}",
        }
    data = response.json()
    response_entry = (data.get("responses") or [{}])[0]
    annotations = response_entry.get("textAnnotations") or []
    text = ""
    if annotations:
        text = annotations[0].get("description") or ""
    return {
        "text": text.strip(),
        "status": "ok",
        "provider": "google_cloud_vision",
        "content_type": content_type,
    }


async def run_ocr(
    image_bytes: bytes,
    settings: Settings,
    content_type: str | None = None,
) -> Dict[str, Any]:
    provider = settings.ocr_provider
    if provider == "none":
        return {"text": "", "status": "disabled", "reason": "provider_disabled"}
    if provider == "google_cloud_vision":
        return await _run_google_cloud_vision(image_bytes, settings, content_type)
    return {"text": "", "status": "disabled", "reason": f"unsupported_provider:{provider}"}
