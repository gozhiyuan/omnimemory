"""Cache helpers backed by Redis/Valkey."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import get_settings


_redis_client: Optional[Redis] = None
_redis_loop: Optional[asyncio.AbstractEventLoop] = None


def get_redis_client() -> Redis:
    global _redis_client, _redis_loop
    settings = get_settings()
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _redis_client is None or (_redis_loop is not None and current_loop is not None and _redis_loop is not current_loop):
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        _redis_loop = current_loop
    return _redis_client


async def get_cache_json(key: str) -> Optional[dict[str, Any]]:
    client = get_redis_client()
    try:
        raw = await client.get(key)
    except (RedisError, OSError, RuntimeError) as exc:
        logger.warning("Cache read failed for {}: {}", key, exc)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Cache payload invalid for {}: {}", key, exc)
        return None


async def set_cache_json(key: str, payload: Any, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    client = get_redis_client()
    try:
        await client.setex(key, ttl_seconds, json.dumps(payload, default=str))
    except (RedisError, OSError, RuntimeError) as exc:
        logger.warning("Cache write failed for {}: {}", key, exc)
