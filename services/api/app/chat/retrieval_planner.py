"""Retrieval planning for chat queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .query_plan import QueryPlan


@dataclass
class RetrievalConfig:
    limit: int
    context_types: Optional[set[str]] = None
    allow_rerank: Optional[bool] = None


def plan_retrieval(plan: QueryPlan) -> RetrievalConfig:
    query_type = plan.query_type
    if query_type == "summary":
        return RetrievalConfig(
            limit=50,
            context_types={"daily_summary", "episode"},
            allow_rerank=True,
        )
    if query_type == "fact":
        return RetrievalConfig(
            limit=20,
            context_types={"episode", "activity_context", "user_annotation"},
            allow_rerank=True,
        )
    if query_type == "browse":
        return RetrievalConfig(
            limit=40,
            context_types={"episode", "daily_summary", "activity_context"},
            allow_rerank=False,
        )
    if query_type == "compare":
        return RetrievalConfig(
            limit=40,
            context_types={"episode", "daily_summary"},
            allow_rerank=True,
        )
    if query_type == "count":
        return RetrievalConfig(
            limit=30,
            context_types=None,
            allow_rerank=False,
        )
    return RetrievalConfig(limit=40, context_types=None, allow_rerank=None)
