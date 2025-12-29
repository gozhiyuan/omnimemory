"""Pipeline steps for ingestion."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import delete, select

from ..ai import analyze_image_with_vlm, run_ocr
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
        metadata = artifacts.get("metadata", {})
        exif_time = parse_iso_datetime(metadata.get("exif_datetime"))

        event_time: Optional[datetime] = None
        source = None
        confidence = None
        if exif_time:
            event_time = exif_time
            source = "exif"
            confidence = 0.9
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
            return

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
            payload = {
                "status": "exact_duplicate",
                "canonical_item_id": str(duplicate.id),
                "content_hash": content_hash,
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
            artifacts.skip_expensive = True
            return

        if item.item_type != "photo" or not item.phash or not item.event_time_utc:
            return

        window = timedelta(minutes=10)
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
            if distance <= 5:
                payload = {
                    "status": "near_duplicate",
                    "canonical_item_id": str(candidate.id),
                    "phash": item.phash,
                    "distance": distance,
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
                artifacts.skip_expensive = True
                break


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
        if not contexts:
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
                location=context.get("location") or {},
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
    version = "placeholder_v1"
    is_expensive = True

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        context_ids = artifacts.get("context_ids") or []
        if not context_ids:
            return
        fingerprint = hash_parts([self.version, ",".join(context_ids)])
        existing = await artifacts.store.get("embeddings", self.name, self.version, fingerprint)
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
            producer_version=self.version,
            input_fingerprint=fingerprint,
            payload={"context_ids": context_ids},
        )


COMMON_STEPS: list[PipelineStep] = [
    FetchBlobStep(),
    ContentHashStep(),
    MetadataStep(),
    PerceptualHashStep(),
    EventTimeStep(),
    DedupStep(),
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
