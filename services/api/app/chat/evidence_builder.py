"""Evidence selection for chat responses."""

from __future__ import annotations

from typing import Iterable

from .query_plan import QueryPlan


def build_evidence_hits(
    hits: Iterable[dict],
    plan: QueryPlan,
    max_sources: int = 8,
) -> list[dict]:
    """Select a trimmed, deduped list of hits for grounding evidence."""
    ordered = list(hits)
    if plan.query_type == "summary":
        daily = [hit for hit in ordered if (hit.get("payload") or {}).get("context_type") == "daily_summary"]
        rest = [hit for hit in ordered if (hit.get("payload") or {}).get("context_type") != "daily_summary"]
        ordered = daily + rest

    seen_contexts: set[str] = set()
    selected: list[dict] = []
    for hit in ordered:
        context_id = str(hit.get("context_id") or "")
        if not context_id or context_id in seen_contexts:
            continue
        seen_contexts.add(context_id)
        selected.append(hit)
        if len(selected) >= max_sources:
            break
    return selected
