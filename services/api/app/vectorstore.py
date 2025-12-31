"""Lightweight helpers for interacting with Qdrant."""

from __future__ import annotations

import hashlib
import random
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from google import genai
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from .config import get_settings
from .ai.usage import log_usage_from_response


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(url=str(settings.qdrant_url))

def _extract_collection_size(info: Any) -> Optional[int]:
    config = getattr(info, "config", None)
    params = getattr(config, "params", None) if config else None
    vectors = getattr(params, "vectors", None) if params else None
    if isinstance(vectors, qmodels.VectorParams):
        return vectors.size
    if isinstance(vectors, dict):
        if "size" in vectors:
            return vectors.get("size")
        first = next(iter(vectors.values()), None)
        if isinstance(first, dict):
            return first.get("size")
        if isinstance(first, qmodels.VectorParams):
            return first.size
    return None


def ensure_collection(vector_size: int) -> None:
    settings = get_settings()
    client = get_qdrant_client()
    if not client.collection_exists(settings.qdrant_collection):
        logger.info(
            "Creating Qdrant collection {} (dim={})",
            settings.qdrant_collection,
            vector_size,
        )
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(
                size=vector_size,
                distance=qmodels.Distance.COSINE,
            ),
        )
        return
    try:
        info = client.get_collection(settings.qdrant_collection)
    except Exception:
        return
    existing_size = _extract_collection_size(info)
    if existing_size and existing_size != vector_size:
        raise RuntimeError(
            f"Qdrant collection {settings.qdrant_collection} size mismatch "
            f"(expected {vector_size}, found {existing_size})."
        )


def _deterministic_vector(seed: int, size: int) -> List[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in range(size)]


@lru_cache(maxsize=1)
def _get_genai_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required for embedding requests.")
    return genai.Client(api_key=settings.gemini_api_key)


def _extract_embedding_values(embedding: Any) -> List[float]:
    if isinstance(embedding, dict):
        for key in ("values", "embedding", "vector", "data"):
            value = embedding.get(key)
            if isinstance(value, list):
                return value
    values = getattr(embedding, "values", None)
    if isinstance(values, list):
        return values
    values = getattr(embedding, "embedding", None)
    if isinstance(values, list):
        return values
    raise ValueError("Unable to parse embedding values from response")


def _extract_embeddings(response: Any) -> List[List[float]]:
    embeddings = getattr(response, "embeddings", None)
    if embeddings is None:
        embeddings = getattr(response, "embedding", None)
    if embeddings is None:
        return []
    if isinstance(embeddings, list):
        return [_extract_embedding_values(embed) for embed in embeddings]
    return [_extract_embedding_values(embeddings)]


def _deterministic_text_vector(text: str, size: int) -> List[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    return _deterministic_vector(seed, size)


def embed_texts(
    texts: List[str],
    *,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "embeddings",
) -> List[List[float]]:
    settings = get_settings()
    if settings.embedding_provider == "none":
        return [_deterministic_text_vector(text, settings.embedding_dimension) for text in texts]

    client = _get_genai_client()
    vectors: List[List[float]] = []
    batch_size = settings.embedding_batch_size
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = client.models.embed_content(
            model=settings.embedding_model,
            contents=batch,
        )
        log_usage_from_response(
            response,
            user_id=user_id,
            item_id=item_id,
            provider="gemini",
            model=settings.embedding_model,
            step_name=step_name,
        )
        embeddings = _extract_embeddings(response)
        if len(embeddings) != len(batch):
            raise RuntimeError("Embedding response length mismatch.")
        vectors.extend(embeddings)
    return vectors


def embed_text(
    text: str,
    *,
    user_id: UUID | str | None = None,
    item_id: UUID | str | None = None,
    step_name: str = "embeddings",
) -> List[float]:
    return embed_texts([text], user_id=user_id, item_id=item_id, step_name=step_name)[0]


def upsert_context_embeddings(contexts: Iterable[Any]) -> None:
    """Insert embeddings for processed contexts."""

    settings = get_settings()
    client = get_qdrant_client()
    points: list[qmodels.PointStruct] = []
    vector_texts: list[str] = []
    context_list = list(contexts)
    for context in context_list:
        vector_texts.append(getattr(context, "vector_text", "") or "")
    if not vector_texts:
        return
    unique_user_ids = {getattr(context, "user_id", None) for context in context_list}
    resolved_user = next(iter(unique_user_ids)) if len(unique_user_ids) == 1 else None
    unique_source_ids = set()
    for context in context_list:
        source_ids = getattr(context, "source_item_ids", None)
        if isinstance(source_ids, list):
            unique_source_ids.update(source_ids)
    resolved_item = next(iter(unique_source_ids)) if len(unique_source_ids) == 1 else None
    vectors = embed_texts(
        vector_texts,
        user_id=resolved_user,
        item_id=resolved_item,
        step_name="embeddings",
    )
    vector_size = len(vectors[0]) if vectors else settings.embedding_dimension
    ensure_collection(vector_size)
    if settings.embedding_provider == "gemini" and settings.embedding_dimension != vector_size:
        logger.warning(
            "Embedding dimension mismatch (config={} actual={}); update settings.embedding_dimension.",
            settings.embedding_dimension,
            vector_size,
        )
    for context, vector in zip(context_list, vectors):
        payload = {
            "context_id": str(context.id),
            "user_id": str(context.user_id),
            "context_type": getattr(context, "context_type", None),
            "is_episode": bool(getattr(context, "is_episode", False)),
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


def _build_filter(
    user_id: Optional[str],
    *,
    is_episode: Optional[bool] = None,
    context_type: Optional[str] = None,
) -> Optional[qmodels.Filter]:
    must: list[qmodels.FieldCondition] = []
    if user_id:
        must.append(qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id)))
    if is_episode is not None:
        must.append(qmodels.FieldCondition(key="is_episode", match=qmodels.MatchValue(value=is_episode)))
    if context_type:
        must.append(qmodels.FieldCondition(key="context_type", match=qmodels.MatchValue(value=context_type)))
    if not must:
        return None
    return qmodels.Filter(must=must)


def search_contexts(
    query: str,
    limit: int = 5,
    user_id: Optional[str] = None,
    *,
    is_episode: Optional[bool] = None,
    context_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query Qdrant using the configured embedding model."""

    settings = get_settings()
    client = get_qdrant_client()
    vector = embed_text(query, user_id=user_id, step_name="search_embedding")
    ensure_collection(len(vector))
    results = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=vector,
        limit=limit,
        with_payload=True,
        query_filter=_build_filter(user_id, is_episode=is_episode, context_type=context_type),
    )
    return [
        {
            "context_id": point.payload.get("context_id") or str(point.id),
            "score": point.score,
            "payload": point.payload,
        }
        for point in results
    ]


def delete_context_embeddings(context_ids: Iterable[str]) -> None:
    settings = get_settings()
    client = get_qdrant_client()
    ids = [str(context_id) for context_id in context_ids if context_id]
    if not ids:
        return
    if not client.collection_exists(settings.qdrant_collection):
        return
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.PointIdsList(points=ids),
        wait=True,
    )


__all__ = [
    "get_qdrant_client",
    "ensure_collection",
    "embed_text",
    "upsert_context_embeddings",
    "search_contexts",
    "delete_context_embeddings",
]
