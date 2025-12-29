"""Lightweight helpers for interacting with Qdrant."""

from __future__ import annotations

import hashlib
import random
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from .config import get_settings


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(url=str(settings.qdrant_url))


def ensure_collection() -> None:
    settings = get_settings()
    client = get_qdrant_client()
    if not client.collection_exists(settings.qdrant_collection):
        logger.info(
            "Creating Qdrant collection {} (dim={})",
            settings.qdrant_collection,
            settings.embedding_dimension,
        )
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(
                size=settings.embedding_dimension,
                distance=qmodels.Distance.COSINE,
            ),
        )


def _deterministic_vector(seed: int, size: int) -> List[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in range(size)]


def embed_text(text: str, size: int) -> List[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    return _deterministic_vector(seed, size)


def upsert_context_embeddings(contexts: Iterable[Any]) -> None:
    """Insert embeddings for processed contexts."""

    settings = get_settings()
    ensure_collection()
    client = get_qdrant_client()
    points: list[qmodels.PointStruct] = []
    for context in contexts:
        vector_text = getattr(context, "vector_text", "") or ""
        vector = embed_text(vector_text, settings.embedding_dimension)
        payload = {
            "context_id": str(context.id),
            "user_id": str(context.user_id),
            "context_type": getattr(context, "context_type", None),
            "event_time_utc": getattr(context, "event_time_utc", None).isoformat()
            if getattr(context, "event_time_utc", None)
            else None,
            "source_item_ids": [str(value) for value in getattr(context, "source_item_ids", [])],
            "entities": getattr(context, "entities", []),
        }
        points.append(
            qmodels.PointStruct(id=str(context.id), vector=vector, payload=payload)
        )
    if not points:
        return
    logger.info("Upserting {} context embeddings", len(points))
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=points,
        wait=True,
    )


def _user_filter(user_id: Optional[str]) -> Optional[qmodels.Filter]:
    if not user_id:
        return None
    return qmodels.Filter(
        must=[qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id))]
    )


def search_contexts(query: str, limit: int = 5, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Query Qdrant using deterministic placeholder embeddings."""

    settings = get_settings()
    ensure_collection()
    client = get_qdrant_client()
    vector = embed_text(query, settings.embedding_dimension)
    results = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=vector,
        limit=limit,
        with_payload=True,
        query_filter=_user_filter(user_id),
    )
    return [
        {
            "context_id": point.payload.get("context_id") or str(point.id),
            "score": point.score,
            "payload": point.payload,
        }
        for point in results
    ]


__all__ = [
    "get_qdrant_client",
    "ensure_collection",
    "embed_text",
    "upsert_context_embeddings",
    "search_contexts",
]
