"""Lightweight helpers for interacting with Qdrant."""

from __future__ import annotations

import random
from functools import lru_cache
from typing import Any, Dict, List
from uuid import UUID

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


def _random_vector(seed: int, size: int) -> List[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in range(size)]


def upsert_random_embedding(item_id: UUID, payload: Dict[str, Any]) -> None:
    """Insert a placeholder vector for an item."""

    settings = get_settings()
    ensure_collection()
    client = get_qdrant_client()
    vector = _random_vector(hash(item_id.int) & 0xFFFFFFFF, settings.embedding_dimension)
    logger.info("Upserting placeholder embedding for item {}", item_id)
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            qmodels.PointStruct(
                id=str(item_id),
                vector=vector,
                payload=payload,
            )
        ],
        wait=True,
    )


def search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Query Qdrant using a deterministic placeholder embedding for now."""

    settings = get_settings()
    ensure_collection()
    client = get_qdrant_client()
    vector = _random_vector(hash(query) & 0xFFFFFFFF, settings.embedding_dimension)
    results = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=vector,
        limit=limit,
        with_payload=True,
    )
    return [
        {
            "item_id": point.payload.get("item_id"),
            "score": point.score,
            "payload": point.payload,
        }
        for point in results
    ]


__all__ = [
    "get_qdrant_client",
    "ensure_collection",
    "upsert_random_embedding",
    "search",
]
