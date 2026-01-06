"""Pipeline runner orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import SourceItem
from ..storage import get_storage_provider
from ..user_settings import fetch_user_settings
from .steps import get_pipeline_steps
from .types import ArtifactStore, PipelineArtifacts, PipelineConfig


def _shorten(value: Any, limit: int = 120) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    return value


def _step_details(step_name: str, item: SourceItem, artifacts: PipelineArtifacts) -> Dict[str, Any] | None:
    if step_name == "fetch_blob":
        blob = artifacts.get("blob")
        return {"blob_bytes": len(blob) if isinstance(blob, (bytes, bytearray)) else 0}
    if step_name == "content_hash":
        content_hash = artifacts.get("content_hash")
        return {"content_hash": _shorten(content_hash)}
    if step_name == "metadata":
        metadata = artifacts.get("metadata") or {}
        provider_location = metadata.get("provider_location") if isinstance(metadata, dict) else None
        location = None
        if isinstance(provider_location, dict):
            location = {
                "lat": provider_location.get("latitude"),
                "lng": provider_location.get("longitude"),
                "name": _shorten(provider_location.get("name")),
                "source": provider_location.get("source"),
            }
        return {
            "content_type": metadata.get("content_type"),
            "captured_at": metadata.get("captured_at"),
            "original_filename": _shorten(metadata.get("original_filename")),
            "provider_location": location,
        }
    if step_name == "media_metadata":
        metadata = artifacts.get("media_metadata") or {}
        return {
            "duration_sec": metadata.get("duration_sec"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "fps": metadata.get("fps"),
            "captured_at": metadata.get("captured_at"),
        }
    if step_name == "exif":
        exif = artifacts.get("exif") or {}
        gps = exif.get("gps") or {}
        return {
            "datetime_original": exif.get("datetime_original"),
            "tz_offset_minutes": exif.get("timezone_offset_minutes"),
            "event_time_utc": exif.get("event_time_utc"),
            "gps_lat": gps.get("latitude"),
            "gps_lng": gps.get("longitude"),
        }
    if step_name == "preview":
        preview = artifacts.get("preview")
        if isinstance(preview, dict):
            return {
                "status": preview.get("status"),
                "storage_key": preview.get("storage_key"),
                "width": preview.get("width"),
                "height": preview.get("height"),
            }
        return {"status": "skipped"}
    if step_name == "phash":
        return {"phash": artifacts.get("phash")}
    if step_name == "event_time":
        return {
            "event_time_utc": item.event_time_utc.isoformat() if item.event_time_utc else None,
            "event_time_source": item.event_time_source,
            "event_time_confidence": item.event_time_confidence,
        }
    if step_name == "dedupe":
        dedupe = artifacts.get("dedupe") or {}
        return {
            "status": dedupe.get("status"),
            "canonical_item_id": dedupe.get("canonical_item_id"),
            "reprocess": dedupe.get("reprocess"),
        }
    if step_name == "geocode":
        geo = artifacts.get("geo_location")
        if isinstance(geo, dict):
            return {
                "status": geo.get("status"),
                "source": geo.get("source"),
                "formatted_address": _shorten(geo.get("formatted_address")),
                "lat": geo.get("lat"),
                "lng": geo.get("lng"),
            }
        return {"status": "skipped", "reason": "missing_coordinates"}
    if step_name == "caption":
        caption = artifacts.get("caption") or ""
        return {"caption_chars": len(caption)}
    if step_name == "ocr":
        ocr_text = artifacts.get("ocr_text") or ""
        return {"ocr_chars": len(ocr_text)}
    if step_name == "transcription":
        transcript = artifacts.get("transcript_text") or ""
        return {"transcript_chars": len(transcript)}
    if step_name == "media_chunk_understanding":
        contexts = artifacts.get("contexts") or []
        transcript = artifacts.get("transcript_text") or ""
        return {"contexts": len(contexts), "transcript_chars": len(transcript)}
    if step_name == "keyframes":
        frames = artifacts.get("keyframes") or []
        return {"frames": len(frames)}
    if step_name == "vlm":
        contexts = artifacts.get("contexts") or []
        context_types: list[str] = []
        if isinstance(contexts, list):
            for entry in contexts:
                if isinstance(entry, dict):
                    context_type = entry.get("context_type")
                    if context_type and context_type not in context_types:
                        context_types.append(context_type)
        return {"contexts": len(contexts), "context_types": context_types[:5]}
    if step_name == "transcript_context":
        contexts = artifacts.get("contexts") or []
        return {"contexts": len(contexts)}
    if step_name == "media_summary":
        contexts = artifacts.get("contexts") or []
        return {"contexts": len(contexts)}
    if step_name == "contexts":
        context_ids = artifacts.get("context_ids") or []
        return {"context_ids": len(context_ids)}
    if step_name == "embeddings":
        context_ids = artifacts.get("context_ids") or []
        return {"embedded_contexts": len(context_ids)}
    return None


async def run_pipeline(
    session: AsyncSession,
    item: SourceItem,
    payload: Dict[str, Any],
) -> PipelineArtifacts:
    settings = get_settings()
    storage = get_storage_provider()
    user_settings = await fetch_user_settings(session, item.user_id)
    config = PipelineConfig(
        session=session,
        storage=storage,
        settings=settings,
        payload=payload,
        now=datetime.now(timezone.utc),
        user_settings=user_settings,
    )
    artifacts = PipelineArtifacts(ArtifactStore(session, item))
    steps = get_pipeline_steps(item.item_type)
    logger.info("Pipeline start item={} steps={}", item.id, [step.name for step in steps])
    for step in steps:
        if artifacts.skip_expensive and step.is_expensive:
            logger.info("Pipeline step skipped item={} step={} reason=dedupe", item.id, step.name)
            continue
        started = perf_counter()
        logger.info("Pipeline step start item={} step={} version={}", item.id, step.name, step.version)
        skip_before = artifacts.skip_expensive
        try:
            await step.run(item, artifacts, config)
        except Exception:
            elapsed_ms = int((perf_counter() - started) * 1000)
            logger.exception("Pipeline step failed item={} step={} duration_ms={}", item.id, step.name, elapsed_ms)
            raise
        if not skip_before and artifacts.skip_expensive:
            logger.info("Pipeline skip_expensive enabled item={} step={}", item.id, step.name)
        elapsed_ms = int((perf_counter() - started) * 1000)
        logger.info("Pipeline step done item={} step={} duration_ms={}", item.id, step.name, elapsed_ms)
        if settings.pipeline_log_details:
            details = _step_details(step.name, item, artifacts)
            if details is not None:
                logger.info("Pipeline step detail item={} step={} detail={}", item.id, step.name, details)
        await session.commit()
    return artifacts
