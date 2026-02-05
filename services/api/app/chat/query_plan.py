"""Query planning structures for chat retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

QueryType = Literal[
    "fact",
    "summary",
    "browse",
    "compare",
    "count",
    "meta",
    "greeting",
    "clarification",
    "plan",
    "creative",
]

QueryIntent = Literal["memory_query", "meta_question", "greeting", "clarification"]


@dataclass
class TimeRange:
    start: datetime
    end: datetime
    grain: Literal["day", "week", "month", "year", "custom"]


@dataclass
class QueryPlan:
    intent: QueryIntent
    query_type: QueryType
    time_range: Optional[TimeRange]
    entities: dict[str, list[str]]
    retrieval: dict[str, object]
    followup: dict[str, object]
