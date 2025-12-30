"""Pipeline steps for ingestion."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, Optional
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import delete, select

from ..ai import analyze_image_with_vlm, reverse_geocode, run_ocr
from ..ai.prompts import build_lifelog_image_prompt
from ..db.models import DataConnection, ProcessedContent, ProcessedContext, SourceItem
from ..google_photos import get_valid_access_token
from ..vectorstore import upsert_context_embeddings
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
        if item.item_type != "photo":
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
        exif_payload = artifacts.get("exif") or {}
        exif_time = parse_iso_datetime(exif_payload.get("event_time_utc"))
        if not exif_time:
            exif_time = parse_exif_datetime(exif_payload.get("datetime_original"))

        event_time: Optional[datetime] = None
        source = None
        confidence = None
        if exif_time:
            event_time = exif_time
            source = "exif"
            confidence = 0.9 if exif_payload.get("event_time_utc") else 0.75
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
        fingerprint = hash_parts([content_hash, provider, self.version])
        existing = await artifacts.store.get("ocr", provider, self.version, fingerprint)
        if existing:
            artifacts.set("ocr_text", (existing.payload or {}).get("text", ""))
            return

        blob = artifacts.get("blob") or b""
        try:
            payload = await run_ocr(blob, config.settings, item.content_type)
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
    version = "lifelog_image_analysis_v1"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        if item.item_type != "photo":
            return
        content_hash = artifacts.get("content_hash")
        ocr_text = artifacts.get("ocr_text", "")
        provider = config.settings.vlm_provider
        model = config.settings.gemini_model
        fingerprint = hash_parts([content_hash, ocr_text, provider, model, self.version])
        existing = await artifacts.store.get("vlm_observations", provider, model, fingerprint)
        if existing:
            contexts = (existing.payload or {}).get("contexts", [])
            artifacts.set("contexts", contexts)
            return

        blob = artifacts.get("blob") or b""
        prompt = build_lifelog_image_prompt(ocr_text)
        try:
            response = await analyze_image_with_vlm(blob, prompt, config.settings, item.content_type)
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
        context_signature = hash_parts(
            [json.dumps(contexts, sort_keys=True), artifacts.get("content_hash")]
        )
        existing = await artifacts.store.get("contexts", self.name, self.version, context_signature)
        if existing:
            context_ids = (existing.payload or {}).get("context_ids", [])
            artifacts.set("context_ids", context_ids)
            return

        await config.session.execute(
            delete(ProcessedContext).where(
                ProcessedContext.user_id == item.user_id,
                ProcessedContext.is_episode.is_(False),
                ProcessedContext.source_item_ids.contains([item.id]),
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


COMMON_STEPS: list[PipelineStep] = [
    FetchBlobStep(),
    ContentHashStep(),
    MetadataStep(),
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
    ContextPersistStep(),
    EmbeddingStep(),
]

GENERIC_STEPS: list[PipelineStep] = [
    GenericContextStep(),
    ContextPersistStep(),
    EmbeddingStep(),
]


def get_pipeline_steps(item_type: str) -> list[PipelineStep]:
    if item_type == "photo":
        return COMMON_STEPS + IMAGE_STEPS
    return COMMON_STEPS + GENERIC_STEPS
