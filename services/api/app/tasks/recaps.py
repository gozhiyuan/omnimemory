"""Weekly recap tasks."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import select

from ..celery_app import celery_app
from ..db.models import ProcessedContext, UserSettings
from ..db.session import isolated_session
from ..pipeline.utils import build_vector_text, extract_keywords
from ..recaps import resolve_week_window
from ..vectorstore import delete_context_embeddings, upsert_context_embeddings


def _weekly_summary_title(start_date: date, end_date: date) -> str:
    if start_date == end_date:
        return f"Weekly recap - {start_date.isoformat()}"
    return f"Weekly recap - {start_date.isoformat()} to {end_date.isoformat()}"


def _build_weekly_summary(episodes: list[ProcessedContext], start_date: date, end_date: date) -> tuple[str, str, list[str]]:
    titles = []
    for episode in episodes:
        if episode.title and episode.context_type == "activity_context":
            titles.append(episode.title)
    highlights = "; ".join(titles[:8])
    if highlights:
        summary = f"Highlights: {highlights}."
    else:
        summary = "Summary unavailable."
    keywords = extract_keywords(" ".join(titles))
    return _weekly_summary_title(start_date, end_date), summary, keywords


async def _generate_weekly_recap(
    user_id: UUID,
    *,
    tz_name: Optional[str],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict[str, Any]:
    window = resolve_week_window(tz_name=tz_name, start_date=start_date, end_date=end_date)
    async with isolated_session() as session:
        summary_stmt = select(ProcessedContext).where(
            ProcessedContext.user_id == user_id,
            ProcessedContext.is_episode.is_(True),
            ProcessedContext.context_type == "weekly_summary",
            ProcessedContext.processor_versions["weekly_summary_start"].astext == window.start_date.isoformat(),
        )
        summary_rows = await session.execute(summary_stmt)
        summary_contexts = list(summary_rows.scalars().all())
        summary_context = summary_contexts[0] if summary_contexts else None

        episode_stmt = select(ProcessedContext).where(
            ProcessedContext.user_id == user_id,
            ProcessedContext.is_episode.is_(True),
            ProcessedContext.context_type == "activity_context",
            ProcessedContext.start_time_utc.is_not(None),
            ProcessedContext.start_time_utc >= window.start_utc,
            ProcessedContext.start_time_utc < window.end_utc,
        )
        episode_rows = await session.execute(episode_stmt)
        episodes = list(episode_rows.scalars().all())

        if not episodes:
            if summary_contexts:
                for context in summary_contexts:
                    await session.delete(context)
                await session.flush()
                try:
                    delete_context_embeddings([str(context.id) for context in summary_contexts])
                except Exception as exc:  # pragma: no cover - external dependency
                    logger.warning("Weekly recap embedding delete failed: {}", exc)
            await session.commit()
            return {
                "status": "skipped",
                "start_date": window.start_date.isoformat(),
                "end_date": window.end_date.isoformat(),
                "reason": "no_episodes",
            }

        summary_source_items: list[UUID] = []
        for episode in episodes:
            summary_source_items.extend(episode.source_item_ids or [])
        summary_source_items = list(dict.fromkeys(summary_source_items))[:300]

        title, summary, keywords = _build_weekly_summary(episodes, window.start_date, window.end_date)
        processor_versions = {
            "weekly_summary_start": window.start_date.isoformat(),
            "weekly_summary_end": window.end_date.isoformat(),
            "weekly_summary_timezone": window.timezone,
        }

        if summary_context is None:
            summary_context = ProcessedContext(
                user_id=user_id,
                context_type="weekly_summary",
                title=title,
                summary=summary,
                keywords=keywords,
                entities=[],
                location={},
                event_time_utc=window.start_utc,
                start_time_utc=window.start_utc,
                end_time_utc=window.end_utc,
                is_episode=True,
                source_item_ids=summary_source_items,
                merged_from_context_ids=[],
                vector_text=build_vector_text(
                    title, summary, keywords, context_type="weekly_summary"
                ),
                processor_versions=processor_versions,
            )
            session.add(summary_context)
        else:
            summary_context.title = title
            summary_context.summary = summary
            summary_context.keywords = keywords
            summary_context.source_item_ids = summary_source_items
            summary_context.event_time_utc = window.start_utc
            summary_context.start_time_utc = window.start_utc
            summary_context.end_time_utc = window.end_utc
            summary_context.vector_text = build_vector_text(
                title, summary, keywords, context_type=summary_context.context_type
            )
            summary_context.processor_versions = processor_versions

        await session.commit()
        try:
            upsert_context_embeddings([summary_context])
        except Exception as exc:  # pragma: no cover - external dependency
            logger.warning("Weekly recap embedding upsert failed: {}", exc)

    return {
        "status": "updated",
        "start_date": window.start_date.isoformat(),
        "end_date": window.end_date.isoformat(),
        "context_id": str(summary_context.id),
    }


@celery_app.task(name="recaps.weekly_for_user")
def weekly_recap_for_user(
    user_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tz_name: Optional[str] = None,
) -> dict[str, Any]:
    resolved_user = UUID(user_id)
    parsed_start: Optional[date] = None
    parsed_end: Optional[date] = None
    if start_date:
        try:
            parsed_start = date.fromisoformat(start_date)
        except ValueError:
            return {"status": "invalid_start_date", "start_date": start_date}
    if end_date:
        try:
            parsed_end = date.fromisoformat(end_date)
        except ValueError:
            return {"status": "invalid_end_date", "end_date": end_date}
    return asyncio.run(
        _generate_weekly_recap(
            resolved_user,
            tz_name=tz_name,
            start_date=parsed_start,
            end_date=parsed_end,
        )
    )


@celery_app.task(name="recaps.weekly")
def weekly_recap_batch() -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        async with isolated_session() as session:
            result = await session.execute(select(UserSettings.user_id, UserSettings.settings))
            rows = result.fetchall()
        processed = 0
        queued = 0
        for user_id, settings in rows:
            if not isinstance(settings, dict):
                continue
            notifications = settings.get("notifications") or {}
            if not notifications.get("weeklySummary"):
                continue
            preferences = settings.get("preferences") or {}
            tz_name = preferences.get("timezone")
            weekly_recap_for_user.delay(str(user_id), tz_name=tz_name)
            processed += 1
            queued += 1
        return {"status": "queued", "users": processed, "tasks": queued}

    return asyncio.run(_run())
