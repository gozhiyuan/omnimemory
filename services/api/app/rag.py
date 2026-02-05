"""RAG helpers for chat retrieval."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
import re
from typing import Any, Literal, Optional
from uuid import UUID

from sqlalchemy import Text, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from .ai import summarize_text_with_gemini
from .ai.prompts import (
    build_lifelog_date_range_prompt,
    build_lifelog_query_entities_prompt,
    build_lifelog_query_intent_prompt,
    build_lifelog_rerank_prompt,
)
from .config import Settings, get_settings
from .db.models import ProcessedContext
from .pipeline.utils import ensure_tz_aware, parse_iso_datetime
from .vectorstore import search_contexts

logger = logging.getLogger(__name__)


@dataclass
class ParsedQuery:
    original: str
    date_range: Optional[tuple[datetime, datetime]]
    entities: dict[str, list[str]]


_RECENCY_HINTS = (
    "last time",
    "most recent",
    "latest",
    "recently",
    "most lately",
    "when was the last",
    "when did i last",
    "when did we last",
)


_RECAP_HINTS = (
    "recap",
    "summary",
    "summarize",
    "overview",
    "highlights",
    "how was my day",
    "how was my week",
    "how was my month",
    "weekly summary",
    "daily summary",
    "monthly summary",
)


_ACTIVITY_HINTS = (
    "what did i do",
    "what did we do",
    "what happened",
    "show me photos",
    "show me memories",
    "show me pictures",
    "what did i see",
)


_RELATIVE_DATE_PATTERNS = (
    ("day before yesterday", "day_before_yesterday"),
    ("yesterday", "yesterday"),
    ("today", "today"),
    ("tomorrow", "tomorrow"),
    ("last week", "last_week"),
    ("previous week", "last_week"),
    ("this week", "this_week"),
    ("last month", "last_month"),
    ("previous month", "last_month"),
    ("this month", "this_month"),
    ("last year", "last_year"),
    ("previous year", "last_year"),
    ("this year", "this_year"),
)

def _extract_json(text: str) -> Optional[dict]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _parse_local_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _local_dates_to_utc_range(
    start_date: date,
    end_date: date,
    offset: timedelta,
) -> tuple[datetime, datetime]:
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc) + offset
    end = datetime.combine(end_date, time.min, tzinfo=timezone.utc) + offset
    return start, end


def _extract_date_range_heuristic(
    query: str,
    tz_offset_minutes: Optional[int],
) -> Optional[tuple[datetime, datetime]]:
    if not query:
        return None
    q = " ".join(query.lower().split())
    offset = timedelta(minutes=tz_offset_minutes or 0)
    local_today = (datetime.now(timezone.utc) - offset).date()

    # Explicit ISO dates first
    iso_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", q)
    parsed_dates: list[date] = []
    for raw in iso_dates:
        parsed = _parse_local_date(raw)
        if parsed:
            parsed_dates.append(parsed)
    if parsed_dates:
        start_date = min(parsed_dates)
        end_date = max(parsed_dates)
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        else:
            end_date = end_date + timedelta(days=1)
        logger.info(
            "Heuristic date range: iso_dates=%s start=%s end=%s",
            iso_dates,
            start_date,
            end_date,
        )
        return _local_dates_to_utc_range(start_date, end_date, offset)

    # Relative named ranges
    for phrase, label in _RELATIVE_DATE_PATTERNS:
        if phrase not in q:
            continue
        if label == "day_before_yesterday":
            start_date = local_today - timedelta(days=2)
            end_date = local_today - timedelta(days=1)
        elif label == "yesterday":
            start_date = local_today - timedelta(days=1)
            end_date = local_today
        elif label == "today":
            start_date = local_today
            end_date = local_today + timedelta(days=1)
        elif label == "tomorrow":
            start_date = local_today + timedelta(days=1)
            end_date = local_today + timedelta(days=2)
        elif label == "last_week":
            start_of_week = local_today - timedelta(days=local_today.weekday())
            start_date = start_of_week - timedelta(days=7)
            end_date = start_of_week
        elif label == "this_week":
            start_date = local_today - timedelta(days=local_today.weekday())
            end_date = start_date + timedelta(days=7)
        elif label == "last_month":
            first_this = local_today.replace(day=1)
            last_month_end = first_this - timedelta(days=1)
            start_date = last_month_end.replace(day=1)
            end_date = first_this
        elif label == "this_month":
            start_date = local_today.replace(day=1)
            rollover = start_date.replace(day=28) + timedelta(days=4)
            end_date = rollover.replace(day=1)
        elif label == "last_year":
            start_date = date(local_today.year - 1, 1, 1)
            end_date = date(local_today.year, 1, 1)
        elif label == "this_year":
            start_date = date(local_today.year, 1, 1)
            end_date = date(local_today.year + 1, 1, 1)
        else:
            continue

        logger.info(
            "Heuristic date range: phrase='%s' start=%s end=%s",
            phrase,
            start_date,
            end_date,
        )
        return _local_dates_to_utc_range(start_date, end_date, offset)

    match = re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", q)
    if match:
        try:
            days = int(match.group(1))
        except ValueError:
            days = 0
        if days > 0:
            start_date = local_today - timedelta(days=days - 1)
            end_date = local_today + timedelta(days=1)
            logger.info(
                "Heuristic date range: last %s days start=%s end=%s",
                days,
                start_date,
                end_date,
            )
            return _local_dates_to_utc_range(start_date, end_date, offset)

    return None


async def _extract_date_range_with_llm(
    query: str,
    settings: Settings,
    tz_offset_minutes: Optional[int],
) -> Optional[tuple[datetime, datetime]]:
    heuristic = _extract_date_range_heuristic(query, tz_offset_minutes)
    if heuristic:
        return heuristic
    if settings.chat_provider != "gemini" or not settings.gemini_api_key:
        return None
    offset = timedelta(minutes=tz_offset_minutes or 0)
    now = datetime.now(timezone.utc)
    # Calculate local time by adjusting UTC. Note: tz_offset_minutes from JS getTimezoneOffset
    # convention is negative for positive UTC offsets, so we subtract it.
    local_now = now - offset
    # Remove timezone info to avoid confusing the LLM - the datetime represents local time
    local_now_naive = local_now.replace(tzinfo=None)
    logger.debug(f"Date range extraction: UTC now={now.isoformat()}, offset_minutes={tz_offset_minutes}, local_now={local_now_naive.isoformat()}")
    prompt = build_lifelog_date_range_prompt(
        query=query,
        now_iso=local_now_naive.isoformat(),
        tz_offset_minutes=tz_offset_minutes or 0,
    )
    response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.chat_model,
        temperature=0.0,
        max_output_tokens=128,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="date_range",
    )
    parsed = response.get("parsed")
    if not isinstance(parsed, dict):
        parsed = _extract_json(response.get("raw_text", ""))
    if not isinstance(parsed, dict):
        return None
    start_date = _parse_local_date(parsed.get("start_date"))
    end_date = _parse_local_date(parsed.get("end_date"))
    if not start_date and not end_date:
        return None
    if start_date and not end_date:
        end_date = start_date + timedelta(days=1)
    if end_date and not start_date:
        return None
    if not start_date or not end_date:
        return None
    if end_date <= start_date:
        end_date = start_date + timedelta(days=1)
    logger.info(f"Date range extracted: query='{query[:30]}...', local_now={local_now.isoformat()}, start={start_date}, end={end_date}")
    return _local_dates_to_utc_range(start_date, end_date, offset)


def _normalize_entity_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


async def _extract_query_entities(query: str, settings: Settings) -> dict[str, list[str]]:
    if not settings.chat_entity_extraction_enabled:
        return {}
    if settings.chat_provider != "gemini" or not settings.gemini_api_key:
        return {}
    prompt = build_lifelog_query_entities_prompt(query)
    response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.chat_model,
        temperature=0.0,
        max_output_tokens=128,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="query_entities",
    )
    parsed = response.get("parsed")
    if not isinstance(parsed, dict):
        return {}
    return {
        "people": _normalize_entity_list(parsed.get("people")),
        "places": _normalize_entity_list(parsed.get("places")),
        "objects": _normalize_entity_list(parsed.get("objects")),
        "organizations": _normalize_entity_list(parsed.get("organizations")),
        "topics": _normalize_entity_list(parsed.get("topics")),
        "food": _normalize_entity_list(parsed.get("food")),
    }


QueryIntent = Literal["memory_query", "meta_question", "greeting", "clarification"]


def _heuristic_intent(query: str) -> Optional[QueryIntent]:
    if not query:
        return "greeting"
    q = " ".join(query.lower().split())
    if not q:
        return "greeting"
    greeting_patterns = (
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "good morning",
        "good afternoon",
        "good evening",
    )
    if q in greeting_patterns or (len(q.split()) <= 3 and any(q.startswith(p) for p in greeting_patterns)):
        return "greeting"

    meta_patterns = (
        "what day is it",
        "what's the date",
        "what is the date",
        "what time is it",
        "current time",
        "today's date",
        "todays date",
    )
    if any(pattern in q for pattern in meta_patterns):
        return "meta_question"
    return None


async def classify_query_intent(
    query: str,
    settings: Settings,
) -> QueryIntent:
    """Classify user query intent using LLM.

    Returns one of: memory_query, meta_question, greeting, clarification.
    Defaults to memory_query if classification fails.
    """
    heuristic = _heuristic_intent(query)
    if heuristic:
        return heuristic
    if settings.chat_provider != "gemini" or not settings.gemini_api_key:
        return "memory_query"

    prompt = build_lifelog_query_intent_prompt(query)
    response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.chat_model,
        temperature=0.0,
        max_output_tokens=64,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="query_intent",
    )

    parsed = response.get("parsed")
    raw_text = response.get("raw_text", "")
    if not isinstance(parsed, dict):
        parsed = _extract_json(raw_text)

    if isinstance(parsed, dict):
        intent = parsed.get("intent", "")
        if intent in ("memory_query", "meta_question", "greeting", "clarification"):
            logger.info(f"Intent classification: query='{query[:50]}...' -> intent={intent}")
            return intent

    logger.warning(f"Intent classification failed for query='{query[:50]}...', raw_text='{raw_text[:100]}', defaulting to memory_query")
    return "memory_query"


async def _rerank_candidates_with_llm(
    query: str,
    candidates: list[dict[str, Any]],
    settings: Settings,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Rerank candidates using LLM.

    Returns reranked list limited to top_k.
    Falls back to original order if reranking fails.
    """
    if not candidates:
        return []

    if settings.chat_provider != "gemini" or not settings.gemini_api_key:
        return candidates[:top_k]

    # Format candidates for the prompt
    candidate_lines = []
    for idx, hit in enumerate(candidates):
        payload = hit.get("payload") or {}
        title = payload.get("title", "")
        summary = payload.get("summary", "")
        event_time = payload.get("event_time_utc", "")
        context_type = payload.get("context_type", "")
        is_episode = payload.get("is_episode", False)

        line_parts = [f"[{idx}]"]
        if title:
            line_parts.append(f"Title: {title}")
        if summary:
            # Truncate long summaries
            truncated = summary[:200] + "..." if len(summary) > 200 else summary
            line_parts.append(f"Summary: {truncated}")
        if event_time:
            line_parts.append(f"Time: {event_time}")
        if context_type:
            line_parts.append(f"Type: {context_type}")
        if is_episode:
            line_parts.append("(Episode)")

        candidate_lines.append(" | ".join(line_parts))

    candidates_text = "\n".join(candidate_lines)

    prompt = build_lifelog_rerank_prompt(query, candidates_text)
    response = await summarize_text_with_gemini(
        prompt=prompt,
        settings=settings,
        model=settings.chat_model,
        temperature=0.0,
        max_output_tokens=256,
        timeout_seconds=settings.chat_timeout_seconds,
        step_name="rerank",
    )

    parsed = response.get("parsed")
    if not isinstance(parsed, dict):
        parsed = _extract_json(response.get("raw_text", ""))

    if isinstance(parsed, dict):
        ranking = parsed.get("ranking", [])
        if isinstance(ranking, list) and ranking:
            reranked = []
            seen_indices = set()
            for idx in ranking:
                if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen_indices:
                    reranked.append(candidates[idx])
                    seen_indices.add(idx)
                    if len(reranked) >= top_k:
                        break
            if reranked:
                logger.info(f"LLM reranking: input={len(candidates)}, output={len(reranked)}, ranking={ranking[:10]}")
                return reranked
            else:
                logger.warning(f"LLM reranking returned empty list, ranking={ranking}")

    logger.warning(f"LLM reranking failed, using original order. parsed={parsed}, raw={response.get('raw_text', '')[:100]}")
    return candidates[:top_k]


async def parse_query(
    query: str,
    *,
    settings: Optional[Settings] = None,
    tz_offset_minutes: Optional[int] = None,
    date_range_override: Optional[tuple[datetime, datetime]] = None,
) -> ParsedQuery:
    settings = settings or get_settings()
    if date_range_override:
        date_range = date_range_override
    else:
        date_range = await _extract_date_range_with_llm(
            query=query,
            settings=settings,
            tz_offset_minutes=tz_offset_minutes,
        )
    entities = await _extract_query_entities(query, settings)
    return ParsedQuery(original=query, date_range=date_range, entities=entities)


def _entity_name_set(entities: dict[str, list[str]]) -> set[str]:
    names: set[str] = set()
    for values in entities.values():
        for value in values:
            names.add(value.lower())
    return names


def _detect_recency_intent(query: str) -> bool:
    if not query:
        return False
    q = " ".join(query.lower().split())
    return any(hint in q for hint in _RECENCY_HINTS)


def _detect_recap_intent(query: str) -> bool:
    if not query:
        return False
    q = " ".join(query.lower().split())
    return any(hint in q for hint in _RECAP_HINTS)


def _detect_activity_intent(query: str) -> bool:
    if not query:
        return False
    q = " ".join(query.lower().split())
    return any(hint in q for hint in _ACTIVITY_HINTS)


def _extract_payload_entity_names(payload: dict[str, Any]) -> set[str]:
    entities = payload.get("entities") or []
    names: set[str] = set()
    if isinstance(entities, list):
        for entry in entities:
            if isinstance(entry, dict):
                name = entry.get("name")
            else:
                name = entry
            if not name:
                continue
            names.add(str(name).strip().lower())
    return names


async def _search_contexts_fts(
    session: AsyncSession,
    query: str,
    *,
    user_id: UUID,
    limit: int,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    document = func.to_tsvector(
        "english",
        func.coalesce(ProcessedContext.title, "")
        + literal(" ")
        + func.coalesce(ProcessedContext.summary, "")
        + literal(" ")
        + func.coalesce(cast(ProcessedContext.keywords, Text), ""),
    )
    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank_cd(document, tsquery)
    stmt = (
        select(ProcessedContext, rank.label("rank"))
        .where(
            ProcessedContext.user_id == user_id,
            document.op("@@")(tsquery),
        )
        .order_by(rank.desc(), ProcessedContext.event_time_utc.desc())
        .limit(limit)
    )
    if start_time:
        stmt = stmt.where(ProcessedContext.event_time_utc >= start_time)
    if end_time:
        stmt = stmt.where(ProcessedContext.event_time_utc < end_time)

    rows = await session.execute(stmt)
    results: list[dict[str, Any]] = []
    for context, rank_value in rows.all():
        event_time = ensure_tz_aware(context.event_time_utc) if context.event_time_utc else None
        results.append(
            {
                "context_id": str(context.id),
                "fts_score": float(rank_value or 0.0),
                "payload": {
                    "title": context.title,
                    "summary": context.summary,
                    "event_time_utc": event_time.isoformat() if event_time else None,
                    "context_type": context.context_type,
                    "is_episode": bool(context.is_episode),
                    "entities": context.entities or [],
                },
            }
        )
    return results


def _rrf_fuse_candidates(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    k: int,
) -> list[dict[str, Any]]:
    if not ranked_lists:
        return []
    scores: defaultdict[str, float] = defaultdict(float)
    payloads: dict[str, dict[str, Any]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for results in ranked_lists:
        for idx, hit in enumerate(results):
            context_id = str(hit.get("context_id") or "")
            if not context_id:
                continue
            scores[context_id] += 1.0 / (k + idx + 1)
            payload = hit.get("payload") or {}
            if payload:
                if context_id not in payloads:
                    payloads[context_id] = dict(payload)
                else:
                    merged = payloads[context_id]
                    for key, value in payload.items():
                        if key not in merged or merged.get(key) in (None, "", [], {}):
                            if value not in (None, "", [], {}):
                                merged[key] = value
            meta = metadata.setdefault(context_id, {})
            if hit.get("score") is not None:
                meta["vector_score"] = float(hit.get("score") or 0.0)
            if hit.get("fts_score") is not None:
                meta["fts_score"] = float(hit.get("fts_score") or 0.0)

    fused: list[dict[str, Any]] = []
    max_fts = max((meta.get("fts_score", 0.0) for meta in metadata.values()), default=0.0)
    for context_id, combined in scores.items():
        meta = metadata.get(context_id, {})
        fts_score = float(meta.get("fts_score") or 0.0)
        fts_norm = fts_score / max_fts if max_fts > 0 else 0.0
        fused.append(
            {
                "context_id": context_id,
                "combined_score": combined,
                "score": meta.get("vector_score", fts_norm),
                "fts_score": fts_score,
                "payload": payloads.get(context_id, {}),
            }
        )
    fused.sort(key=lambda item: item.get("combined_score", 0.0), reverse=True)
    return fused


def _event_time_for_hit(hit: dict[str, Any]) -> Optional[datetime]:
    payload = hit.get("payload") or {}
    raw = payload.get("event_time_utc")
    if isinstance(raw, str):
        return parse_iso_datetime(raw)
    if isinstance(raw, datetime):
        return ensure_tz_aware(raw)
    return None


def _sort_hits_by_recency(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(hit: dict[str, Any]) -> tuple[datetime, float]:
        event_time = hit.get("event_time") or _event_time_for_hit(hit)
        if not event_time:
            event_time = datetime.min.replace(tzinfo=timezone.utc)
        score = float(hit.get("score") or 0.0)
        return (event_time, score)

    return sorted(hits, key=sort_key, reverse=True)


def _filter_hits_to_local_day(
    hits: list[dict[str, Any]],
    tz_offset_minutes: Optional[int],
) -> list[dict[str, Any]]:
    if not hits:
        return hits
    top_event = _event_time_for_hit(hits[0])
    if not top_event:
        return hits
    offset = timedelta(minutes=tz_offset_minutes or 0)
    local_date = (top_event - offset).date()
    start, end = _local_dates_to_utc_range(local_date, local_date + timedelta(days=1), offset)
    filtered: list[dict[str, Any]] = []
    for hit in hits:
        event_time = hit.get("event_time") or _event_time_for_hit(hit)
        if not event_time:
            continue
        if start <= event_time < end:
            filtered.append(hit)
    return filtered or hits


async def retrieve_context_hits(
    query: str,
    *,
    user_id: UUID,
    top_k: int = 10,
    settings: Optional[Settings] = None,
    tz_offset_minutes: Optional[int] = None,
    session: Optional[AsyncSession] = None,
    date_range_override: Optional[tuple[datetime, datetime]] = None,
    start_time_override: Optional[datetime] = None,
    end_time_override: Optional[datetime] = None,
    parsed_override: Optional[ParsedQuery] = None,
    intent_override: Optional[QueryIntent] = None,
    query_type: Optional[str] = None,
    context_types: Optional[set[str]] = None,
    allow_rerank: Optional[bool] = None,
) -> tuple[QueryIntent, ParsedQuery, list[dict[str, Any]]]:
    """Retrieve context hits with intent classification.

    Returns:
        Tuple of (intent, parsed_query, hits).
        For non-memory intents (meta_question, greeting, clarification), hits will be empty.
    """
    settings = settings or get_settings()

    # Classify intent first (allow override from query planning)
    intent = intent_override or await classify_query_intent(query, settings)

    # For non-memory queries, skip RAG entirely
    if intent != "memory_query":
        logger.debug(f"Skipping RAG for intent={intent}")
        parsed = ParsedQuery(original=query, date_range=None, entities={})
        return intent, parsed, []

    # Parse query for date range and entities
    if parsed_override is not None:
        parsed = parsed_override
    else:
        parsed = await parse_query(
            query,
            settings=settings,
            tz_offset_minutes=tz_offset_minutes,
            date_range_override=date_range_override,
        )
    start_time = start_time_override or (parsed.date_range[0] if parsed.date_range else None)
    end_time = end_time_override or (parsed.date_range[1] if parsed.date_range else None)
    recency_intent = _detect_recency_intent(query) and not (
        parsed.date_range or start_time_override or end_time_override
    )
    recap_intent = _detect_recap_intent(query)
    activity_intent = _detect_activity_intent(query)

    # Vector search with expanded limit for reranking
    candidate_limit = max(top_k * 4, 40)
    if recency_intent:
        candidate_limit = max(candidate_limit, top_k * 8, 80)
    vector_candidates = search_contexts(
        query,
        limit=candidate_limit,
        user_id=str(user_id),
        start_time=start_time,
        end_time=end_time,
    )
    fts_candidates: list[dict[str, Any]] = []
    if settings.rag_hybrid_enabled and session is not None:
        fts_limit = max(settings.rag_fts_limit, top_k * 3)
        try:
            fts_candidates = await _search_contexts_fts(
                session,
                query,
                user_id=user_id,
                limit=fts_limit,
                start_time=start_time,
                end_time=end_time,
            )
        except Exception as exc:
            logger.warning("FTS search failed: %s", exc)
            fts_candidates = []

    if fts_candidates:
        candidates = _rrf_fuse_candidates(
            [vector_candidates, fts_candidates],
            k=settings.rag_rrf_k,
        )
    else:
        candidates = _rrf_fuse_candidates([vector_candidates], k=settings.rag_rrf_k)

    # Apply date filter as hard constraint
    filtered: list[dict[str, Any]] = []
    for hit in candidates:
        payload = hit.get("payload") or {}
        event_time_raw = payload.get("event_time_utc")
        event_time = parse_iso_datetime(event_time_raw) if isinstance(event_time_raw, str) else None

        # Strict date filtering
        if (start_time or end_time) and event_time:
            if start_time and event_time < start_time:
                continue
            if end_time and event_time >= end_time:
                continue
        elif (start_time or end_time) and not event_time:
            continue

        hit["event_time"] = event_time
        filtered.append(hit)

    if context_types:
        typed = [
            hit
            for hit in filtered
            if (hit.get("payload") or {}).get("context_type") in context_types
        ]
        if typed:
            filtered = typed

    if filtered and not recap_intent:
        filtered_without_daily = [
            hit
            for hit in filtered
            if (hit.get("payload") or {}).get("context_type") != "daily_summary"
        ]
        if filtered_without_daily:
            filtered = filtered_without_daily

    query_entities = _entity_name_set(parsed.entities)
    for hit in filtered:
        payload = hit.get("payload") or {}
        combined_score = float(hit.get("combined_score") or 0.0)
        match_count = 0
        if query_entities:
            payload_entities = _extract_payload_entity_names(payload)
            match_count = len(query_entities.intersection(payload_entities))
            if match_count:
                combined_score += match_count * settings.rag_entity_match_boost
                hit["entity_match_count"] = match_count

        context_type = payload.get("context_type")
        if recap_intent:
            if context_type == "daily_summary":
                combined_score += settings.rag_recap_daily_boost
            elif context_type == "weekly_summary":
                combined_score += settings.rag_recap_weekly_boost
        else:
            if activity_intent and context_type == "activity_context":
                combined_score += settings.rag_activity_context_boost
            elif context_type == "daily_summary":
                combined_score -= settings.rag_daily_penalty

        hit["combined_score"] = combined_score

    filtered.sort(key=lambda item: float(item.get("combined_score") or 0.0), reverse=True)

    # Log context types in filtered results
    context_types = [h.get("payload", {}).get("context_type", "unknown") for h in filtered[:10]]
    logger.info(
        "RAG retrieval: query='%s...', candidates=%s, after_date_filter=%s, filter_start=%s, filter_end=%s, recap_intent=%s, activity_intent=%s, recency_intent=%s, context_types=%s",
        query[:30],
        len(candidates),
        len(filtered),
        start_time,
        end_time,
        recap_intent,
        activity_intent,
        recency_intent,
        context_types,
    )

    # LLM reranking (optional)
    if allow_rerank is None and query_type:
        allow_rerank = query_type in ("fact", "summary", "compare")
    if allow_rerank is None:
        allow_rerank = True

    rerank_limit = top_k * 2 if recency_intent else top_k
    if allow_rerank:
        reranked = await _rerank_candidates_with_llm(
            query,
            filtered,
            settings,
            top_k=rerank_limit,
        )
    else:
        reranked = filtered[:rerank_limit]

    if recency_intent and reranked:
        reranked = _sort_hits_by_recency(reranked)
        reranked = _filter_hits_to_local_day(reranked, tz_offset_minutes)
        reranked = reranked[:top_k]

    reranked_types = [h.get("payload", {}).get("context_type", "unknown") for h in reranked[:10]]
    logger.info(f"RAG reranking: after_rerank={len(reranked)}, reranked_types={reranked_types}")

    return intent, parsed, reranked
