"""RAG helpers for chat retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Any, Optional
from uuid import UUID

from .ai import summarize_text_with_gemini
from .ai.prompts import build_lifelog_query_entities_prompt
from .config import Settings, get_settings
from .pipeline.utils import ensure_tz_aware, parse_iso_datetime
from .vectorstore import search_contexts


@dataclass
class ParsedQuery:
    original: str
    date_range: Optional[tuple[datetime, datetime]]
    entities: dict[str, list[str]]


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

DATE_RE = re.compile(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b")
MDY_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
MONTH_DAY_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b",
    re.IGNORECASE,
)
MONTH_YEAR_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{4})\b",
    re.IGNORECASE,
)
RELATIVE_DAYS_RE = re.compile(r"\b(last|past)\s+(\d{1,3})\s+days\b")


def _day_bounds_local(day: date, offset: timedelta) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc) + offset
    end = start + timedelta(days=1)
    return start, end


def _month_bounds_local(year: int, month: int, offset: timedelta) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc) + offset
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) + offset
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc) + offset
    return start, end


def _extract_explicit_dates(query: str, now: datetime) -> list[date]:
    matches: list[date] = []
    for match in DATE_RE.finditer(query):
        year, month, day = match.groups()
        try:
            matches.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue

    for match in MONTH_DAY_RE.finditer(query):
        month_raw, day_raw, year_raw = match.groups()
        month = MONTHS.get(month_raw.lower())
        if not month:
            continue
        year = int(year_raw) if year_raw else now.year
        try:
            matches.append(date(year, month, int(day_raw)))
        except ValueError:
            continue

    for match in MDY_RE.finditer(query):
        month_raw, day_raw, year_raw = match.groups()
        if len(month_raw) == 4:
            continue
        try:
            month = int(month_raw)
            day = int(day_raw)
        except ValueError:
            continue
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        if year_raw:
            try:
                year_val = int(year_raw)
            except ValueError:
                continue
            if year_val < 100:
                year_val += 2000
        else:
            year_val = now.year
        try:
            parsed = date(year_val, month, day)
        except ValueError:
            continue
        if parsed not in matches:
            matches.append(parsed)

    return matches


def _extract_month_ranges(
    query: str, now: datetime, offset: timedelta
) -> Optional[tuple[datetime, datetime]]:
    match = MONTH_YEAR_RE.search(query)
    if match:
        month_raw, year_raw = match.groups()
        month = MONTHS.get(month_raw.lower())
        if month:
            return _month_bounds_local(int(year_raw), month, offset)
    return None


def _parse_date_range(
    query: str,
    now: datetime,
    offset: timedelta,
) -> Optional[tuple[datetime, datetime]]:
    lowered = query.lower()
    local_now = now - offset
    if "today" in lowered:
        return _day_bounds_local(local_now.date(), offset)
    if "yesterday" in lowered:
        return _day_bounds_local((local_now - timedelta(days=1)).date(), offset)
    if "last week" in lowered:
        start_local = (local_now - timedelta(days=7)).date()
        end_local = local_now.date() + timedelta(days=1)
        start = datetime.combine(start_local, time.min, tzinfo=timezone.utc) + offset
        end = datetime.combine(end_local, time.min, tzinfo=timezone.utc) + offset
        return start, end
    if "this week" in lowered:
        start_local = local_now.date() - timedelta(days=local_now.weekday())
        end_local = local_now.date() + timedelta(days=1)
        start = datetime.combine(start_local, time.min, tzinfo=timezone.utc) + offset
        end = datetime.combine(end_local, time.min, tzinfo=timezone.utc) + offset
        return start, end
    if "last month" in lowered:
        year = local_now.year
        month = local_now.month - 1
        if month <= 0:
            month = 12
            year -= 1
        return _month_bounds_local(year, month, offset)
    if "this month" in lowered:
        return _month_bounds_local(local_now.year, local_now.month, offset)
    if "last year" in lowered:
        start = datetime(local_now.year - 1, 1, 1, tzinfo=timezone.utc) + offset
        end = datetime(local_now.year, 1, 1, tzinfo=timezone.utc) + offset
        return start, end
    if "this year" in lowered:
        start = datetime(local_now.year, 1, 1, tzinfo=timezone.utc) + offset
        end = datetime(local_now.year + 1, 1, 1, tzinfo=timezone.utc) + offset
        return start, end

    rel_match = RELATIVE_DAYS_RE.search(lowered)
    if rel_match:
        days = int(rel_match.group(2))
        start = local_now - timedelta(days=days)
        return ensure_tz_aware(start + offset), ensure_tz_aware(local_now + offset)

    month_range = _extract_month_ranges(query, local_now, offset)
    if month_range:
        return month_range

    explicit_dates = _extract_explicit_dates(query, local_now)
    if len(explicit_dates) >= 2:
        first, second = explicit_dates[0], explicit_dates[1]
        if first > second:
            first, second = second, first
        start = datetime.combine(first, time.min, tzinfo=timezone.utc) + offset
        end = datetime.combine(second, time.min, tzinfo=timezone.utc) + offset + timedelta(days=1)
        return start, end
    if len(explicit_dates) == 1:
        return _day_bounds_local(explicit_dates[0], offset)
    return None


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


async def parse_query(
    query: str,
    *,
    settings: Optional[Settings] = None,
    tz_offset_minutes: Optional[int] = None,
) -> ParsedQuery:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    offset = timedelta(minutes=tz_offset_minutes or 0)
    date_range = _parse_date_range(query, now, offset)
    entities = await _extract_query_entities(query, settings)
    return ParsedQuery(original=query, date_range=date_range, entities=entities)


def _entity_name_set(entities: dict[str, list[str]]) -> set[str]:
    names: set[str] = set()
    for values in entities.values():
        for value in values:
            names.add(value.lower())
    return names


async def retrieve_context_hits(
    query: str,
    *,
    user_id: UUID,
    top_k: int = 10,
    settings: Optional[Settings] = None,
    tz_offset_minutes: Optional[int] = None,
) -> tuple[ParsedQuery, list[dict[str, Any]]]:
    settings = settings or get_settings()
    parsed = await parse_query(
        query,
        settings=settings,
        tz_offset_minutes=tz_offset_minutes,
    )
    start_time = parsed.date_range[0] if parsed.date_range else None
    end_time = parsed.date_range[1] if parsed.date_range else None
    candidates = search_contexts(
        query,
        limit=max(top_k * 8, 40),
        user_id=str(user_id),
        start_time=start_time,
        end_time=end_time,
    )

    now = datetime.now(timezone.utc)
    entity_names = _entity_name_set(parsed.entities)
    filtered: list[dict[str, Any]] = []
    for hit in candidates:
        score = float(hit.get("score") or 0.0)
        payload = hit.get("payload") or {}
        event_time_raw = payload.get("event_time_utc")
        event_time = parse_iso_datetime(event_time_raw) if isinstance(event_time_raw, str) else None
        if parsed.date_range and event_time:
            start, end = parsed.date_range
            if event_time < start or event_time >= end:
                continue
        elif parsed.date_range and not event_time:
            continue

        if payload.get("is_episode"):
            score *= 1.1

        if entity_names:
            payload_entities = payload.get("entities") or []
            hit_names: set[str] = set()
            for entity in payload_entities:
                if isinstance(entity, dict):
                    name = entity.get("name")
                else:
                    name = None
                if name:
                    hit_names.add(str(name).lower())
            if hit_names.intersection(entity_names):
                score *= 1.25

        if event_time:
            days_old = max((now - ensure_tz_aware(event_time)).days, 0)
            time_decay = 1.0 / (1.0 + days_old * 0.01)
            score *= time_decay

        filtered.append(
            {
                "context_id": hit.get("context_id"),
                "score": score,
                "payload": payload,
            }
        )

    filtered.sort(key=lambda entry: entry.get("score") or 0.0, reverse=True)
    return parsed, filtered[:top_k]
