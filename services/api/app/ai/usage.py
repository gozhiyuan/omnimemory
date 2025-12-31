"""Usage logging helpers for AI requests."""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import UUID

from loguru import logger

from ..db.models import AiUsageEvent
from ..db.session import get_sessionmaker


PRICING_TABLE_USD_PER_1M = {
    "gemini-2.5-flash-lite": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash": {"input": 0.35, "output": 1.05},
    "gemini-2.5-pro": {"input": 3.50, "output": 10.50},
    "gemini-1.5-flash": {"input": 0.35, "output": 1.05},
    "gemini-1.5-pro": {"input": 3.50, "output": 10.50},
    "gemini-embedding-001": {"input": 0.10, "output": 0.00},
}


def _normalize_model(model: str | None) -> str:
    return (model or "").strip()


def estimate_cost_usd(model: str, prompt_tokens: int, output_tokens: int) -> Optional[float]:
    pricing = PRICING_TABLE_USD_PER_1M.get(_normalize_model(model))
    if not pricing:
        return None
    input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 8)


def _extract_usage_value(usage: Any, *keys: str) -> Optional[int]:
    for key in keys:
        value = None
        if isinstance(usage, dict):
            value = usage.get(key)
        else:
            value = getattr(usage, key, None)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def extract_usage_metadata(response: Any) -> Optional[dict[str, int]]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    prompt_tokens = _extract_usage_value(
        usage,
        "prompt_token_count",
        "prompt_tokens",
        "input_token_count",
        "token_count",
    )
    output_tokens = _extract_usage_value(
        usage,
        "candidates_token_count",
        "output_token_count",
        "generated_token_count",
        "completion_token_count",
    )
    total_tokens = _extract_usage_value(
        usage,
        "total_token_count",
        "total_tokens",
    )
    if total_tokens is None:
        if prompt_tokens is None and output_tokens is None:
            return None
        total_tokens = int((prompt_tokens or 0) + (output_tokens or 0))
    return {
        "prompt_tokens": int(prompt_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "total_tokens": int(total_tokens),
    }


async def _write_usage_event(event: AiUsageEvent) -> None:
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            session.add(event)
            await session.commit()
    except Exception as exc:  # pragma: no cover - logging should never block primary path
        logger.warning("Failed to log AI usage event: {}", exc)


def log_ai_usage_event(
    *,
    user_id: UUID | str | None,
    item_id: UUID | str | None,
    provider: str,
    model: str,
    step_name: str,
    prompt_tokens: int,
    output_tokens: int,
    total_tokens: int,
    cost_usd: Optional[float],
) -> None:
    if not user_id:
        return
    try:
        resolved_user = UUID(str(user_id))
    except Exception:
        return
    resolved_item = None
    if item_id:
        try:
            resolved_item = UUID(str(item_id))
        except Exception:
            resolved_item = None

    event = AiUsageEvent(
        user_id=resolved_user,
        item_id=resolved_item,
        provider=provider,
        model=model,
        step_name=step_name,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_write_usage_event(event))
    else:
        loop.create_task(_write_usage_event(event))


def log_usage_from_response(
    response: Any,
    *,
    user_id: UUID | str | None,
    item_id: UUID | str | None,
    provider: str,
    model: str,
    step_name: str,
) -> None:
    usage = extract_usage_metadata(response)
    if not usage:
        return
    prompt_tokens = usage["prompt_tokens"]
    output_tokens = usage["output_tokens"]
    total_tokens = usage["total_tokens"]
    cost_usd = estimate_cost_usd(model, prompt_tokens, output_tokens)
    log_ai_usage_event(
        user_id=user_id,
        item_id=item_id,
        provider=provider,
        model=model,
        step_name=step_name,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )
