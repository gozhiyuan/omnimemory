"""Utility helpers for pipeline steps."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import re
from typing import Iterable, Optional


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_parts(parts: Iterable[object]) -> str:
    joined = "||".join(str(part) for part in parts if part is not None)
    return hash_text(joined)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_exif_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def ensure_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def compute_image_ahash(blob: bytes) -> Optional[str]:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        image = Image.open(io.BytesIO(blob))
        image = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    except Exception:
        return None
    pixels = list(image.getdata())
    if not pixels:
        return None
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel > avg else "0" for pixel in pixels)
    return f"{int(bits, 2):016x}"


def hamming_distance_hex(a: str, b: str) -> Optional[int]:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None


def extract_keywords(text: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    keywords = [word for word in words if len(word) > 2]
    seen = []
    for word in keywords:
        if word not in seen:
            seen.append(word)
        if len(seen) >= limit:
            break
    return seen


_KEYWORD_STOPWORDS = {
    "outdoors",
    "outdoor",
    "indoor",
    "people",
    "person",
    "photo",
    "photos",
    "video",
    "videos",
    "image",
    "images",
    "scene",
    "view",
    "background",
    "group",
    "selfie",
    "portrait",
    "thing",
    "stuff",
}

_KEYWORD_SKIP_CONTEXTS = {"entity_context"}


def _filter_keywords(keywords: list[str], context_type: str | None) -> list[str]:
    if context_type in _KEYWORD_SKIP_CONTEXTS:
        return []
    filtered: list[str] = []
    seen: set[str] = set()
    for word in keywords or []:
        cleaned = str(word or "").strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in _KEYWORD_STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        filtered.append(lowered)
        if len(filtered) >= 8:
            break
    return filtered


def build_vector_text(
    title: str,
    summary: str,
    keywords: list[str],
    *,
    context_type: str | None = None,
) -> str:
    parts = [title.strip(), summary.strip()]
    filtered = _filter_keywords(keywords, context_type)
    if filtered:
        parts.append("Keywords: " + ", ".join(filtered))
    return "\n".join(part for part in parts if part)
