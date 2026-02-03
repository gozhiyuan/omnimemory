"""Pipeline steps for ingestion."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Optional
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import delete, select

from ..ai import (
    analyze_audio_with_gemini,
    analyze_image_with_vlm,
    analyze_video_with_gemini,
    reverse_geocode,
    run_ocr,
)
from ..ai.prompts import (
    build_lifelog_audio_chunk_prompt,
    build_lifelog_image_prompt,
    build_lifelog_video_chunk_prompt,
)
from ..db.models import DataConnection, ProcessedContent, ProcessedContext, SourceItem
from ..google_photos import get_valid_access_token
from ..user_settings import (
    build_preference_guidance,
    resolve_language_code,
    resolve_language_label,
    resolve_ocr_language_hints,
)
from ..vectorstore import upsert_context_embeddings
from .media_utils import (
    MediaToolError,
    create_video_preview,
    extract_single_frame,
    extract_keyframes,
    ffmpeg_available,
    parse_fraction,
    parse_iso6709,
    probe_media,
    segment_audio_with_vad,
    segment_video,
)
from .types import PipelineArtifacts, PipelineConfig, PipelineStep
from .utils import (
    build_vector_text,
    compute_image_ahash,
    ensure_tz_aware,
    extract_keywords,
    hash_bytes,
    hash_parts,
    parse_iso_datetime,
    hamming_distance_hex,
    parse_exif_datetime,
)


def _resolve_language(config: PipelineConfig) -> tuple[str, str]:
    code = resolve_language_code(config.user_settings)
    return code, resolve_language_label(code)


def _truncate_text(value: str, limit: int) -> str:
    cleaned = value.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _normalize_context_entry(
    entry: dict[str, Any],
    *,
    default_title: str,
    provider_versions: dict[str, Any],
) -> dict[str, Any]:
    context_type = entry.get("context_type") or "activity_context"
    title = entry.get("title") or default_title
    summary = entry.get("summary") or ""
    keywords = entry.get("keywords") or extract_keywords(summary)
    entities = entry.get("entities") or []
    location = entry.get("location") or {}
    return {
        "context_type": context_type,
        "title": title,
        "summary": summary,
        "keywords": keywords,
        "entities": entities,
        "location": location,
        "vector_text": build_vector_text(title, summary, keywords),
        "processor_versions": provider_versions,
    }


def _tokenize_text(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _context_signature(context: dict[str, Any]) -> set[str]:
    title = str(context.get("title") or "")
    summary = str(context.get("summary") or "")
    keywords = context.get("keywords") or []
    keyword_text = " ".join(str(word) for word in keywords if word)
    return _tokenize_text(f"{title} {summary} {keyword_text}")


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = left.intersection(right)
    union = left.union(right)
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _merge_unique_list(values: list[Any]) -> list[Any]:
    seen = set()
    merged: list[Any] = []
    for value in values:
        key = json.dumps(value, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        merged.append(value)
    return merged


def _should_merge_contexts(
    primary: dict[str, Any],
    candidate: dict[str, Any],
    min_similarity: float,
) -> bool:
    if primary.get("context_type") != candidate.get("context_type"):
        return False
    primary_versions = primary.get("processor_versions") or {}
    candidate_versions = candidate.get("processor_versions") or {}
    primary_chunk = primary_versions.get("chunk_index")
    candidate_chunk = candidate_versions.get("chunk_index")
    if isinstance(primary_chunk, int) and isinstance(candidate_chunk, int) and primary_chunk != candidate_chunk:
        return False
    primary_title = str(primary.get("title") or "").strip().lower()
    candidate_title = str(candidate.get("title") or "").strip().lower()
    if primary_title and primary_title == candidate_title:
        return True
    primary_summary = str(primary.get("summary") or "").strip().lower()
    candidate_summary = str(candidate.get("summary") or "").strip().lower()
    if primary_summary and candidate_summary:
        if primary_summary in candidate_summary or candidate_summary in primary_summary:
            return True
    similarity = _jaccard_similarity(_context_signature(primary), _context_signature(candidate))
    return similarity >= min_similarity


def _merge_contexts(
    contexts: list[dict[str, Any]],
    min_similarity: float,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for context in contexts:
        if not isinstance(context, dict):
            continue
        inserted = False
        for target in merged:
            if _should_merge_contexts(target, context, min_similarity):
                target_title = str(target.get("title") or "")
                incoming_title = str(context.get("title") or "")
                if len(incoming_title) > len(target_title):
                    target["title"] = incoming_title
                target_summary = str(target.get("summary") or "")
                incoming_summary = str(context.get("summary") or "")
                if incoming_summary and incoming_summary not in target_summary:
                    if target_summary and target_summary not in incoming_summary:
                        target_summary = f"{target_summary} / {incoming_summary}"
                    else:
                        target_summary = incoming_summary
                    target["summary"] = target_summary
                target_keywords = (target.get("keywords") or []) + (context.get("keywords") or [])
                target["keywords"] = _merge_unique_list(target_keywords)
                target_entities = (target.get("entities") or []) + (context.get("entities") or [])
                target["entities"] = _merge_unique_list(target_entities)
                if not target.get("location") and context.get("location"):
                    target["location"] = context.get("location")
                processor_versions = target.get("processor_versions") or {}
                processor_versions["semantic_merge"] = "v1"
                processor_versions["merged_count"] = int(processor_versions.get("merged_count") or 1) + 1
                target["processor_versions"] = processor_versions
                target["vector_text"] = build_vector_text(
                    target.get("title") or "Memory context",
                    target.get("summary") or "",
                    target.get("keywords") or [],
                )
                inserted = True
                break
        if not inserted:
            merged.append(context)
    return merged


async def _upsert_content(
    session, item_id: UUID, role: str, data: dict[str, Any]
) -> None:
    await session.execute(
        delete(ProcessedContent).where(
            ProcessedContent.item_id == item_id,
            ProcessedContent.content_role == role,
        )
    )
    session.add(ProcessedContent(item_id=item_id, content_role=role, data=data))


async def _fetch_item_blob(session, storage, item: SourceItem) -> bytes:
    storage_key = item.storage_key
    if storage_key.startswith("http://") or storage_key.startswith("https://"):
        headers = {}
        if item.connection_id:
            connection = await session.get(DataConnection, item.connection_id)
            if connection and connection.provider == "google_photos":
                access_token = await get_valid_access_token(session, connection)
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(storage_key, headers=headers)
            response.raise_for_status()
            return response.content
    return await asyncio.to_thread(storage.fetch, storage_key)


class FetchBlobStep:
    name = "fetch_blob"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if artifacts.get("blob") is not None:
            return
        blob = await _fetch_item_blob(config.session, config.storage, item)
        artifacts.set("blob", blob)


class ContentHashStep:
    name = "content_hash"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        blob = artifacts.get("blob")
        if blob is None:
            return
        content_hash = hash_bytes(blob)
        item.content_hash = content_hash
        artifacts.set("content_hash", content_hash)
        await artifacts.store.upsert(
            artifact_type="content_hash",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=content_hash,
            payload={"content_hash": content_hash},
        )


class MetadataStep:
    name = "metadata"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        blob = artifacts.get("blob") or b""
        payload = config.payload
        provider_location = payload.get("provider_location")
        if isinstance(provider_location, dict):
            artifacts.set("provider_location", provider_location)
        if payload.get("content_type") and not item.content_type:
            item.content_type = payload.get("content_type")
        if payload.get("original_filename") and not item.original_filename:
            item.original_filename = payload.get("original_filename")
        if payload.get("captured_at") and not item.captured_at:
            parsed = parse_iso_datetime(payload.get("captured_at"))
            if parsed:
                item.captured_at = parsed

        metadata = {
            "size_bytes": len(blob),
            "item_type": item.item_type,
            "captured_at": item.captured_at.isoformat() if item.captured_at else None,
            "storage_key": item.storage_key,
            "content_type": item.content_type,
            "original_filename": item.original_filename,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        if payload.get("duration_sec") is not None:
            metadata["duration_sec"] = payload.get("duration_sec")
        if payload.get("client_tz_offset_minutes") is not None:
            metadata["client_tz_offset_minutes"] = payload.get("client_tz_offset_minutes")
        window_start = payload.get("event_time_window_start")
        window_end = payload.get("event_time_window_end")
        if window_start:
            metadata["event_time_window_start"] = window_start
        if window_end:
            metadata["event_time_window_end"] = window_end
        if isinstance(provider_location, dict):
            metadata["provider_location"] = provider_location
        fingerprint = hash_parts([artifacts.get("content_hash"), metadata.get("size_bytes"), item.content_type])
        await artifacts.store.upsert(
            artifact_type="metadata",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=metadata,
        )
        await _upsert_content(config.session, item.id, "metadata", metadata)
        artifacts.set("metadata", metadata)


class MediaMetadataStep:
    name = "media_metadata"
    version = "v2"
    is_expensive = False

    def _parse_duration_value(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.endswith("s"):
                cleaned = cleaned[:-1].strip()
            try:
                return float(cleaned)
            except ValueError:
                pass
            match = re.match(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$", cleaned)
            if match:
                hours = int(match.group(1) or 0)
                minutes = int(match.group(2))
                seconds = float(match.group(3))
                return hours * 3600 + minutes * 60 + seconds
        return None

    def _duration_from_timebase(self, stream: dict[str, Any]) -> Optional[float]:
        if not isinstance(stream, dict):
            return None
        duration_ts = stream.get("duration_ts")
        time_base = parse_fraction(stream.get("time_base"))
        if duration_ts is None or time_base is None or time_base <= 0:
            return None
        try:
            return float(duration_ts) * float(time_base)
        except (TypeError, ValueError):
            return None

    def _duration_from_frames(self, stream: dict[str, Any]) -> Optional[float]:
        if not isinstance(stream, dict):
            return None
        fps = parse_fraction(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
        nb_frames = stream.get("nb_frames")
        if fps is None or fps <= 0 or nb_frames is None:
            return None
        try:
            return float(nb_frames) / fps
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _duration_from_tags(self, *tag_sets: dict[str, Any]) -> Optional[float]:
        for tags in tag_sets:
            if not isinstance(tags, dict):
                continue
            for key in ("duration", "DURATION", "DURATION-eng"):
                duration = self._parse_duration_value(tags.get(key))
                if duration:
                    return duration
        return None

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type not in {"video", "audio"}:
            return
        content_hash = artifacts.get("content_hash")
        fingerprint = hash_parts([content_hash, self.version])
        existing = await artifacts.store.get("media_metadata", self.name, self.version, fingerprint)
        if existing:
            payload = existing.payload or {}
            artifacts.set("media_metadata", payload)
            location = payload.get("location") or {}
            if isinstance(location, dict) and location.get("latitude") is not None:
                artifacts.set(
                    "provider_location",
                    {
                        "latitude": location.get("latitude"),
                        "longitude": location.get("longitude"),
                        "altitude": location.get("altitude"),
                        "source": "metadata",
                    },
                )
            captured_at = payload.get("captured_at")
            parsed = parse_iso_datetime(captured_at) if isinstance(captured_at, str) else None
            if parsed and not item.captured_at:
                item.captured_at = parsed
            return

        if not ffmpeg_available():
            payload = {"status": "skipped", "reason": "missing_ffmpeg"}
            await artifacts.store.upsert(
                artifact_type="media_metadata",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("media_metadata", payload)
            return

        blob = artifacts.get("blob") or b""
        if not blob:
            return
        if config.settings.media_max_bytes and len(blob) > config.settings.media_max_bytes:
            raise ValueError(
                f"Media size {len(blob)} bytes exceeds {config.settings.media_max_bytes} bytes"
            )

        suffix = Path(item.original_filename or "").suffix
        if not suffix:
            suffix = ".mp4" if item.item_type == "video" else ".wav"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                media_path = Path(tmpdir) / f"source{suffix}"
                media_path.write_bytes(blob)
                info = probe_media(str(media_path))
        except MediaToolError as exc:
            payload = {"status": "error", "error": str(exc)}
            await artifacts.store.upsert(
                artifact_type="media_metadata",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("media_metadata", payload)
            return

        format_info = info.get("format") or {}
        tags = format_info.get("tags") or {}
        streams = info.get("streams") or []
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        stream_tags = (video_stream or {}).get("tags") or {}
        audio_tags = (audio_stream or {}).get("tags") or {}

        creation_time = (
            tags.get("creation_time")
            or tags.get("com.apple.quicktime.creationdate")
            or tags.get("com.apple.quicktime.creation_time")
            or stream_tags.get("creation_time")
            or stream_tags.get("com.apple.quicktime.creationdate")
        )
        location_tag = (
            tags.get("com.apple.quicktime.location.ISO6709")
            or tags.get("location")
            or tags.get("com.apple.quicktime.location")
            or tags.get("com.android.location")
            or tags.get("location-eng")
            or stream_tags.get("com.apple.quicktime.location.ISO6709")
            or stream_tags.get("location")
            or stream_tags.get("com.android.location")
            or stream_tags.get("location-eng")
        )
        location = parse_iso6709(location_tag) if isinstance(location_tag, str) else None

        duration = self._parse_duration_value(format_info.get("duration"))
        duration_source = "format" if duration else None
        if duration is None:
            duration = self._parse_duration_value((video_stream or {}).get("duration")) or self._parse_duration_value(
                (audio_stream or {}).get("duration")
            )
            duration_source = "stream" if duration else duration_source
        if duration is None:
            duration = self._duration_from_timebase(video_stream or {}) or self._duration_from_timebase(
                audio_stream or {}
            )
            duration_source = "time_base" if duration else duration_source
        if duration is None:
            duration = self._duration_from_frames(video_stream or {})
            duration_source = "frames" if duration else duration_source
        if duration is None:
            duration = self._duration_from_tags(tags, stream_tags, audio_tags)
            duration_source = "tags" if duration else duration_source
        if duration is None:
            duration = self._parse_duration_value(config.payload.get("duration_sec"))
            duration_source = "client" if duration else duration_source
        if item.item_type == "video" and isinstance(duration, (int, float)):
            max_duration = min(
                config.settings.video_max_duration_sec,
                config.settings.video_understanding_max_duration_sec,
            )
            if duration > max_duration:
                raise ValueError(
                    f"Video duration {duration:.1f}s exceeds {max_duration}s"
                )
        if item.item_type == "audio" and isinstance(duration, (int, float)):
            if duration > config.settings.audio_max_duration_sec:
                raise ValueError(
                    f"Audio duration {duration:.1f}s exceeds {config.settings.audio_max_duration_sec}s"
                )

        fps = parse_fraction((video_stream or {}).get("avg_frame_rate") or (video_stream or {}).get("r_frame_rate"))
        bit_rate = self._parse_duration_value(format_info.get("bit_rate"))
        if bit_rate is None:
            bit_rate = self._parse_duration_value((video_stream or {}).get("bit_rate")) or self._parse_duration_value(
                (audio_stream or {}).get("bit_rate")
            )
        rotation = None
        if isinstance(stream_tags, dict):
            rotation = stream_tags.get("rotate")

        payload = {
            "status": "ok",
            "size_bytes": len(blob),
            "bit_rate": bit_rate,
            "duration_sec": duration,
            "duration_source": duration_source,
            "width": (video_stream or {}).get("width"),
            "height": (video_stream or {}).get("height"),
            "fps": fps,
            "rotation": rotation,
            "format_name": format_info.get("format_name"),
            "video_codec": (video_stream or {}).get("codec_name"),
            "audio_codec": (audio_stream or {}).get("codec_name"),
            "audio_sample_rate": (audio_stream or {}).get("sample_rate"),
            "audio_channels": (audio_stream or {}).get("channels"),
            "captured_at": creation_time,
            "location": location,
        }
        await artifacts.store.upsert(
            artifact_type="media_metadata",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        artifacts.set("media_metadata", payload)
        parsed = parse_iso_datetime(creation_time) if isinstance(creation_time, str) else None
        if parsed and not item.captured_at:
            item.captured_at = parsed
        if location:
            artifacts.set(
                "provider_location",
                {
                    "latitude": location.get("latitude"),
                    "longitude": location.get("longitude"),
                    "altitude": location.get("altitude"),
                    "source": "metadata",
                },
            )


class ExifStep:
    name = "exif"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "photo":
            return
        blob = artifacts.get("blob")
        if not blob:
            return
        content_hash = artifacts.get("content_hash")
        fingerprint = hash_parts([content_hash, self.version])
        existing = await artifacts.store.get("exif", self.name, self.version, fingerprint)
        if existing:
            artifacts.set("exif", existing.payload or {})
            return

        try:
            from PIL import ExifTags, Image
        except Exception:  # pragma: no cover - optional dependency
            return

        def _rational_to_float(value) -> float:
            if hasattr(value, "numerator") and hasattr(value, "denominator"):
                return float(value.numerator) / float(value.denominator)
            if isinstance(value, tuple) and len(value) == 2:
                return float(value[0]) / float(value[1])
            return float(value)

        def _convert_to_degrees(value) -> float:
            degrees = _rational_to_float(value[0])
            minutes = _rational_to_float(value[1])
            seconds = _rational_to_float(value[2])
            return degrees + (minutes / 60.0) + (seconds / 3600.0)

        def _parse_offset(offset: str | None) -> int | None:
            if not offset:
                return None
            match = re.match(r"^([+-])(\d{2}):?(\d{2})$", offset.strip())
            if not match:
                return None
            sign = -1 if match.group(1) == "-" else 1
            hours = int(match.group(2))
            minutes = int(match.group(3))
            return sign * (hours * 60 + minutes)


        try:
            image = Image.open(BytesIO(blob))
            exif = image.getexif()
        except Exception:
            return

        date_time_original = exif.get(36867) or exif.get(306)
        offset_time = exif.get(36881) or exif.get(36880)
        offset_minutes = _parse_offset(offset_time) if isinstance(offset_time, str) else None

        event_time_utc = None
        parsed_dt = parse_exif_datetime(date_time_original) if isinstance(date_time_original, str) else None
        if parsed_dt and offset_minutes is not None:
            tzinfo = timezone(timedelta(minutes=offset_minutes))
            event_time_utc = parsed_dt.replace(tzinfo=tzinfo).astimezone(timezone.utc)

        gps_info = None
        if hasattr(exif, "get_ifd"):
            try:
                gps_ifd = ExifTags.IFD.GPSInfo if hasattr(ExifTags, "IFD") else 34853
                gps_info = exif.get_ifd(gps_ifd)
            except Exception:
                gps_info = None
        if not gps_info:
            gps_info = exif.get(34853)
        gps_payload = {}
        lat = None
        lng = None
        gps_items = None
        if isinstance(gps_info, dict):
            gps_items = gps_info.items()
        else:
            try:
                gps_items = dict(gps_info).items()
            except Exception:
                gps_items = None
        if gps_items:
            gps_tags = {}
            for key, value in gps_items:
                tag_name = ExifTags.GPSTAGS.get(key, key)
                gps_tags[tag_name] = value
            lat_ref = gps_tags.get("GPSLatitudeRef")
            lat_val = gps_tags.get("GPSLatitude")
            lon_ref = gps_tags.get("GPSLongitudeRef")
            lon_val = gps_tags.get("GPSLongitude")
            if lat_ref and lat_val and lon_ref and lon_val:
                lat = _convert_to_degrees(lat_val)
                if str(lat_ref).upper().startswith("S"):
                    lat = -lat
                lng = _convert_to_degrees(lon_val)
                if str(lon_ref).upper().startswith("W"):
                    lng = -lng
            altitude = gps_tags.get("GPSAltitude")
            if altitude is not None:
                try:
                    altitude = _rational_to_float(altitude)
                except Exception:
                    altitude = None
            gps_payload = {
                "latitude": lat,
                "longitude": lng,
                "altitude": altitude,
            }

        payload = {
            "datetime_original": date_time_original if isinstance(date_time_original, str) else None,
            "timezone_offset_minutes": offset_minutes,
            "event_time_utc": event_time_utc.isoformat() if event_time_utc else None,
            "gps": gps_payload if gps_payload else None,
        }
        await artifacts.store.upsert(
            artifact_type="exif",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        artifacts.set("exif", payload)


class PreviewStep:
    name = "preview"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "photo":
            return
        blob = artifacts.get("blob")
        if not blob:
            return
        content_type = (item.content_type or "").lower()
        filename = (item.original_filename or "").lower()
        heif_types = {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"}
        is_heif = content_type in heif_types or filename.endswith((".heic", ".heif"))
        if not is_heif:
            return

        content_hash = artifacts.get("content_hash") or hash_bytes(blob)
        fingerprint = hash_parts([content_hash, self.version, "jpeg_preview"])
        existing = await artifacts.store.get("preview_image", self.name, self.version, fingerprint)
        if existing:
            artifacts.set("preview", existing.payload or {})
            return

        try:
            from PIL import Image, ImageOps
        except Exception:  # pragma: no cover - optional dependency
            return
        try:
            from pillow_heif import register_heif_opener
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning("HEIF preview skipped for item {}: {}", item.id, exc)
            return

        try:
            register_heif_opener()
            image = Image.open(BytesIO(blob))
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image.thumbnail((1600, 1600))
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
            preview_bytes = output.getvalue()
        except Exception as exc:  # pragma: no cover - image decoding errors
            logger.warning("Preview generation failed for item {}: {}", item.id, exc)
            payload = {"status": "error", "error": str(exc)}
            await artifacts.store.upsert(
                artifact_type="preview_image",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("preview", payload)
            return

        preview_key = f"previews/{item.user_id}/{item.id}.jpg"
        try:
            await asyncio.to_thread(config.storage.store, preview_key, preview_bytes, "image/jpeg")
        except Exception as exc:  # pragma: no cover - external storage dependency
            logger.warning("Preview upload failed for item {}: {}", item.id, exc)
            payload = {"status": "error", "error": str(exc)}
            await artifacts.store.upsert(
                artifact_type="preview_image",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("preview", payload)
            return

        payload = {
            "status": "ok",
            "storage_key": preview_key,
            "content_type": "image/jpeg",
            "width": image.width,
            "height": image.height,
            "source_content_type": item.content_type,
        }
        await artifacts.store.upsert(
            artifact_type="preview_image",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        artifacts.set("preview", payload)


class GeoLocationStep:
    name = "geocode"
    version = "v1"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type not in {"photo", "video", "audio"}:
            return
        exif_payload = artifacts.get("exif") or {}
        gps = exif_payload.get("gps") or {}
        lat = gps.get("latitude")
        lng = gps.get("longitude")
        location_source = "exif"
        provider_location = None
        if lat is None or lng is None:
            candidate = artifacts.get("provider_location") or config.payload.get("provider_location")
            if isinstance(candidate, dict):
                provider_location = candidate
                lat = provider_location.get("latitude")
                lng = provider_location.get("longitude")
                location_source = provider_location.get("source") or "provider"
        if lat is None or lng is None:
            return
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return
        provider = config.settings.maps_geocoding_provider
        fingerprint = hash_parts([provider, lat, lng, self.version])
        existing = await artifacts.store.get("geocode", provider, self.version, fingerprint)
        if existing:
            artifacts.set("geo_location", existing.payload or {})
            return
        try:
            payload = await reverse_geocode(lat, lng, config.settings)
        except Exception as exc:  # pragma: no cover - external service dependency
            logger.warning("Geocoding failed for item {}: {}", item.id, exc)
            payload = {"status": "error", "error": str(exc), "lat": lat, "lng": lng}
        payload.setdefault("source", location_source)
        if provider_location is not None and "provider_location" not in payload:
            payload["provider_location"] = provider_location
        await artifacts.store.upsert(
            artifact_type="geocode",
            producer=provider,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        artifacts.set("geo_location", payload)


class PerceptualHashStep:
    name = "phash"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "photo":
            return
        blob = artifacts.get("blob")
        if not blob:
            return
        phash = compute_image_ahash(blob)
        if not phash:
            return
        item.phash = phash
        artifacts.set("phash", phash)
        fingerprint = hash_parts([artifacts.get("content_hash"), phash])
        await artifacts.store.upsert(
            artifact_type="phash",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload={"phash": phash},
        )


class EventTimeStep:
    name = "event_time"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        override_enabled = bool(config.payload.get("event_time_override"))
        override_time = parse_iso_datetime(config.payload.get("captured_at"))
        if not override_time and item.captured_at:
            override_time = item.captured_at

        exif_payload = artifacts.get("exif") or {}
        exif_time = parse_iso_datetime(exif_payload.get("event_time_utc"))
        exif_time_usable = exif_time is not None
        if not exif_time:
            exif_time = parse_exif_datetime(exif_payload.get("datetime_original"))
            exif_time_usable = exif_time is not None
        client_offset = config.payload.get("client_tz_offset_minutes")
        if exif_time and exif_time.tzinfo is None:
            if isinstance(client_offset, (int, float)):
                tzinfo = timezone(timedelta(minutes=-int(client_offset)))
                exif_time = exif_time.replace(tzinfo=tzinfo).astimezone(timezone.utc)
                exif_time_usable = True
            elif item.provider == "upload":
                exif_time_usable = True
            else:
                exif_time_usable = False
        media_metadata = artifacts.get("media_metadata") or {}
        media_time = None
        if isinstance(media_metadata, dict):
            media_time = parse_iso_datetime(media_metadata.get("captured_at"))

        event_time: Optional[datetime] = None
        source = None
        confidence = None
        if override_enabled and override_time:
            event_time = override_time
            source = "manual"
            confidence = 0.95
        elif exif_time and exif_time_usable:
            event_time = exif_time
            source = "exif"
            confidence = 0.9 if exif_payload.get("event_time_utc") else 0.75
        elif media_time:
            event_time = media_time
            source = "metadata"
            confidence = 0.8
        elif item.captured_at:
            event_time = item.captured_at
            if item.provider and item.provider != "upload":
                source = "provider"
                confidence = 0.85
            else:
                source = "client"
                confidence = 0.7
        else:
            event_time = item.created_at or config.now
            source = "server"
            confidence = 0.4

        event_time = ensure_tz_aware(event_time)
        item.event_time_utc = event_time
        item.event_time_source = source
        item.event_time_confidence = confidence

        payload = {
            "event_time_utc": event_time.isoformat(),
            "event_time_source": source,
            "event_time_confidence": confidence,
        }
        fingerprint = hash_parts([artifacts.get("content_hash"), source, confidence, event_time.isoformat()])
        await artifacts.store.upsert(
            artifact_type="event_time",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )


class DedupStep:
    name = "dedupe"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        content_hash = artifacts.get("content_hash") or item.content_hash
        if not content_hash:
            if item.canonical_item_id is None:
                item.canonical_item_id = item.id
            return
        reprocess_duplicates = config.payload.get("reprocess_duplicates")
        if reprocess_duplicates is None:
            reprocess_duplicates = config.settings.pipeline_reprocess_duplicates
        reprocess_duplicates = bool(reprocess_duplicates)
        canonical_item_id: Optional[UUID] = None

        stmt = (
            select(SourceItem)
            .where(
                SourceItem.user_id == item.user_id,
                SourceItem.content_hash == content_hash,
                SourceItem.id != item.id,
            )
            .order_by(SourceItem.created_at.asc())
            .limit(1)
        )
        result = await config.session.execute(stmt)
        duplicate = result.scalar_one_or_none()
        if duplicate:
            canonical_item_id = duplicate.canonical_item_id or duplicate.id
            if duplicate.canonical_item_id is None:
                duplicate.canonical_item_id = canonical_item_id
            payload = {
                "status": "exact_duplicate",
                "canonical_item_id": str(canonical_item_id),
                "content_hash": content_hash,
                "reprocess": reprocess_duplicates,
            }
            fingerprint = hash_parts([content_hash, "exact", duplicate.id])
            await artifacts.store.upsert(
                artifact_type="dedupe",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("dedupe", payload)
            item.canonical_item_id = canonical_item_id
            if not reprocess_duplicates:
                artifacts.skip_expensive = True
            return

        if item.item_type != "photo" or not item.phash or not item.event_time_utc:
            item.canonical_item_id = item.id
            return

        window = timedelta(minutes=config.settings.dedupe_near_window_minutes)
        start = item.event_time_utc - window
        end = item.event_time_utc + window
        stmt = select(SourceItem).where(
            SourceItem.user_id == item.user_id,
            SourceItem.phash.is_not(None),
            SourceItem.id != item.id,
            SourceItem.event_time_utc >= start,
            SourceItem.event_time_utc <= end,
        )
        result = await config.session.execute(stmt)
        candidates = result.scalars().all()
        for candidate in candidates:
            distance = hamming_distance_hex(item.phash, candidate.phash or "")
            if distance is None:
                continue
            if distance <= config.settings.dedupe_near_hamming_threshold:
                canonical_item_id = candidate.canonical_item_id or candidate.id
                if candidate.canonical_item_id is None:
                    candidate.canonical_item_id = canonical_item_id
                payload = {
                    "status": "near_duplicate",
                    "canonical_item_id": str(canonical_item_id),
                    "phash": item.phash,
                    "distance": distance,
                    "reprocess": reprocess_duplicates,
                }
                fingerprint = hash_parts([item.phash, "near", candidate.id, distance])
                await artifacts.store.upsert(
                    artifact_type="dedupe",
                    producer=self.name,
                    producer_version=self.version,
                    input_fingerprint=fingerprint,
                    payload=payload,
                )
                artifacts.set("dedupe", payload)
                item.canonical_item_id = canonical_item_id
                if not reprocess_duplicates:
                    artifacts.skip_expensive = True
                break
        if item.canonical_item_id is None:
            item.canonical_item_id = item.id


class CaptionStep:
    name = "caption"
    version = "v1"
    is_expensive = False

    def _build_caption(self, item: SourceItem) -> str:
        if item.original_filename:
            stem = Path(item.original_filename).stem
            stem = stem.replace("_", " ").replace("-", " ").strip()
            if stem:
                return stem
        return f"{item.item_type} upload"

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        text = self._build_caption(item)
        metadata = artifacts.get("metadata", {})
        payload = {"text": text, "summary": text, "metadata": metadata}
        fingerprint = hash_parts([artifacts.get("content_hash"), text])
        await artifacts.store.upsert(
            artifact_type="caption",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        await _upsert_content(config.session, item.id, "caption", payload)
        artifacts.set("caption", text)


class OcrStep:
    name = "ocr"
    version = "v1"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "photo":
            return
        content_hash = artifacts.get("content_hash")
        provider = config.settings.ocr_provider
        language_code, _ = _resolve_language(config)
        language_hints = resolve_ocr_language_hints(
            config.settings.ocr_language_hints,
            language_code,
        )
        fingerprint = hash_parts([content_hash, provider, self.version, ",".join(language_hints)])
        existing = await artifacts.store.get("ocr", provider, self.version, fingerprint)
        if existing:
            artifacts.set("ocr_text", (existing.payload or {}).get("text", ""))
            return

        blob = artifacts.get("blob") or b""
        try:
            payload = await run_ocr(
                blob,
                config.settings,
                item.content_type,
                language_hints=language_hints,
            )
        except Exception as exc:  # pragma: no cover - external service dependency
            logger.warning("OCR failed for item {}: {}", item.id, exc)
            payload = {"text": "", "status": "error", "error": str(exc)}
        ocr_text = (payload or {}).get("text", "")
        await artifacts.store.upsert(
            artifact_type="ocr",
            producer=provider,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        await _upsert_content(config.session, item.id, "ocr", payload)
        artifacts.set("ocr_text", ocr_text)


class VlmStep:
    name = "vlm"
    version = "lifelog_image_analysis_v2"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "photo":
            return
        content_hash = artifacts.get("content_hash")
        ocr_text = artifacts.get("ocr_text", "")
        provider = config.settings.vlm_provider
        model = config.settings.gemini_model
        language_code, language_label = _resolve_language(config)
        preference_guidance = build_preference_guidance(config.user_settings)
        fingerprint = hash_parts([content_hash, ocr_text, provider, model, self.version, language_code])
        existing = await artifacts.store.get("vlm_observations", provider, model, fingerprint)
        if existing:
            contexts = (existing.payload or {}).get("contexts", [])
            artifacts.set("contexts", contexts)
            return

        blob = artifacts.get("blob") or b""
        prompt = build_lifelog_image_prompt(
            ocr_text,
            language=language_label,
            extra_guidance=preference_guidance,
        )
        try:
            response = await analyze_image_with_vlm(
                blob,
                prompt,
                config.settings,
                item.content_type,
                user_id=item.user_id,
                item_id=item.id,
                step_name="vlm",
            )
        except Exception as exc:  # pragma: no cover - external service dependency
            logger.warning("VLM failed for item {}: {}", item.id, exc)
            response = {"status": "error", "error": str(exc), "raw_text": "", "parsed": None}
        raw_text = response.get("raw_text", "")
        parsed = response.get("parsed") or {}
        parsed_entry = parsed.get("image_0") if isinstance(parsed, dict) else None
        contexts_in = []
        if isinstance(parsed_entry, dict):
            contexts_in = parsed_entry.get("contexts") or []
        elif isinstance(parsed, dict) and parsed.get("contexts"):
            contexts_in = parsed.get("contexts") or []

        contexts: list[dict[str, Any]] = []
        if isinstance(contexts_in, list):
            for entry in contexts_in:
                if not isinstance(entry, dict):
                    continue
                context_type = entry.get("context_type") or "activity_context"
                title = entry.get("title") or "Memory context"
                summary = entry.get("summary") or ""
                keywords = entry.get("keywords") or extract_keywords(summary)
                entities = entry.get("entities") or []
                location = entry.get("location") or {}
                context = {
                    "context_type": context_type,
                    "title": title,
                    "summary": summary,
                    "keywords": keywords,
                    "entities": entities,
                    "location": location,
                    "vector_text": build_vector_text(title, summary, keywords),
                    "processor_versions": {
                        "vlm_prompt": self.version,
                        "vlm_provider": provider,
                        "vlm_model": model,
                    },
                }
                contexts.append(context)

        if not contexts:
            caption = artifacts.get("caption") or "Photo memory"
            summary = caption if not ocr_text else f"{caption}. Text noticed: {ocr_text[:120]}"
            keywords = extract_keywords(summary)
            title = "Activity snapshot"
            contexts = [
                {
                    "context_type": "activity_context",
                    "title": title,
                    "summary": summary,
                    "keywords": keywords,
                    "entities": [],
                    "location": {},
                    "vector_text": build_vector_text(title, summary, keywords),
                    "processor_versions": {
                        "vlm_prompt": self.version,
                        "vlm_provider": provider,
                        "vlm_model": model,
                    },
                }
            ]

        payload = {
            "status": response.get("status"),
            "error": response.get("error"),
            "raw_text": raw_text[:4000],
            "contexts": contexts,
        }
        await artifacts.store.upsert(
            artifact_type="vlm_observations",
            producer=provider,
            producer_version=model,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        artifacts.set("contexts", contexts)


class MediaChunkUnderstandingStep:
    name = "media_chunk_understanding"
    version = "v2"
    is_expensive = True

    def _compute_video_chunk_duration(self, metadata: dict[str, Any], config: PipelineConfig) -> int:
        bit_rate = metadata.get("bit_rate")
        fallback = config.settings.video_chunk_duration_sec
        target_bytes = config.settings.media_chunk_target_bytes
        duration = fallback
        if isinstance(bit_rate, (int, float)) and bit_rate > 0:
            derived = int((target_bytes * 8) / bit_rate)
            if derived > 0:
                duration = min(fallback, derived)
        return max(1, duration)

    def _compute_audio_chunk_duration(self, config: PipelineConfig) -> int:
        bytes_per_sec = config.settings.audio_sample_rate_hz * config.settings.audio_channels * 2
        fallback = config.settings.audio_chunk_duration_sec
        if bytes_per_sec <= 0:
            return fallback
        derived = int(config.settings.media_chunk_target_bytes / bytes_per_sec)
        if derived <= 0:
            return fallback
        return max(1, min(fallback, derived))

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type not in {"video", "audio"}:
            return
        blob = artifacts.get("blob")
        if not blob:
            logger.info("media_chunk_understanding skipped: no blob for item {}", item.id)
            return
        if not ffmpeg_available():
            logger.info("media_chunk_understanding skipped: ffmpeg missing for item {}", item.id)
            payload = {"status": "skipped", "reason": "missing_ffmpeg"}
            await artifacts.store.upsert(
                artifact_type="media_chunk_analysis",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=hash_parts([artifacts.get("content_hash"), self.version]),
                payload=payload,
            )
            artifacts.set("contexts", [])
            artifacts.set("transcript_text", "")
            artifacts.set("transcript_segments", [])
            return

        media_metadata = artifacts.get("media_metadata") or {}
        duration_sec = media_metadata.get("duration_sec")
        if item.item_type == "video" and isinstance(duration_sec, (int, float)):
            max_duration = min(
                config.settings.video_max_duration_sec,
                config.settings.video_understanding_max_duration_sec,
            )
            if duration_sec > max_duration:
                logger.info(
                    "media_chunk_understanding skipped: video duration {}s exceeds {}s for item {}",
                    f"{duration_sec:.1f}",
                    max_duration,
                    item.id,
                )
                return
        if item.item_type == "audio" and isinstance(duration_sec, (int, float)):
            if duration_sec > config.settings.audio_max_duration_sec:
                logger.info(
                    "media_chunk_understanding skipped: audio duration {}s exceeds {}s for item {}",
                    f"{duration_sec:.1f}",
                    config.settings.audio_max_duration_sec,
                    item.id,
                )
                return

        provider = (
            config.settings.video_understanding_provider
            if item.item_type == "video"
            else config.settings.audio_understanding_provider
        )
        model = (
            config.settings.video_understanding_model
            if item.item_type == "video"
            else config.settings.audio_understanding_model
        )
        language_code, language_label = _resolve_language(config)
        if provider == "none":
            logger.info("media_chunk_understanding skipped: provider disabled for item {}", item.id)
            payload = {"status": "disabled", "reason": "provider_disabled"}
            await artifacts.store.upsert(
                artifact_type="media_chunk_analysis",
                producer=provider,
                producer_version=model,
                input_fingerprint=hash_parts([artifacts.get("content_hash"), self.version, "disabled"]),
                payload=payload,
            )
            artifacts.set("contexts", [])
            artifacts.set("transcript_text", "")
            artifacts.set("transcript_segments", [])
            return
        chunk_duration = (
            self._compute_video_chunk_duration(media_metadata, config)
            if item.item_type == "video"
            else self._compute_audio_chunk_duration(config)
        )
        fingerprint_parts: list[Any] = [
            artifacts.get("content_hash"),
            provider,
            model,
            self.version,
            chunk_duration,
            config.settings.media_chunk_target_bytes,
            language_code,
        ]
        if item.item_type == "audio":
            fingerprint_parts.extend(
                [
                    config.settings.audio_vad_enabled,
                    config.settings.audio_vad_silence_db,
                    config.settings.audio_vad_min_silence_sec,
                    config.settings.audio_vad_padding_sec,
                    config.settings.audio_vad_min_segment_sec,
                ]
            )
        fingerprint = hash_parts(fingerprint_parts)
        existing = await artifacts.store.get("media_chunk_analysis", provider, model, fingerprint)
        if existing:
            payload = existing.payload or {}
            artifacts.set("contexts", payload.get("contexts") or [])
            artifacts.set("transcript_text", payload.get("transcript_text") or "")
            artifacts.set("transcript_segments", payload.get("segments") or [])
            return

        suffix = Path(item.original_filename or "").suffix
        if not suffix:
            suffix = ".mp4" if item.item_type == "video" else ".wav"

        transcript_segments: list[dict[str, Any]] = []
        transcript_lines: list[str] = []
        contexts: list[dict[str, Any]] = []
        errors: list[str] = []
        max_chunks = config.settings.media_chunk_max_chunks
        chunk_count = 0
        max_contexts = 80

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                media_path = Path(tmpdir) / f"source{suffix}"
                media_path.write_bytes(blob)
                if item.item_type == "video":
                    chunk_paths = segment_video(
                        str(media_path),
                        tmpdir,
                        chunk_duration_sec=chunk_duration,
                        output_ext=suffix or ".mp4",
                    )
                    prompt = build_lifelog_video_chunk_prompt(
                        language=language_label,
                        extra_guidance=build_preference_guidance(config.user_settings),
                    )
                    max_bytes = config.settings.video_understanding_max_bytes
                    content_type = item.content_type or "video/mp4"
                else:
                    chunk_infos = segment_audio_with_vad(
                        str(media_path),
                        tmpdir,
                        chunk_duration_sec=chunk_duration,
                        sample_rate_hz=config.settings.audio_sample_rate_hz,
                        channels=config.settings.audio_channels,
                        vad_enabled=config.settings.audio_vad_enabled,
                        vad_silence_db=config.settings.audio_vad_silence_db,
                        vad_min_silence_sec=config.settings.audio_vad_min_silence_sec,
                        vad_padding_sec=config.settings.audio_vad_padding_sec,
                        vad_min_segment_sec=config.settings.audio_vad_min_segment_sec,
                        duration_sec=duration_sec if isinstance(duration_sec, (int, float)) else None,
                    )
                    prompt = build_lifelog_audio_chunk_prompt(
                        language=language_label,
                        extra_guidance=build_preference_guidance(config.user_settings),
                    )
                    max_bytes = config.settings.audio_understanding_max_bytes
                    content_type = "audio/wav"

                if item.item_type == "video":
                    chunk_infos = [
                        {
                            "path": path,
                            "start_ms": idx * chunk_duration * 1000,
                            "end_ms": int(
                                (min(duration_sec, (idx + 1) * chunk_duration) if isinstance(duration_sec, (int, float))
                                 else (idx + 1) * chunk_duration)
                                * 1000
                            ),
                        }
                        for idx, path in enumerate(chunk_paths)
                    ]

                if not chunk_infos:
                    raise MediaToolError("no media chunks produced")

                for idx, chunk_info in enumerate(chunk_infos):
                    if idx >= max_chunks:
                        break
                    chunk_file = Path(chunk_info["path"])
                    if not chunk_file.exists():
                        continue
                    chunk_bytes = chunk_file.read_bytes()
                    start_ms = int(chunk_info["start_ms"])
                    end_ms = int(chunk_info["end_ms"])
                    if max_bytes and len(chunk_bytes) > max_bytes:
                        errors.append("chunk_too_large")
                        transcript_segments.append(
                            {
                                "start_ms": start_ms,
                                "end_ms": end_ms,
                                "text": "",
                                "status": "skipped",
                                "error": "max_bytes",
                            }
                        )
                        continue

                    if item.item_type == "video":
                        response = await analyze_video_with_gemini(
                            chunk_bytes,
                            prompt,
                            config.settings,
                            content_type,
                            user_id=item.user_id,
                            item_id=item.id,
                            step_name="media_chunk_understanding",
                        )
                    else:
                        response = await analyze_audio_with_gemini(
                            chunk_bytes,
                            prompt,
                            config.settings,
                            content_type,
                            user_id=item.user_id,
                            item_id=item.id,
                            step_name="media_chunk_understanding",
                        )

                    parsed = response.get("parsed") or {}
                    transcript = ""
                    contexts_in = []
                    if isinstance(parsed, dict):
                        raw_transcript = parsed.get("transcript")
                        if isinstance(raw_transcript, str):
                            transcript = raw_transcript.strip()
                        contexts_in = parsed.get("contexts") or []
                    if transcript:
                        transcript_lines.append(transcript)

                    transcript_segments.append(
                        {
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "text": transcript,
                            "status": response.get("status"),
                            "error": response.get("error"),
                        }
                    )

                    provider_versions = {
                        "media_chunk": self.version,
                        "provider": provider,
                        "model": model,
                        "chunk_index": idx,
                        "chunk_start_ms": start_ms,
                    }
                    if isinstance(contexts_in, list):
                        for entry in contexts_in[:5]:
                            if len(contexts) >= max_contexts:
                                break
                            if isinstance(entry, dict):
                                contexts.append(
                                    _normalize_context_entry(
                                        entry,
                                        default_title=f"{item.item_type.title()} moment",
                                        provider_versions=provider_versions,
                                    )
                                )
                    if response.get("status") != "ok":
                        errors.append(response.get("error") or "error")
                    chunk_count += 1
        except MediaToolError as exc:
            payload = {"status": "error", "error": str(exc)}
            await artifacts.store.upsert(
                artifact_type="media_chunk_analysis",
                producer=provider,
                producer_version=model,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("contexts", [])
            artifacts.set("transcript_text", "")
            artifacts.set("transcript_segments", [])
            return

        transcript_text = "\n".join(transcript_lines).strip()
        status = "ok" if transcript_text or contexts else "skipped"
        if errors:
            status = "partial"

        transcript_payload = {
            "status": status,
            "error": errors[0] if errors else None,
            "chunk_duration_sec": chunk_duration,
            "chunk_count": len(transcript_segments),
            "text": transcript_text,
            "segments": transcript_segments,
        }
        transcript_bytes = json.dumps(transcript_payload, ensure_ascii=True).encode("utf-8")
        transcript_storage_key = None
        if len(transcript_bytes) > config.settings.transcription_storage_max_bytes:
            transcript_storage_key = f"users/{item.user_id}/derived/{item.id}/transcript/transcript.json"
            try:
                await asyncio.to_thread(
                    config.storage.store, transcript_storage_key, transcript_bytes, "application/json"
                )
            except Exception as exc:  # pragma: no cover - external storage dependency
                logger.warning("Transcript storage failed for item {}: {}", item.id, exc)
            transcript_payload = {
                "status": status,
                "error": errors[0] if errors else None,
                "chunk_duration_sec": chunk_duration,
                "chunk_count": len(transcript_segments),
                "storage_key": transcript_storage_key,
                "text": _truncate_text(transcript_text, 4000),
            }

        await artifacts.store.upsert(
            artifact_type="transcription",
            producer=provider,
            producer_version=model,
            input_fingerprint=fingerprint,
            payload=transcript_payload,
            storage_key=transcript_storage_key,
        )
        await _upsert_content(config.session, item.id, "transcription", transcript_payload)

        analysis_payload = {
            "status": status,
            "error": errors[0] if errors else None,
            "chunk_duration_sec": chunk_duration,
            "chunk_count": chunk_count,
            "contexts": contexts,
            "transcript_text": _truncate_text(transcript_text, 6000),
            "segments": transcript_segments[:200],
        }
        await artifacts.store.upsert(
            artifact_type="media_chunk_analysis",
            producer=provider,
            producer_version=model,
            input_fingerprint=fingerprint,
            payload=analysis_payload,
        )

        artifacts.set("contexts", contexts)
        artifacts.set(
            "transcript_text",
            _truncate_text(transcript_text, 6000) if transcript_storage_key else transcript_text,
        )
        artifacts.set("transcript_segments", transcript_segments)


class KeyframeExtractionStep:
    name = "keyframes"
    version = "v4"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "video":
            return
        if not config.settings.video_keyframes_always:
            logger.info("keyframes skipped: video_keyframes_always disabled for item {}", item.id)
            return

        content_hash = artifacts.get("content_hash")
        fingerprint = hash_parts(
            [
                content_hash,
                self.version,
                config.settings.video_keyframe_mode,
                config.settings.video_keyframe_interval_sec,
                config.settings.video_scene_threshold,
                config.settings.video_max_keyframes,
            ]
        )
        existing = await artifacts.store.get("keyframes", self.name, self.version, fingerprint)
        if existing:
            payload = existing.payload or {}
            artifacts.set("keyframes", payload.get("frames") or [])
            return

        if not ffmpeg_available():
            logger.info("keyframes skipped: ffmpeg missing for item {}", item.id)
            payload = {"status": "skipped", "reason": "missing_ffmpeg"}
            await artifacts.store.upsert(
                artifact_type="keyframes",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("keyframes", [])
            return

        blob = artifacts.get("blob") or b""
        if not blob:
            logger.info("keyframes skipped: no blob for item {}", item.id)
            return
        suffix = Path(item.original_filename or "").suffix or ".mp4"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                media_path = Path(tmpdir) / f"source{suffix}"
                media_path.write_bytes(blob)
                frames, mode, _times = extract_keyframes(
                    str(media_path),
                    tmpdir,
                    mode=config.settings.video_keyframe_mode,
                    interval_sec=config.settings.video_keyframe_interval_sec,
                    scene_threshold=config.settings.video_scene_threshold,
                    max_frames=config.settings.video_max_keyframes,
                )
                if not frames and config.settings.video_keyframe_mode == "scene":
                    frames, mode, _times = extract_keyframes(
                        str(media_path),
                        tmpdir,
                        mode="interval",
                        interval_sec=config.settings.video_keyframe_interval_sec,
                        scene_threshold=config.settings.video_scene_threshold,
                        max_frames=max(1, min(config.settings.video_max_keyframes, 4)),
                    )
                poster_path = Path(tmpdir) / "poster.jpg"
                try:
                    extract_single_frame(str(media_path), str(poster_path), timestamp_sec=0.0)
                except MediaToolError as exc:
                    logger.warning("Poster extraction failed for item {}: {}", item.id, exc)
                    poster_path = None
                stored_frames: list[dict[str, Any]] = []
                poster_storage_key = None
                if poster_path and poster_path.exists():
                    poster_storage_key = f"users/{item.user_id}/derived/{item.id}/poster/poster.jpg"
                    poster_bytes = poster_path.read_bytes()
                    await asyncio.to_thread(
                        config.storage.store, poster_storage_key, poster_bytes, "image/jpeg"
                    )
                for idx, frame in enumerate(frames):
                    frame_path = Path(frame["path"])
                    if not frame_path.exists():
                        continue
                    t_sec = frame.get("t_sec")
                    time_label = f"{t_sec:.2f}".replace(".", "p") if isinstance(t_sec, (int, float)) else f"{idx}"
                    storage_key = (
                        f"users/{item.user_id}/derived/{item.id}/keyframes/{idx:04d}_{time_label}.jpg"
                    )
                    frame_bytes = frame_path.read_bytes()
                    await asyncio.to_thread(config.storage.store, storage_key, frame_bytes, "image/jpeg")
                    stored_frames.append({"t_sec": t_sec, "storage_key": storage_key})
        except MediaToolError as exc:
            payload = {"status": "error", "error": str(exc)}
            await artifacts.store.upsert(
                artifact_type="keyframes",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            artifacts.set("keyframes", [])
            return

        payload = {
            "status": "ok",
            "mode": mode,
            "interval_sec": config.settings.video_keyframe_interval_sec,
            "scene_threshold": config.settings.video_scene_threshold,
            "frames": stored_frames,
            "poster": {
                "t_sec": 0.0,
                "storage_key": poster_storage_key,
            }
            if poster_storage_key
            else None,
        }
        await artifacts.store.upsert(
            artifact_type="keyframes",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
        )
        artifacts.set("keyframes", stored_frames)


class VideoPreviewStep:
    name = "video_preview"
    version = "v1"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "video":
            return
        if not config.settings.video_preview_enabled:
            return
        if not ffmpeg_available():
            payload = {"status": "skipped", "reason": "missing_ffmpeg"}
            await artifacts.store.upsert(
                artifact_type="video_preview",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=hash_parts([artifacts.get("content_hash"), self.version, "missing_ffmpeg"]),
                payload=payload,
            )
            return

        blob = artifacts.get("blob") or b""
        if not blob:
            return

        content_hash = artifacts.get("content_hash")
        fingerprint = hash_parts(
            [
                content_hash,
                self.version,
                config.settings.video_preview_duration_sec,
                config.settings.video_preview_max_width,
                config.settings.video_preview_fps,
                config.settings.video_preview_bitrate_kbps,
            ]
        )
        existing = await artifacts.store.get("video_preview", self.name, self.version, fingerprint)
        if existing:
            return

        duration_sec = config.settings.video_preview_duration_sec
        media_metadata = artifacts.get("media_metadata") or {}
        meta_duration = media_metadata.get("duration_sec")
        if isinstance(meta_duration, (int, float)) and meta_duration > 0:
            duration_sec = min(duration_sec, int(meta_duration))

        suffix = Path(item.original_filename or "").suffix or ".mp4"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                media_path = Path(tmpdir) / f"source{suffix}"
                media_path.write_bytes(blob)
                preview_path = Path(tmpdir) / "preview.mp4"
                create_video_preview(
                    str(media_path),
                    str(preview_path),
                    duration_sec=duration_sec,
                    max_width=config.settings.video_preview_max_width,
                    fps=config.settings.video_preview_fps,
                    bitrate_kbps=config.settings.video_preview_bitrate_kbps,
                )
                preview_bytes = preview_path.read_bytes()
        except MediaToolError as exc:
            payload = {"status": "error", "error": str(exc)}
            await artifacts.store.upsert(
                artifact_type="video_preview",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            return

        preview_key = f"users/{item.user_id}/derived/{item.id}/preview/preview.mp4"
        try:
            await asyncio.to_thread(
                config.storage.store,
                preview_key,
                preview_bytes,
                "video/mp4",
            )
        except Exception as exc:  # pragma: no cover - external storage dependency
            logger.warning("Preview upload failed for item {}: {}", item.id, exc)
            payload = {"status": "error", "error": str(exc)}
            await artifacts.store.upsert(
                artifact_type="video_preview",
                producer=self.name,
                producer_version=self.version,
                input_fingerprint=fingerprint,
                payload=payload,
            )
            return

        payload = {
            "status": "ok",
            "storage_key": preview_key,
            "content_type": "video/mp4",
            "duration_sec": duration_sec,
        }
        await artifacts.store.upsert(
            artifact_type="video_preview",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload=payload,
            storage_key=preview_key,
        )


class MediaSummaryContextStep:
    name = "media_summary"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type not in {"video", "audio"}:
            return
        contexts = artifacts.get("contexts") or []
        transcript_text = (artifacts.get("transcript_text") or "").strip()
        if not contexts and not transcript_text:
            return
        if any(
            isinstance(context, dict) and context.get("processor_versions", {}).get("media_summary") == self.version
            for context in contexts
        ):
            return

        summary_parts: list[str] = []
        for entry in contexts:
            if isinstance(entry, dict):
                summary = entry.get("summary")
                if summary:
                    summary_parts.append(summary)
            if len(summary_parts) >= 3:
                break
        if not summary_parts and transcript_text:
            summary_parts.append(_truncate_text(transcript_text, 400))
        summary = _truncate_text(" ".join(summary_parts).strip(), 600)
        if transcript_text and transcript_text not in summary:
            summary = _truncate_text(f"{summary} Transcript: {_truncate_text(transcript_text, 300)}", 800)

        title = f"{item.item_type.title()} summary"
        keywords = extract_keywords(summary)
        summary_context = {
            "context_type": "activity_context",
            "title": title,
            "summary": summary,
            "keywords": keywords,
            "entities": [],
            "location": {},
            "vector_text": build_vector_text(title, summary, keywords),
            "processor_versions": {"media_summary": self.version},
        }
        contexts = [summary_context] + list(contexts)
        artifacts.set("contexts", contexts)




class GenericContextStep:
    name = "generic_context"
    version = "v1"
    is_expensive = False

    def _context_type(self, item: SourceItem) -> str:
        if item.item_type == "document":
            return "knowledge_context"
        return "activity_context"

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type == "photo":
            return
        if artifacts.get("contexts"):
            return
        caption = artifacts.get("caption") or f"{item.item_type} upload"
        context_type = self._context_type(item)
        title = f"{item.item_type.title()} capture"
        keywords = extract_keywords(caption)
        context = {
            "context_type": context_type,
            "title": title,
            "summary": caption,
            "keywords": keywords,
            "entities": [],
            "location": {},
            "vector_text": build_vector_text(title, caption, keywords),
            "processor_versions": {"generic": self.version},
        }
        artifacts.set("contexts", [context])


class TranscriptContextStep:
    name = "transcript_context"
    version = "v1"
    is_expensive = False

    def _context_type(self, item: SourceItem) -> str:
        if item.item_type == "audio":
            return "knowledge_context"
        return "activity_context"

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type not in {"audio", "video"}:
            return
        transcript_text = (artifacts.get("transcript_text") or "").strip()
        if not transcript_text:
            return
        caption = artifacts.get("caption") or f"{item.item_type} upload"
        snippet = _truncate_text(transcript_text, 400)
        summary = snippet
        if caption and caption not in snippet:
            summary = f"{caption}. Transcript: {snippet}"
        title = f"{item.item_type.title()} transcript"
        keywords = extract_keywords(transcript_text)
        vector_text = build_vector_text(
            title,
            _truncate_text(transcript_text, 2000),
            keywords,
        )
        provider = (
            config.settings.video_understanding_provider
            if item.item_type == "video"
            else config.settings.audio_understanding_provider
        )
        model = (
            config.settings.video_understanding_model
            if item.item_type == "video"
            else config.settings.audio_understanding_model
        )
        context = {
            "context_type": self._context_type(item),
            "title": title,
            "summary": summary,
            "keywords": keywords,
            "entities": [],
            "location": {},
            "vector_text": vector_text,
            "processor_versions": {
                "transcription_step": self.version,
                "transcription_provider": provider,
                "transcription_model": model,
            },
        }
        contexts = artifacts.get("contexts") or []
        if isinstance(contexts, list):
            contexts.append(context)
        else:
            contexts = [context]
        artifacts.set("contexts", contexts)


class UserAnnotationStep:
    """Create user_annotation context from OpenClaw-provided metadata.

    This step creates a ProcessedContext with context_type="user_annotation"
    from user-provided description, tags, people, and location data.
    These annotations are preserved across reprocessing.
    """

    name = "user_annotation"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        openclaw_context = config.payload.get("openclaw_context")
        if not openclaw_context:
            return

        description = openclaw_context.get("description")
        tags = openclaw_context.get("tags") or []
        people = openclaw_context.get("people") or []
        location_data = openclaw_context.get("location") or {}

        # Skip if no meaningful content
        if not description and not tags and not people:
            return

        # Check if user_annotation already exists for this item (avoid duplicates)
        existing_stmt = select(ProcessedContext).where(
            ProcessedContext.user_id == item.user_id,
            ProcessedContext.context_type == "user_annotation",
            ProcessedContext.source_item_ids.contains([item.id]),
        )
        result = await config.session.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        # Build entities list from people and location
        entities: list[dict] = []
        for person in people:
            entities.append({
                "type": "person",
                "name": person,
                "confidence": 1.0,
            })
        if location_data.get("name"):
            entities.append({
                "type": "place",
                "name": location_data["name"],
                "confidence": 1.0,
            })

        # Build location dict
        location: dict = {}
        if location_data:
            if location_data.get("lat") is not None:
                location["lat"] = location_data["lat"]
            if location_data.get("lng") is not None:
                location["lng"] = location_data["lng"]
            if location_data.get("name"):
                location["name"] = location_data["name"]
            if location_data.get("address"):
                location["formatted_address"] = location_data["address"]

        # Build title and summary
        title = (description[:100] if description else "User annotation").strip()
        summary = description or ""

        # Build vector text for embeddings
        vector_text = build_vector_text(title, summary, tags)

        # Build processor_versions
        processor_versions = {
            "source": "user",
            "openclaw": True,
            "action": "annotate",
            "prompt_name": "user_annotation",  # Sentinel, not a real prompt
        }

        event_time = item.event_time_utc or item.captured_at or item.created_at or config.now
        event_time = ensure_tz_aware(event_time)

        if existing:
            # Update existing annotation
            existing.title = title
            existing.summary = summary
            existing.keywords = tags
            existing.entities = entities
            existing.location = location if location else {}
            existing.vector_text = vector_text
            existing.processor_versions = processor_versions
            await config.session.flush()
            logger.info("Updated user_annotation context {} for item {}", existing.id, item.id)
        else:
            # Create new annotation
            record = ProcessedContext(
                user_id=item.user_id,
                context_type="user_annotation",
                title=title,
                summary=summary,
                keywords=tags,
                entities=entities,
                location=location if location else {},
                event_time_utc=event_time,
                start_time_utc=None,
                end_time_utc=None,
                is_episode=False,
                source_item_ids=[item.id],
                merged_from_context_ids=[],
                vector_text=vector_text,
                processor_versions=processor_versions,
            )
            config.session.add(record)
            await config.session.flush()
            logger.info("Created user_annotation context {} for item {}", record.id, item.id)


class ContextPersistStep:
    name = "contexts"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        contexts = artifacts.get("contexts") or []
        geo_location = artifacts.get("geo_location") or {}
        geo_info = {}
        if geo_location.get("status") == "ok":
            geo_info = {
                "formatted_address": geo_location.get("formatted_address"),
                "lat": geo_location.get("lat"),
                "lng": geo_location.get("lng"),
                "place_id": geo_location.get("place_id"),
                "components": geo_location.get("components") or {},
            }
        if geo_info and not any(
            isinstance(context, dict) and context.get("context_type") == "location_context"
            for context in contexts
        ):
            title = geo_info.get("formatted_address") or "Location"
            summary = geo_info.get("formatted_address") or "Captured location"
            keywords = extract_keywords(summary)
            contexts.append(
                {
                    "context_type": "location_context",
                    "title": title,
                    "summary": summary,
                    "keywords": keywords,
                    "entities": [],
                    "location": geo_info,
                    "vector_text": build_vector_text(title, summary, keywords),
                    "processor_versions": {"geocode": GeoLocationStep.version},
                }
            )
        if not contexts:
            dedupe = artifacts.get("dedupe") or {}
            canonical_id = dedupe.get("canonical_item_id")
            if canonical_id and not dedupe.get("reprocess"):
                try:
                    canonical_uuid = UUID(str(canonical_id))
                except ValueError:
                    canonical_uuid = None
                if canonical_uuid:
                    stmt = select(ProcessedContext).where(
                        ProcessedContext.user_id == item.user_id,
                        ProcessedContext.is_episode.is_(False),
                        ProcessedContext.source_item_ids.contains([canonical_uuid]),
                    )
                    result = await config.session.execute(stmt)
                    canonical_contexts = result.scalars().all()
                    if canonical_contexts:
                        context_ids: list[str] = []
                        for context in canonical_contexts:
                            if item.id not in context.source_item_ids:
                                context.source_item_ids = list(context.source_item_ids) + [item.id]
                            context_ids.append(str(context.id))
                        await config.session.flush()
                        artifacts.set("context_ids", context_ids)
                        artifacts.set("context_records", canonical_contexts)
                        fingerprint = hash_parts([canonical_id, item.id, "reuse"])
                        await artifacts.store.upsert(
                            artifact_type="contexts",
                            producer=self.name,
                            producer_version=self.version,
                            input_fingerprint=fingerprint,
                            payload={
                                "context_ids": context_ids,
                                "canonical_item_id": str(canonical_id),
                                "reused": True,
                            },
                        )
            return
        if config.settings.semantic_merge_enabled and isinstance(contexts, list):
            contexts = _merge_contexts(contexts, config.settings.semantic_merge_min_jaccard)
            artifacts.set("contexts", contexts)
        context_signature = hash_parts(
            [json.dumps(contexts, sort_keys=True), artifacts.get("content_hash")]
        )
        existing = await artifacts.store.get("contexts", self.name, self.version, context_signature)
        if existing:
            context_ids = (existing.payload or {}).get("context_ids", [])
            artifacts.set("context_ids", context_ids)
            return

        # Delete existing non-episode contexts, but preserve user_annotation contexts
        # (user-provided annotations from OpenClaw/API should survive reprocessing)
        await config.session.execute(
            delete(ProcessedContext).where(
                ProcessedContext.user_id == item.user_id,
                ProcessedContext.is_episode.is_(False),
                ProcessedContext.source_item_ids.contains([item.id]),
                ProcessedContext.context_type != "user_annotation",
            )
        )

        context_records: list[ProcessedContext] = []
        event_time = item.event_time_utc or item.captured_at or item.created_at or config.now
        event_time = ensure_tz_aware(event_time)
        for context in contexts:
            record = ProcessedContext(
                user_id=item.user_id,
                context_type=context.get("context_type", "activity_context"),
                title=context.get("title") or "Memory context",
                summary=context.get("summary") or "",
                keywords=context.get("keywords") or [],
                entities=context.get("entities") or [],
                location=context.get("location") or (geo_info if geo_info else {}),
                event_time_utc=event_time,
                start_time_utc=None,
                end_time_utc=None,
                is_episode=False,
                source_item_ids=[item.id],
                merged_from_context_ids=[],
                vector_text=context.get("vector_text") or "",
                processor_versions=context.get("processor_versions") or {},
            )
            config.session.add(record)
            context_records.append(record)

        await config.session.flush()
        context_ids = [str(record.id) for record in context_records]
        artifacts.set("context_ids", context_ids)
        artifacts.set("context_records", context_records)

        await artifacts.store.upsert(
            artifact_type="contexts",
            producer=self.name,
            producer_version=self.version,
            input_fingerprint=context_signature,
            payload={"context_ids": context_ids},
        )

        primary_context = context_records[0] if context_records else None
        if primary_context:
            caption_payload = {
                "text": primary_context.summary,
                "summary": primary_context.summary,
                "metadata": artifacts.get("metadata", {}),
            }
            await _upsert_content(config.session, item.id, "caption", caption_payload)


class EmbeddingStep:
    name = "embeddings"
    version = "v2"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        context_ids = artifacts.get("context_ids") or []
        if not context_ids:
            return
        model = config.settings.embedding_model
        fingerprint = hash_parts([model, ",".join(context_ids)])
        existing = await artifacts.store.get("embeddings", self.name, model, fingerprint)
        if existing:
            return
        context_records = artifacts.get("context_records")
        if not context_records:
            stmt = select(ProcessedContext).where(ProcessedContext.id.in_([UUID(cid) for cid in context_ids]))
            result = await config.session.execute(stmt)
            context_records = result.scalars().all()

        try:
            upsert_context_embeddings(context_records)
        except Exception as exc:  # pragma: no cover - external service dependency
            logger.warning("Failed to upsert embeddings for item {}: {}", item.id, exc)
            return
        await artifacts.store.upsert(
            artifact_type="embeddings",
            producer=self.name,
            producer_version=model,
            input_fingerprint=fingerprint,
            payload={"context_ids": context_ids},
        )


class EpisodeEnqueueStep:
    name = "episode_merge"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if not config.settings.episode_merge_enabled:
            return
        try:
            from ..celery_app import celery_app
        except Exception:  # pragma: no cover - import guard
            return
        celery_app.send_task("episodes.update_for_item", args=[str(item.id)])


class MemoryGraphEnqueueStep:
    name = "memory_graph"
    version = "v1"
    is_expensive = False

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if not getattr(config.settings, "memory_graph_enabled", True):
            return
        try:
            from ..celery_app import celery_app
        except Exception:  # pragma: no cover - import guard
            return
        celery_app.send_task("memory_graph.update_for_item", args=[str(item.id)])


COMMON_STEPS: list[PipelineStep] = [
    FetchBlobStep(),
    ContentHashStep(),
    MetadataStep(),
    MediaMetadataStep(),
    ExifStep(),
    PreviewStep(),
    PerceptualHashStep(),
    EventTimeStep(),
    DedupStep(),
    GeoLocationStep(),
    CaptionStep(),
]

IMAGE_STEPS: list[PipelineStep] = [
    OcrStep(),
    VlmStep(),
    UserAnnotationStep(),
    ContextPersistStep(),
    EmbeddingStep(),
    MemoryGraphEnqueueStep(),
    EpisodeEnqueueStep(),
]

VIDEO_STEPS: list[PipelineStep] = [
    KeyframeExtractionStep(),
    VideoPreviewStep(),
    MediaChunkUnderstandingStep(),
    MediaSummaryContextStep(),
    TranscriptContextStep(),
    GenericContextStep(),
    UserAnnotationStep(),
    ContextPersistStep(),
    EmbeddingStep(),
    MemoryGraphEnqueueStep(),
    EpisodeEnqueueStep(),
]

AUDIO_STEPS: list[PipelineStep] = [
    MediaChunkUnderstandingStep(),
    MediaSummaryContextStep(),
    TranscriptContextStep(),
    GenericContextStep(),
    UserAnnotationStep(),
    ContextPersistStep(),
    EmbeddingStep(),
    MemoryGraphEnqueueStep(),
    EpisodeEnqueueStep(),
]

GENERIC_STEPS: list[PipelineStep] = [
    GenericContextStep(),
    UserAnnotationStep(),
    ContextPersistStep(),
    EmbeddingStep(),
    MemoryGraphEnqueueStep(),
    EpisodeEnqueueStep(),
]


def get_pipeline_steps(item_type: str) -> list[PipelineStep]:
    if item_type == "photo":
        return COMMON_STEPS + IMAGE_STEPS
    if item_type == "video":
        return COMMON_STEPS + VIDEO_STEPS
    if item_type == "audio":
        return COMMON_STEPS + AUDIO_STEPS
    return COMMON_STEPS + GENERIC_STEPS
