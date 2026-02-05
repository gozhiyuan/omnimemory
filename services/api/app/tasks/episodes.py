"""Episode merge tasks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import json
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert

from ..celery_app import celery_app
from ..ai import summarize_text_with_gemini
from ..ai.prompts import build_lifelog_episode_summary_prompt
from ..config import Settings, get_settings
from ..db.models import (
    DEFAULT_TEST_USER_ID,
    DailySummary,
    DerivedArtifact,
    ProcessedContent,
    ProcessedContext,
    SourceItem,
)
from ..db.session import isolated_session
from ..pipeline.utils import build_vector_text, ensure_tz_aware, extract_keywords, parse_iso_datetime
from ..user_settings import (
    build_preference_guidance,
    fetch_user_settings,
    resolve_language_code,
    resolve_language_label,
    resolve_timezone_offset_minutes,
)
from ..vectorstore import delete_context_embeddings, search_contexts, upsert_context_embeddings
from ..integrations.openclaw_sync import get_openclaw_sync


def _tokenize(value: str) -> set[str]:
    return set(word for word in value.lower().split() if word)


def _summary_signature(title: str, summary: str, keywords: Iterable[str]) -> set[str]:
    keyword_text = " ".join(str(value) for value in keywords if value)
    return _tokenize(f"{title} {summary} {keyword_text}")


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left.union(right)
    if not union:
        return 0.0
    return len(left.intersection(right)) / len(union)


def _coerce_event_time(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return ensure_tz_aware(value)


def _parse_client_offset(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate_text(value: str, limit: int) -> str:
    cleaned = (value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _parse_time_window(metadata: Optional[dict[str, Any]]) -> tuple[Optional[datetime], Optional[datetime]]:
    if not metadata:
        return None, None
    start_raw = metadata.get("event_time_window_start")
    end_raw = metadata.get("event_time_window_end")
    start_dt = parse_iso_datetime(start_raw) if isinstance(start_raw, str) else None
    end_dt = parse_iso_datetime(end_raw) if isinstance(end_raw, str) else None
    if start_dt:
        start_dt = ensure_tz_aware(start_dt)
    if end_dt:
        end_dt = ensure_tz_aware(end_dt)
    return start_dt, end_dt


def _item_time_bounds(
    item: SourceItem,
    metadata: Optional[dict[str, Any]],
    duration_sec: Optional[float],
) -> tuple[datetime, datetime]:
    base_time = _coerce_event_time(item.event_time_utc or item.captured_at or item.created_at)
    window_start, window_end = _parse_time_window(metadata)
    if window_start and window_end:
        if window_end >= window_start:
            return window_start, window_end
    if window_start and not window_end:
        return window_start, window_start
    if window_end and not window_start:
        return window_end, window_end
    if isinstance(duration_sec, (int, float)) and duration_sec > 0:
        return base_time, base_time + timedelta(seconds=float(duration_sec))
    return base_time, base_time


def _collect_episode_summary_items(
    contexts: list[ProcessedContext],
    items_by_id: dict[UUID, SourceItem],
) -> tuple[list[dict[str, Any]], int]:
    grouped: dict[UUID, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    keyword_map: dict[UUID, list[str]] = defaultdict(list)
    time_map: dict[UUID, datetime] = {}
    type_map: dict[UUID, str] = {}

    for context in contexts:
        if not context.source_item_ids:
            continue
        for source_id in context.source_item_ids:
            summary = _truncate_text(context.summary or "", 220)
            if summary:
                grouped[source_id][context.context_type].append(summary)
            if context.keywords:
                for word in context.keywords:
                    if word not in keyword_map[source_id]:
                        keyword_map[source_id].append(word)
            item = items_by_id.get(source_id)
            if item:
                time_value = item.event_time_utc or item.captured_at or item.created_at
                if time_value:
                    time_map[source_id] = ensure_tz_aware(time_value)
                type_map[source_id] = item.item_type
            elif context.event_time_utc:
                time_map[source_id] = ensure_tz_aware(context.event_time_utc)

    entries: list[tuple[datetime, dict[str, Any]]] = []
    for source_id, context_groups in grouped.items():
        time_value = time_map.get(source_id, datetime.now(timezone.utc))
        activity = " | ".join(context_groups.get("activity_context", [])[:3])
        social = " | ".join(context_groups.get("social_context", [])[:2])
        location = " | ".join(context_groups.get("location_context", [])[:2])
        knowledge = " | ".join(context_groups.get("knowledge_context", [])[:2])
        entry = {
            "item_id": str(source_id),
            "time": time_value.isoformat(),
            "item_type": type_map.get(source_id),
            "activity": activity,
            "social": social,
            "location": location,
            "knowledge": knowledge,
            "keywords": keyword_map.get(source_id, [])[:10],
        }
        entries.append((time_value, entry))

    entries.sort(key=lambda pair: pair[0])
    ordered = [entry for _, entry in entries]
    max_items = 80
    omitted = 0
    if len(ordered) > max_items:
        head = ordered[:40]
        tail = ordered[-40:]
        omitted = len(ordered) - len(head) - len(tail)
        ordered = head + tail
    return ordered, omitted


async def _generate_episode_summary(
    settings: Settings,
    *,
    items: list[dict[str, Any]],
    item_count: int,
    omitted_count: int,
    start_time: datetime,
    end_time: datetime,
    user_id: UUID,
    language: str | None = None,
    tz_name: str | None = None,
    preference_guidance: str | None = None,
) -> Optional[dict[str, Any]]:
    if settings.video_understanding_provider != "gemini":
        return None
    if not settings.gemini_api_key:
        return None
    local_tz = timezone.utc
    tz_label = "UTC"
    if tz_name:
        try:
            local_tz = ZoneInfo(tz_name)
            tz_label = tz_name
        except Exception:
            local_tz = timezone.utc
            tz_label = "UTC"
    time_range = f"{start_time.astimezone(local_tz).isoformat()} to {end_time.astimezone(local_tz).isoformat()} ({tz_label})"
    items_json = json.dumps(items, ensure_ascii=True)
    prompt = build_lifelog_episode_summary_prompt(
        items_json,
        item_count=item_count,
        time_range=time_range,
        omitted_count=omitted_count,
        language=language,
        extra_guidance=preference_guidance,
    )
    response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.video_understanding_model,
        temperature=settings.video_understanding_temperature,
        max_output_tokens=settings.vlm_max_output_tokens,
        timeout_seconds=settings.video_understanding_timeout_seconds,
        user_id=user_id,
        step_name="episode_summary",
    )
    if response.get("status") != "ok":
        return None
    parsed = response.get("parsed")
    if not isinstance(parsed, dict):
        return None
    title = parsed.get("title")
    summary = parsed.get("summary")
    keywords = parsed.get("keywords")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(title, str) or not title.strip():
        title = "Episode summary"
    if not isinstance(keywords, list):
        keywords = extract_keywords(summary)
    keywords = [str(word).strip().lower() for word in keywords if str(word).strip()]
    return {
        "title": _truncate_text(title, 120),
        "summary": _truncate_text(summary, 900),
        "keywords": keywords[:12],
        "provider": "gemini",
        "model": settings.video_understanding_model,
    }


def _primary_context(contexts: list[ProcessedContext]) -> ProcessedContext:
    for context in contexts:
        if context.context_type == "activity_context":
            return context
    return contexts[0]


def _choose_title(contexts: list[ProcessedContext]) -> str:
    if not contexts:
        return "Episode"
    titles = [context.title for context in contexts if context.title]
    if not titles:
        return "Episode"
    return max(titles, key=len)


def _choose_summary(contexts: list[ProcessedContext]) -> str:
    if not contexts:
        return ""
    summaries = [context.summary for context in contexts if context.summary]
    if not summaries:
        return ""
    return max(summaries, key=len)


def _merge_context_group(contexts: list[ProcessedContext]) -> dict[str, Any]:
    title = _choose_title(contexts)
    summary = _choose_summary(contexts)
    keywords: list[str] = []
    entities: list[Any] = []
    location: dict[str, Any] = {}
    for context in contexts:
        for word in context.keywords or []:
            if word not in keywords:
                keywords.append(word)
        if context.entities:
            for entity in context.entities:
                key = json.dumps(entity, sort_keys=True, default=str)
                if key not in {json.dumps(item, sort_keys=True, default=str) for item in entities}:
                    entities.append(entity)
        if not location and context.location:
            location = context.location
    return {
        "title": title,
        "summary": summary,
        "keywords": keywords,
        "entities": entities,
        "location": location,
    }


def _context_signature(context: ProcessedContext) -> set[str]:
    return _summary_signature(context.title, context.summary, context.keywords or [])


def _episode_similarity(item_context: ProcessedContext, episode_context: ProcessedContext) -> float:
    return _jaccard(_context_signature(item_context), _context_signature(episode_context))


def _episode_id_from_context(context: ProcessedContext) -> Optional[str]:
    versions = context.processor_versions or {}
    episode_id = versions.get("episode_id") if isinstance(versions, dict) else None
    if episode_id:
        return str(episode_id)
    return None


def _episode_query_filter(episode_id: str):
    return ProcessedContext.processor_versions["episode_id"].astext == episode_id


def _build_episode_context_records(
    *,
    episode_id: str,
    user_id: UUID,
    grouped_contexts: dict[str, list[ProcessedContext]],
    source_item_ids: list[UUID],
    start_time: datetime,
    end_time: datetime,
    existing_by_type: dict[str, ProcessedContext],
    episode_summary: Optional[dict[str, Any]] = None,
) -> list[ProcessedContext]:
    records: list[ProcessedContext] = []
    for context_type, contexts in grouped_contexts.items():
        merged_payload = _merge_context_group(contexts)
        existing = existing_by_type.get(context_type)
        edited = False
        processor_versions: dict[str, Any] = {"episode_id": episode_id, "episode_merge": "v1"}
        if existing and isinstance(existing.processor_versions, dict):
            edited = bool(existing.processor_versions.get("edited_by_user"))
            processor_versions.update(existing.processor_versions)
            processor_versions["episode_id"] = episode_id
            processor_versions["episode_merge"] = "v1"
        if edited and existing:
            merged_payload["title"] = existing.title
            merged_payload["summary"] = existing.summary
            merged_payload["keywords"] = existing.keywords or merged_payload["keywords"]
            merged_payload["entities"] = existing.entities or merged_payload["entities"]
        if (
            context_type == "activity_context"
            and episode_summary
            and not edited
        ):
            merged_payload["title"] = episode_summary.get("title") or merged_payload["title"]
            merged_payload["summary"] = episode_summary.get("summary") or merged_payload["summary"]
            merged_payload["keywords"] = episode_summary.get("keywords") or merged_payload["keywords"]
            processor_versions["episode_summary"] = "v1"
            processor_versions["episode_summary_provider"] = episode_summary.get("provider")
            processor_versions["episode_summary_model"] = episode_summary.get("model")
        record = existing or ProcessedContext(
            user_id=user_id,
            context_type=context_type,
            title=merged_payload["title"],
            summary=merged_payload["summary"],
            keywords=merged_payload["keywords"],
            entities=merged_payload["entities"],
            location=merged_payload["location"],
            event_time_utc=start_time,
            start_time_utc=start_time,
            end_time_utc=end_time,
            is_episode=True,
            source_item_ids=source_item_ids,
            merged_from_context_ids=[],
            vector_text="",
            processor_versions=processor_versions,
        )
        record.title = merged_payload["title"]
        record.summary = merged_payload["summary"]
        record.keywords = merged_payload["keywords"]
        record.entities = merged_payload["entities"]
        record.location = merged_payload["location"]
        record.event_time_utc = start_time
        record.start_time_utc = start_time
        record.end_time_utc = end_time
        record.is_episode = True
        record.source_item_ids = source_item_ids
        record.processor_versions = processor_versions
        record.vector_text = build_vector_text(
            merged_payload["title"],
            merged_payload["summary"],
            merged_payload["keywords"],
        )
        records.append(record)
    return records


def _daily_summary_title(summary_date: date) -> str:
    return f"Daily summary - {summary_date.isoformat()}"


def _build_daily_summary(episodes: list[ProcessedContext], summary_date: date) -> tuple[str, str, list[str]]:
    titles = []
    for episode in episodes:
        if episode.title and episode.context_type == "activity_context":
            titles.append(episode.title)
    highlights = "; ".join(titles[:6])
    if highlights:
        summary = f"Highlights: {highlights}."
    else:
        summary = "Summary unavailable."
    keywords = extract_keywords(" ".join(titles))
    return _daily_summary_title(summary_date), summary, keywords


def _summary_window(summary_date: date, tz_offset_minutes: Optional[int] = None) -> tuple[datetime, datetime]:
    offset = timedelta(minutes=tz_offset_minutes or 0)
    start = datetime.combine(summary_date, datetime.min.time(), tzinfo=timezone.utc) + offset
    end = start + timedelta(days=1)
    return start, end


async def _update_daily_summary(
    session,
    user_id: UUID,
    summary_date: date,
    tz_offset_minutes: Optional[int] = None,
    context_id: Optional[UUID] = None,
    force_regen: bool = False,
) -> None:
    start, end = _summary_window(summary_date, tz_offset_minutes=tz_offset_minutes)
    summary_contexts: list[ProcessedContext] = []
    summary_context: Optional[ProcessedContext] = None
    existing_summary_date: Optional[date] = None

    if context_id:
        context = await session.get(ProcessedContext, context_id)
        if (
            context
            and context.user_id == user_id
            and context.context_type == "daily_summary"
            and context.is_episode
        ):
            summary_contexts = [context]
            summary_context = context
    if summary_context is None:
        date_keys = {summary_date.isoformat()}
        if tz_offset_minutes is not None:
            utc_key = start.date().isoformat()
            date_keys.add(utc_key)
        summary_context_stmt = (
            select(ProcessedContext)
            .where(
                ProcessedContext.user_id == user_id,
                ProcessedContext.is_episode.is_(True),
                ProcessedContext.context_type == "daily_summary",
                ProcessedContext.processor_versions["daily_summary_date"].astext.in_(list(date_keys)),
            )
            .order_by(ProcessedContext.created_at.desc())
        )
        summary_context_rows = await session.execute(summary_context_stmt)
        summary_contexts = list(summary_context_rows.scalars().all())
        summary_context = summary_contexts[0] if summary_contexts else None
    episode_stmt = select(ProcessedContext).where(
        ProcessedContext.user_id == user_id,
        ProcessedContext.is_episode.is_(True),
        ProcessedContext.context_type == "activity_context",
        ProcessedContext.start_time_utc.is_not(None),
        ProcessedContext.start_time_utc >= start,
        ProcessedContext.start_time_utc < end,
    )
    episode_rows = await session.execute(episode_stmt)
    episodes = list(episode_rows.scalars().all())
    if summary_context:
        processor_versions = summary_context.processor_versions or {}
        if isinstance(processor_versions, dict):
            date_value = processor_versions.get("daily_summary_date")
            if date_value:
                try:
                    existing_summary_date = date.fromisoformat(date_value)
                except ValueError:
                    existing_summary_date = None
    if not episodes:
        if summary_contexts:
            for context in summary_contexts:
                await session.delete(context)
            await session.flush()
            try:
                delete_context_embeddings([str(context.id) for context in summary_contexts])
            except Exception as exc:  # pragma: no cover - external service dependency
                logger.warning("Daily summary embedding delete failed: {}", exc)
        delete_dates = {summary_date}
        if existing_summary_date:
            delete_dates.add(existing_summary_date)
        for target_date in delete_dates:
            await session.execute(
                delete(DailySummary).where(
                    DailySummary.user_id == user_id,
                    DailySummary.summary_date == target_date,
                )
            )
        # Sync deletion to OpenClaw if enabled
        try:
            user_settings = await fetch_user_settings(session, user_id)
            openclaw_sync = get_openclaw_sync(user_settings)
            for target_date in delete_dates:
                openclaw_sync.delete_daily_summary(target_date)
        except Exception as exc:
            logger.warning("OpenClaw sync delete failed: {}", exc)
        return
    summary_source_items: list[UUID] = []
    for episode in episodes:
        summary_source_items.extend(episode.source_item_ids or [])
    summary_source_items = list(dict.fromkeys(summary_source_items))[:200]
    if summary_context:
        processor_versions = summary_context.processor_versions or {}
        if isinstance(processor_versions, dict) and force_regen:
            processor_versions.pop("edited_by_user", None)
        if isinstance(processor_versions, dict) and processor_versions.get("edited_by_user"):
            summary_context.source_item_ids = summary_source_items
            summary_context.event_time_utc = start
            summary_context.start_time_utc = start
            summary_context.end_time_utc = end
            processor_versions["daily_summary_date"] = summary_date.isoformat()
            if tz_offset_minutes is not None:
                processor_versions["tz_offset_minutes"] = tz_offset_minutes
            summary_context.processor_versions = processor_versions
            await session.flush()

            summary_metadata = {
                "episode_count": len(episodes),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "edited_by_user": True,
            }
            if tz_offset_minutes is not None:
                summary_metadata["tz_offset_minutes"] = tz_offset_minutes
            daily_table = DailySummary.__table__
            daily_upsert = insert(daily_table).values(
                {
                    daily_table.c.user_id: user_id,
                    daily_table.c.summary_date: summary_date,
                    daily_table.c.summary: summary_context.summary,
                    daily_table.c.metadata: summary_metadata,
                }
            )
            daily_upsert = daily_upsert.on_conflict_do_update(
                index_elements=[daily_table.c.user_id, daily_table.c.summary_date],
                set_={
                    daily_table.c.summary: summary_context.summary,
                    daily_table.c.metadata: summary_metadata,
                },
            )
            await session.execute(daily_upsert)
            if existing_summary_date and existing_summary_date != summary_date:
                await session.execute(
                    delete(DailySummary).where(
                        DailySummary.user_id == user_id,
                        DailySummary.summary_date == existing_summary_date,
                    )
                )
            # Sync to OpenClaw if enabled (user-edited summary)
            try:
                user_settings = await fetch_user_settings(session, user_id)
                openclaw_sync = get_openclaw_sync(user_settings)
                if openclaw_sync.enabled:
                    episode_dicts = [
                        {
                            "title": ep.title,
                            "summary": ep.summary,
                            "start_time": ep.start_time_utc.isoformat() if ep.start_time_utc else None,
                            "end_time": ep.end_time_utc.isoformat() if ep.end_time_utc else None,
                        }
                        for ep in episodes
                    ]
                    highlights = [ep.title for ep in episodes if ep.title][:5]
                    openclaw_sync.sync_daily_summary(
                        user_id=str(user_id),
                        summary_date=summary_date,
                        summary=summary_context.summary,
                        episodes=episode_dicts,
                        highlights=highlights,
                    )
            except Exception as exc:
                logger.warning("OpenClaw sync failed: {}", exc)
            return

    title, summary, keywords = _build_daily_summary(episodes, summary_date)

    summary_metadata = {
        "episode_count": len(episodes),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if tz_offset_minutes is not None:
        summary_metadata["tz_offset_minutes"] = tz_offset_minutes
    daily_table = DailySummary.__table__
    daily_upsert = insert(daily_table).values(
        {
            daily_table.c.user_id: user_id,
            daily_table.c.summary_date: summary_date,
            daily_table.c.summary: summary,
            daily_table.c.metadata: summary_metadata,
        }
    )
    daily_upsert = daily_upsert.on_conflict_do_update(
        index_elements=[daily_table.c.user_id, daily_table.c.summary_date],
        set_={
            daily_table.c.summary: summary,
            daily_table.c.metadata: summary_metadata,
        },
    )
    await session.execute(daily_upsert)
    if existing_summary_date and existing_summary_date != summary_date:
        await session.execute(
            delete(DailySummary).where(
                DailySummary.user_id == user_id,
                DailySummary.summary_date == existing_summary_date,
            )
        )
    processor_versions = {
        "daily_summary": "v1",
        "daily_summary_date": summary_date.isoformat(),
    }
    if tz_offset_minutes is not None:
        processor_versions["tz_offset_minutes"] = tz_offset_minutes
    if summary_context is None:
        summary_context = ProcessedContext(
            user_id=user_id,
            context_type="daily_summary",
            title=title,
            summary=summary,
            keywords=keywords,
            entities=[],
            location={},
            event_time_utc=start,
            start_time_utc=start,
            end_time_utc=end,
            is_episode=True,
            source_item_ids=summary_source_items,
            merged_from_context_ids=[],
            vector_text="",
            processor_versions=processor_versions,
        )
        session.add(summary_context)
    summary_context.title = title
    summary_context.summary = summary
    summary_context.keywords = keywords
    summary_context.source_item_ids = summary_source_items
    summary_context.event_time_utc = start
    summary_context.start_time_utc = start
    summary_context.end_time_utc = end
    summary_context.processor_versions = processor_versions
    summary_context.vector_text = build_vector_text(title, summary, keywords)

    await session.flush()
    upsert_context_embeddings([summary_context])

    # Sync to OpenClaw if enabled
    try:
        user_settings = await fetch_user_settings(session, user_id)
        openclaw_sync = get_openclaw_sync(user_settings)
        if openclaw_sync.enabled:
            episode_dicts = [
                {
                    "title": ep.title,
                    "summary": ep.summary,
                    "start_time": ep.start_time_utc.isoformat() if ep.start_time_utc else None,
                    "end_time": ep.end_time_utc.isoformat() if ep.end_time_utc else None,
                }
                for ep in episodes
            ]
            highlights = [ep.title for ep in episodes if ep.title][:5]
            openclaw_sync.sync_daily_summary(
                user_id=str(user_id),
                summary_date=summary_date,
                summary=summary,
                episodes=episode_dicts,
                highlights=highlights,
            )
    except Exception as exc:
        logger.warning("OpenClaw sync failed: {}", exc)


async def _update_episode_for_item(item_id: str) -> dict[str, Any]:
    settings = get_settings()
    async with isolated_session() as session:
        item = await session.get(SourceItem, UUID(item_id))
        if not item:
            return {"status": "missing_item"}
        if item.processing_status != "completed":
            return {"status": "not_ready"}

        user_settings = await fetch_user_settings(session, item.user_id)
        language = resolve_language_label(resolve_language_code(user_settings))
        tz_name = None
        if isinstance(user_settings, dict):
            preferences = user_settings.get("preferences")
            if isinstance(preferences, dict):
                tz_name = preferences.get("timezone")
        preference_guidance = build_preference_guidance(user_settings)

        context_stmt = select(ProcessedContext).where(
            ProcessedContext.user_id == item.user_id,
            ProcessedContext.is_episode.is_(False),
            ProcessedContext.source_item_ids.contains([item.id]),
        )
        context_rows = await session.execute(context_stmt)
        item_contexts = list(context_rows.scalars().all())
        if not item_contexts:
            return {"status": "no_contexts"}

        primary = _primary_context(item_contexts)
        event_time = _coerce_event_time(item.event_time_utc or item.captured_at or item.created_at)
        metadata_offset: Optional[int] = None
        metadata_stmt = select(ProcessedContent.data).where(
            ProcessedContent.item_id == item.id,
            ProcessedContent.content_role == "metadata",
        )
        metadata_row = await session.execute(metadata_stmt)
        metadata = metadata_row.scalar_one_or_none()
        if isinstance(metadata, dict):
            metadata_offset = _parse_client_offset(metadata.get("client_tz_offset_minutes"))
        settings_offset = resolve_timezone_offset_minutes(user_settings, at=event_time)

        episode_id: Optional[str] = None
        episode_contexts: list[ProcessedContext] = []
        existing_by_type: dict[str, ProcessedContext] = {}

        if settings.episode_merge_enabled:
            candidates = search_contexts(
                primary.vector_text or primary.summary or primary.title,
                limit=6,
                user_id=str(item.user_id),
                is_episode=True,
                context_type="activity_context",
            )
            best_candidate: Optional[ProcessedContext] = None
            best_score = 0.0
            max_gap = timedelta(minutes=settings.episode_merge_max_gap_minutes)
            if candidates:
                candidate_ids: list[UUID] = []
                candidate_scores: dict[str, float] = {}
                for result in candidates:
                    context_id = result.get("context_id")
                    try:
                        candidate_ids.append(UUID(context_id))
                        candidate_scores[str(context_id)] = float(result.get("score") or 0.0)
                    except Exception:
                        continue
                if candidate_ids:
                    candidate_stmt = select(ProcessedContext).where(
                        ProcessedContext.id.in_(candidate_ids),
                        ProcessedContext.is_episode.is_(True),
                    )
                    candidate_rows = await session.execute(candidate_stmt)
                    for candidate in candidate_rows.scalars().all():
                        end_time = candidate.end_time_utc or candidate.event_time_utc
                        if end_time is None:
                            continue
                        end_time = ensure_tz_aware(end_time)
                        gap = event_time - end_time
                        if gap < -max_gap or gap > max_gap:
                            continue
                        score = candidate_scores.get(str(candidate.id))
                        if score is None:
                            score = _episode_similarity(primary, candidate)
                        if score >= settings.episode_merge_similarity_threshold and score >= best_score:
                            best_candidate = candidate
                            best_score = score

            if best_candidate:
                episode_id = _episode_id_from_context(best_candidate) or str(best_candidate.id)
                episode_stmt = select(ProcessedContext).where(
                    ProcessedContext.user_id == item.user_id,
                    ProcessedContext.is_episode.is_(True),
                    _episode_query_filter(episode_id),
                )
                episode_rows = await session.execute(episode_stmt)
                episode_contexts = list(episode_rows.scalars().all())
                if episode_contexts:
                    existing_by_type = {context.context_type: context for context in episode_contexts}

        if item.device_id and settings.device_episode_merge_window_minutes > 0:
            window = timedelta(minutes=settings.device_episode_merge_window_minutes)
            window_start = event_time - window
            window_end = event_time + window
            time_stmt = select(ProcessedContext).where(
                ProcessedContext.user_id == item.user_id,
                ProcessedContext.is_episode.is_(True),
                ProcessedContext.context_type == "activity_context",
                ProcessedContext.start_time_utc <= window_end,
                ProcessedContext.end_time_utc >= window_start,
            ).order_by(ProcessedContext.start_time_utc.asc())
            time_rows = await session.execute(time_stmt)
            time_candidates = list(time_rows.scalars().all())
            if time_candidates:
                candidate_source_ids: list[UUID] = []
                for candidate in time_candidates:
                    if candidate.source_item_ids:
                        candidate_source_ids.extend(candidate.source_item_ids)
                source_device_map: dict[UUID, Optional[UUID]] = {}
                if candidate_source_ids:
                    source_stmt = select(SourceItem.id, SourceItem.device_id).where(
                        SourceItem.id.in_(candidate_source_ids)
                    )
                    source_rows = await session.execute(source_stmt)
                    source_device_map = {item_id: device_id for item_id, device_id in source_rows.fetchall()}

                best_time_candidate: Optional[ProcessedContext] = None
                best_gap: Optional[timedelta] = None
                for candidate in time_candidates:
                    if not candidate.source_item_ids:
                        continue
                    if not any(
                        source_device_map.get(source_id) == item.device_id
                        for source_id in candidate.source_item_ids
                    ):
                        continue
                    start_bound = ensure_tz_aware(candidate.start_time_utc or candidate.event_time_utc)
                    end_bound = ensure_tz_aware(candidate.end_time_utc or candidate.event_time_utc)
                    if event_time < start_bound:
                        gap = start_bound - event_time
                    elif event_time > end_bound:
                        gap = event_time - end_bound
                    else:
                        gap = timedelta(0)
                    if best_gap is None or gap < best_gap:
                        best_gap = gap
                        best_time_candidate = candidate

                if best_time_candidate:
                    episode_id = _episode_id_from_context(best_time_candidate) or str(best_time_candidate.id)
                    episode_stmt = select(ProcessedContext).where(
                        ProcessedContext.user_id == item.user_id,
                        ProcessedContext.is_episode.is_(True),
                        _episode_query_filter(episode_id),
                    )
                    episode_rows = await session.execute(episode_stmt)
                    episode_contexts = list(episode_rows.scalars().all())
                    if episode_contexts:
                        existing_by_type = {context.context_type: context for context in episode_contexts}

        if episode_id is None:
            episode_id = str(uuid4())

        source_item_ids: list[UUID] = []
        start_time = event_time
        end_time = event_time
        if episode_contexts:
            for context in episode_contexts:
                if context.source_item_ids:
                    source_item_ids.extend(context.source_item_ids)
                if context.start_time_utc:
                    start_time = min(start_time, ensure_tz_aware(context.start_time_utc))
                if context.end_time_utc:
                    end_time = max(end_time, ensure_tz_aware(context.end_time_utc))
        source_item_ids.append(item.id)
        source_item_ids = list(dict.fromkeys(source_item_ids))

        episode_items: list[SourceItem] = []
        if source_item_ids:
            item_stmt = select(SourceItem).where(SourceItem.id.in_(source_item_ids))
            item_rows = await session.execute(item_stmt)
            episode_items = list(item_rows.scalars().all())

        metadata_by_item: dict[UUID, dict[str, Any]] = {}
        if source_item_ids:
            metadata_stmt = select(ProcessedContent.item_id, ProcessedContent.data).where(
                ProcessedContent.item_id.in_(source_item_ids),
                ProcessedContent.content_role == "metadata",
            )
            metadata_rows = await session.execute(metadata_stmt)
            for item_id_value, data in metadata_rows.fetchall():
                if isinstance(data, dict):
                    metadata_by_item[item_id_value] = data

        duration_by_item: dict[UUID, float] = {}
        if source_item_ids:
            duration_stmt = (
                select(DerivedArtifact.source_item_id, DerivedArtifact.payload, DerivedArtifact.created_at)
                .where(
                    DerivedArtifact.source_item_id.in_(source_item_ids),
                    DerivedArtifact.artifact_type == "media_metadata",
                )
                .order_by(DerivedArtifact.created_at.desc())
            )
            duration_rows = await session.execute(duration_stmt)
            for item_id_value, payload, _ in duration_rows.fetchall():
                if item_id_value in duration_by_item:
                    continue
                if not isinstance(payload, dict):
                    continue
                duration = payload.get("duration_sec")
                if isinstance(duration, (int, float)) and duration > 0:
                    duration_by_item[item_id_value] = float(duration)

        for episode_item in episode_items:
            metadata = metadata_by_item.get(episode_item.id)
            duration = duration_by_item.get(episode_item.id)
            if duration is None and metadata:
                meta_duration = metadata.get("duration_sec")
                if isinstance(meta_duration, (int, float)) and meta_duration > 0:
                    duration = float(meta_duration)
            item_start, item_end = _item_time_bounds(episode_item, metadata, duration)
            start_time = min(start_time, item_start)
            end_time = max(end_time, item_end)

        grouped: dict[str, list[ProcessedContext]] = defaultdict(list)
        for context in item_contexts:
            grouped[context.context_type].append(context)
        for context_type, existing in existing_by_type.items():
            if context_type in grouped:
                grouped[context_type].append(existing)
            else:
                grouped[context_type] = [existing]

        episode_summary: Optional[dict[str, Any]] = None
        activity_context = existing_by_type.get("activity_context")
        activity_versions = activity_context.processor_versions if activity_context else {}
        activity_edited = bool(activity_versions.get("edited_by_user")) if isinstance(activity_versions, dict) else False
        if not activity_edited:
            all_context_stmt = select(ProcessedContext).where(
                ProcessedContext.user_id == item.user_id,
                ProcessedContext.is_episode.is_(False),
                ProcessedContext.source_item_ids.overlap(source_item_ids),
            )
            all_context_rows = await session.execute(all_context_stmt)
            all_item_contexts = list(all_context_rows.scalars().all())
            items_by_id = {episode_item.id: episode_item for episode_item in episode_items}
            items_payload, omitted_count = _collect_episode_summary_items(all_item_contexts, items_by_id)
            if items_payload:
                episode_summary = await _generate_episode_summary(
                    settings,
                    items=items_payload,
                    item_count=len(source_item_ids),
                    omitted_count=omitted_count,
                    start_time=start_time,
                    end_time=end_time,
                    user_id=item.user_id,
                    language=language,
                    tz_name=tz_name,
                    preference_guidance=preference_guidance,
                )

        episode_records = _build_episode_context_records(
            episode_id=episode_id,
            user_id=item.user_id,
            grouped_contexts=grouped,
            source_item_ids=source_item_ids,
            start_time=start_time,
            end_time=end_time,
            existing_by_type=existing_by_type,
            episode_summary=episode_summary,
        )

        for record in episode_records:
            session.add(record)
        await session.flush()
        upsert_context_embeddings(episode_records)
        summary_date = start_time.date()
        summary_date_locked = False
        summary_context: Optional[ProcessedContext] = None
        summary_tz_offset: Optional[int] = None
        summary_context_stmt = select(ProcessedContext).where(
            ProcessedContext.user_id == item.user_id,
            ProcessedContext.is_episode.is_(True),
            ProcessedContext.context_type == "daily_summary",
            ProcessedContext.start_time_utc.is_not(None),
            ProcessedContext.end_time_utc.is_not(None),
            ProcessedContext.start_time_utc <= start_time,
            ProcessedContext.end_time_utc > start_time,
        ).order_by(ProcessedContext.created_at.desc())
        summary_rows = await session.execute(summary_context_stmt)
        summary_context = summary_rows.scalars().first()
        if summary_context:
            versions = summary_context.processor_versions or {}
            if isinstance(versions, dict):
                date_value = versions.get("daily_summary_date")
                if date_value:
                    try:
                        summary_date = date.fromisoformat(date_value)
                        summary_date_locked = True
                    except ValueError:
                        summary_date = start_time.date()
                offset_value = versions.get("tz_offset_minutes")
                if offset_value is not None:
                    try:
                        summary_tz_offset = int(offset_value)
                    except (TypeError, ValueError):
                        summary_tz_offset = None
        if summary_tz_offset is None:
            summary_tz_offset = metadata_offset or settings_offset
        if summary_tz_offset is not None and not summary_date_locked:
            summary_date = (start_time - timedelta(minutes=summary_tz_offset)).date()

        await _update_daily_summary(
            session,
            item.user_id,
            summary_date,
            tz_offset_minutes=summary_tz_offset,
            context_id=summary_context.id if summary_context else None,
        )
        await session.commit()

    return {
        "status": "updated",
        "item_id": item_id,
        "episode_id": episode_id,
        "contexts": len(episode_records),
    }


@celery_app.task(name="episodes.update_for_item")
def update_episode_for_item(item_id: str) -> dict[str, Any]:
    try:
        return asyncio.run(_update_episode_for_item(item_id))
    except Exception as exc:  # pragma: no cover - background task robustness
        logger.exception("Episode merge failed for item {}: {}", item_id, exc)
        raise


@celery_app.task(name="episodes.update_daily_summary")
def update_daily_summary(
    user_id: str | None,
    summary_date: str,
    tz_offset_minutes: int | None = None,
) -> dict[str, Any]:
    try:
        summary_day = date.fromisoformat(summary_date)
    except ValueError:
        logger.warning("Invalid summary date provided: {}", summary_date)
        return {"status": "invalid_date", "summary_date": summary_date}
    resolved_user = UUID(user_id) if user_id else DEFAULT_TEST_USER_ID

    async def _run() -> None:
        async with isolated_session() as session:
            await _update_daily_summary(
                session,
                resolved_user,
                summary_day,
                tz_offset_minutes=tz_offset_minutes,
            )
            await session.commit()

    try:
        asyncio.run(_run())
    except Exception as exc:  # pragma: no cover - background task robustness
        logger.exception("Daily summary update failed for {}: {}", summary_date, exc)
        raise
    return {"status": "updated", "summary_date": summary_date}


async def _backfill_episodes(
    *,
    user_id: UUID,
    limit: int,
    offset: int,
    item_type: Optional[str],
    provider: Optional[str],
    processing_statuses: Optional[Iterable[str]],
    since: Optional[datetime],
    until: Optional[datetime],
    only_missing: bool,
) -> dict[str, Any]:
    async with isolated_session() as session:
        stmt = select(SourceItem).where(SourceItem.user_id == user_id)
        if processing_statuses:
            stmt = stmt.where(SourceItem.processing_status.in_(list(processing_statuses)))
        if item_type:
            stmt = stmt.where(SourceItem.item_type == item_type)
        if provider:
            stmt = stmt.where(SourceItem.provider == provider)
        if since:
            stmt = stmt.where(SourceItem.created_at >= since)
        if until:
            stmt = stmt.where(SourceItem.created_at <= until)
        if only_missing:
            subq = select(ProcessedContext.id).where(
                ProcessedContext.user_id == SourceItem.user_id,
                ProcessedContext.is_episode.is_(True),
                ProcessedContext.source_item_ids.any(SourceItem.id),
            )
            stmt = stmt.where(~exists(subq))

        stmt = stmt.order_by(SourceItem.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = list(result.scalars().all())

    enqueued = 0
    for item in items:
        celery_app.send_task("episodes.update_for_item", args=[str(item.id)])
        enqueued += 1

    logger.info(
        "Episode backfill enqueued user={} count={} limit={} offset={} missing_only={}",
        user_id,
        enqueued,
        limit,
        offset,
        only_missing,
    )
    return {
        "status": "enqueued",
        "user_id": str(user_id),
        "count": enqueued,
        "limit": limit,
        "offset": offset,
        "missing_only": only_missing,
    }


@celery_app.task(name="episodes.backfill")
def backfill_episodes(
    user_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    item_type: Optional[str] = None,
    provider: Optional[str] = None,
    processing_statuses: Optional[list[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    only_missing: bool = True,
) -> dict[str, Any]:
    resolved_user = UUID(user_id) if user_id else DEFAULT_TEST_USER_ID
    since_dt = parse_iso_datetime(since) if since else None
    until_dt = parse_iso_datetime(until) if until else None
    return asyncio.run(
        _backfill_episodes(
            user_id=resolved_user,
            limit=limit,
            offset=offset,
            item_type=item_type,
            provider=provider,
            processing_statuses=processing_statuses or ["completed"],
            since=since_dt,
            until=until_dt,
            only_missing=only_missing,
        )
    )
