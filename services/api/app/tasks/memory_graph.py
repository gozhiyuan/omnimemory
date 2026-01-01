"""Memory graph extraction tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Iterable
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..celery_app import celery_app
from ..db.models import MemoryEdge, MemoryNode, ProcessedContext, SourceItem
from ..db.session import isolated_session


NODE_TYPE_MAP = {
    "person": "person",
    "people": "person",
    "place": "place",
    "location": "place",
    "object": "object",
    "org": "org",
    "organization": "org",
    "food": "food",
    "topic": "topic",
    "activity": "activity",
}


def _normalize_node_type(raw_type: str | None) -> str:
    if not raw_type:
        return "other"
    cleaned = raw_type.strip().lower()
    return NODE_TYPE_MAP.get(cleaned, cleaned if cleaned else "other")


def _normalize_entity_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(str(name).strip().split())


def _collect_entity_payloads(context: ProcessedContext) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entity in context.entities or []:
        if not isinstance(entity, dict):
            continue
        node_type = _normalize_node_type(str(entity.get("type") or ""))
        name = _normalize_entity_name(entity.get("name"))
        if not name:
            continue
        key = (node_type, name.lower())
        if key in seen:
            continue
        seen.add(key)
        entries.append({"node_type": node_type, "name": name, "attributes": entity})

    location = context.location or {}
    if isinstance(location, dict):
        location_name = _normalize_entity_name(
            location.get("name")
            or location.get("place_name")
            or location.get("formatted_address")
        )
        if location_name:
            key = ("place", location_name.lower())
            if key not in seen:
                seen.add(key)
                entries.append(
                    {
                        "node_type": "place",
                        "name": location_name,
                        "attributes": {"source": "location", "raw": location},
                    }
                )
    return entries


async def _upsert_node(
    session,
    *,
    user_id: UUID,
    node_type: str,
    name: str,
    attributes: dict[str, Any],
) -> UUID:
    table = MemoryNode.__table__
    stmt = (
        insert(table)
        .values(
            {
                "user_id": user_id,
                "node_type": node_type,
                "name": name,
                "attributes": attributes,
                "first_seen": datetime.now(timezone.utc),
                "last_seen": datetime.now(timezone.utc),
                "mention_count": 1,
            }
        )
        .on_conflict_do_update(
            index_elements=[table.c.user_id, table.c.node_type, table.c.name],
            set_={
                "last_seen": func.now(),
                "mention_count": table.c.mention_count + 1,
            },
        )
        .returning(table.c.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def _upsert_edge(
    session,
    *,
    user_id: UUID,
    source_node_id: UUID,
    target_node_id: UUID,
    relation_type: str,
    source_item_id: UUID,
    source_context_id: UUID,
    strength: float = 1.0,
) -> None:
    if source_node_id == target_node_id:
        return
    table = MemoryEdge.__table__
    stmt = insert(table).values(
        {
            "user_id": user_id,
            "source_node_id": source_node_id,
            "target_node_id": target_node_id,
            "relation_type": relation_type,
            "strength": strength,
            "mention_count": 1,
            "last_connected": datetime.now(timezone.utc),
            "source_item_id": source_item_id,
            "source_context_id": source_context_id,
        }
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.user_id, table.c.source_node_id, table.c.target_node_id, table.c.relation_type],
        set_={
            "strength": table.c.strength + stmt.excluded.strength,
            "mention_count": table.c.mention_count + 1,
            "last_connected": func.now(),
            "source_item_id": stmt.excluded.source_item_id,
            "source_context_id": stmt.excluded.source_context_id,
        },
    )
    await session.execute(stmt)


async def _update_memory_graph_for_item(session, item: SourceItem) -> dict[str, Any]:
    context_stmt = (
        select(ProcessedContext)
        .where(
            ProcessedContext.user_id == item.user_id,
            ProcessedContext.is_episode.is_(False),
            ProcessedContext.source_item_ids.contains([item.id]),
        )
        .order_by(ProcessedContext.created_at.asc())
    )
    rows = await session.execute(context_stmt)
    contexts = list(rows.scalars().all())
    if not contexts:
        return {"status": "skipped", "reason": "no_contexts"}

    node_cache: dict[tuple[str, str], UUID] = {}
    edges_created = 0
    nodes_touched = 0

    for context in contexts:
        payloads = _collect_entity_payloads(context)
        if not payloads:
            continue
        node_ids: list[UUID] = []
        for payload in payloads:
            key = (payload["node_type"], payload["name"].lower())
            node_id = node_cache.get(key)
            if node_id is None:
                node_id = await _upsert_node(
                    session,
                    user_id=item.user_id,
                    node_type=payload["node_type"],
                    name=payload["name"],
                    attributes=payload.get("attributes", {}),
                )
                node_cache[key] = node_id
            node_ids.append(node_id)
            nodes_touched += 1

        unique_nodes = sorted(set(node_ids))
        for left, right in combinations(unique_nodes, 2):
            await _upsert_edge(
                session,
                user_id=item.user_id,
                source_node_id=left,
                target_node_id=right,
                relation_type="co_occurs",
                source_item_id=item.id,
                source_context_id=context.id,
            )
            edges_created += 1

    return {
        "status": "ok",
        "contexts": len(contexts),
        "nodes_touched": nodes_touched,
        "edges_created": edges_created,
    }


async def _process_item(item_id: UUID) -> dict[str, Any]:
    async with isolated_session() as session:
        item = await session.get(SourceItem, item_id)
        if not item:
            return {"status": "skipped", "reason": "item_not_found"}
        result = await _update_memory_graph_for_item(session, item)
        await session.commit()
        return result


@celery_app.task(name="memory_graph.update_for_item")
def update_for_item(item_id: str) -> dict[str, Any]:
    """Update memory graph nodes/edges for a single source item."""

    try:
        resolved = UUID(item_id)
    except Exception as exc:  # pragma: no cover - validation guard
        logger.warning("Invalid item id for memory graph: {}", exc)
        return {"status": "error", "reason": "invalid_item_id"}
    return asyncio.run(_process_item(resolved))

