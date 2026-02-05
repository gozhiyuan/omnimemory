"""Query understanding helpers for chat retrieval planning."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from ..config import Settings, get_settings
from ..rag import ParsedQuery, classify_query_intent, parse_query
from .query_plan import QueryPlan, QueryType, TimeRange


_SUMMARY_HINTS = (
    "summary",
    "summarize",
    "recap",
    "overview",
    "highlights",
    "how was my day",
    "how was my week",
    "how was my month",
)

_COMPARE_HINTS = (
    "compare",
    "difference",
    "differences",
    "vs",
    "versus",
    "compared to",
)

_COUNT_HINTS = (
    "how many",
    "count",
    "number of",
    "times did",
    "times have",
)

_BROWSE_HINTS = (
    "show me",
    "photos",
    "pictures",
    "memories",
    "timeline",
    "list",
    "browse",
)

_PLAN_HINTS = (
    "plan",
    "itinerary",
    "schedule",
)

_CREATIVE_HINTS = (
    "draft",
    "write",
    "compose",
    "album",
    "story",
)

_FOLLOWUP_HINTS = (
    "that",
    "those",
    "them",
    "same",
    "earlier",
    "previous",
    "again",
    "what about",
    "also",
    "and",
)


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _classify_query_type(query: str, intent: str) -> QueryType:
    q = _normalize(query)
    if intent == "meta_question":
        return "meta"
    if intent == "greeting":
        return "greeting"
    if intent == "clarification":
        return "clarification"
    if any(hint in q for hint in _SUMMARY_HINTS):
        return "summary"
    if any(hint in q for hint in _COMPARE_HINTS):
        return "compare"
    if any(hint in q for hint in _COUNT_HINTS):
        return "count"
    if any(hint in q for hint in _BROWSE_HINTS):
        return "browse"
    if any(hint in q for hint in _PLAN_HINTS):
        return "plan"
    if any(hint in q for hint in _CREATIVE_HINTS):
        return "creative"
    return "fact"


def _infer_grain(query: str, start: datetime, end: datetime) -> str:
    q = _normalize(query)
    if "week" in q:
        return "week"
    if "month" in q:
        return "month"
    if "year" in q:
        return "year"
    days = max(0, (end - start).days)
    if days <= 1:
        return "day"
    return "custom"


def _history_text(history: Iterable[object]) -> str:
    parts = []
    for entry in history:
        content = getattr(entry, "content", None)
        if content is None and isinstance(entry, dict):
            content = entry.get("content")
        if content:
            parts.append(str(content))
    return " ".join(parts)


def _detect_followup(query: str, history: Iterable[object]) -> dict[str, object]:
    q = _normalize(query)
    history_text = _normalize(_history_text(history))
    is_followup = bool(history_text) and any(hint in q for hint in _FOLLOWUP_HINTS)
    return {
        "is_followup": is_followup,
        "use_last_time_range": False,
        "use_last_entities": False,
    }


def _build_time_range(query: str, date_range: Optional[tuple[datetime, datetime]]) -> Optional[TimeRange]:
    if not date_range:
        return None
    start, end = date_range
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        end = start + timedelta(days=1)
    grain = _infer_grain(query, start, end)
    return TimeRange(start=start, end=end, grain=grain)


async def build_query_plan_with_parsed(
    query: str,
    history: Iterable[object],
    tz_offset_minutes: Optional[int],
    settings: Optional[Settings] = None,
) -> tuple[QueryPlan, ParsedQuery]:
    settings = settings or get_settings()
    intent = await classify_query_intent(query, settings)
    query_type = _classify_query_type(query, intent)
    if intent != "memory_query":
        parsed = ParsedQuery(original=query, date_range=None, entities={})
    else:
        parsed = await parse_query(
            query,
            settings=settings,
            tz_offset_minutes=tz_offset_minutes,
        )
    time_range = _build_time_range(query, parsed.date_range)
    followup = _detect_followup(query, history)
    retrieval = {
        "limit": None,
        "context_types": None,
        "allow_rerank": None,
    }
    return QueryPlan(
        intent=intent,
        query_type=query_type,
        time_range=time_range,
        entities=parsed.entities,
        retrieval=retrieval,
        followup=followup,
    ), parsed


async def build_query_plan(
    query: str,
    history: Iterable[object],
    tz_offset_minutes: Optional[int],
    settings: Optional[Settings] = None,
) -> QueryPlan:
    plan, _parsed = await build_query_plan_with_parsed(
        query=query,
        history=history,
        tz_offset_minutes=tz_offset_minutes,
        settings=settings,
    )
    return plan


def plan_to_dict(plan: QueryPlan) -> dict:
    data = asdict(plan)
    if plan.time_range:
        data["time_range"]["start"] = plan.time_range.start.isoformat()
        data["time_range"]["end"] = plan.time_range.end.isoformat()
    return data
