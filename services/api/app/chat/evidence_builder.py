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
    # Push entity_context hits later to avoid drowning out richer contexts.
    ordered = sorted(
        enumerate(ordered),
        key=lambda pair: (
            (pair[1].get("payload") or {}).get("context_type") == "entity_context",
            pair[0],
        ),
    )
    ordered = [hit for _idx, hit in ordered]

    seen_contexts: set[str] = set()
    seen_sources: set[tuple[str, ...]] = set()
    selected: list[dict] = []
    for hit in ordered:
        context_id = str(hit.get("context_id") or "")
        if not context_id or context_id in seen_contexts:
            continue
        payload = hit.get("payload") or {}
        if payload.get("context_type") != "daily_summary":
            source_ids = payload.get("source_item_ids") or []
            if source_ids:
                source_key = tuple(sorted(str(value) for value in source_ids))
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
        seen_contexts.add(context_id)
        selected.append(hit)
        if len(selected) >= max_sources:
            break
    return selected
