"""Background maintenance tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from loguru import logger
from sqlalchemy import select, update

from ..celery_app import celery_app
from ..db.models import Device, ProcessedContext
from ..db.session import isolated_session
from ..pipeline.utils import build_vector_text
from ..vectorstore import upsert_context_embeddings


@celery_app.task(name="maintenance.cleanup")
def lifecycle_cleanup() -> dict[str, str]:
    """Placeholder lifecycle cleanup task."""

    timestamp = datetime.utcnow().isoformat()
    logger.info("Running lifecycle cleanup at {}", timestamp)
    return {"status": "ok", "timestamp": timestamp}


async def _cleanup_expired_pairing_codes(delete_orphans: bool = False) -> dict[str, int | str]:
    now = datetime.now(timezone.utc)
    deleted = 0

    async with isolated_session() as session:
        result = await session.execute(
            select(Device).where(
                Device.pairing_code_expires_at.isnot(None),
                Device.pairing_code_expires_at < now,
            )
        )
        expired_devices = result.scalars().all()

        if not expired_devices:
            return {"status": "ok", "cleared": 0, "deleted": 0}

        await session.execute(
            update(Device)
            .where(Device.pairing_code_expires_at.isnot(None), Device.pairing_code_expires_at < now)
            .values(pairing_code_hash=None, pairing_code_expires_at=None, updated_at=now)
        )

        if delete_orphans:
            for device in expired_devices:
                if device.device_token_hash is None:
                    await session.delete(device)
                    deleted += 1

        await session.commit()

    return {"status": "ok", "cleared": len(expired_devices), "deleted": deleted}


@celery_app.task(name="devices.cleanup_pairing_codes")
def cleanup_pairing_codes() -> dict[str, int | str]:
    """Clear expired pairing codes on a schedule."""

    try:
        return asyncio.run(_cleanup_expired_pairing_codes())
    except Exception as exc:  # pragma: no cover - avoid crashing beat
        logger.exception("Failed to cleanup pairing codes: {}", exc)
        raise


async def _reembed_contexts(
    *,
    user_id: str | None = None,
    context_type: str | None = None,
    batch_size: int = 200,
    offset: int = 0,
    max_batches: int = 50,
) -> dict[str, int | str]:
    user_uuid = None
    if user_id:
        user_uuid = UUID(str(user_id))

    total_seen = 0
    total_updated = 0
    batches = 0

    async with isolated_session() as session:
        while True:
            stmt = select(ProcessedContext).order_by(
                ProcessedContext.created_at.asc(),
                ProcessedContext.id.asc(),
            )
            if user_uuid:
                stmt = stmt.where(ProcessedContext.user_id == user_uuid)
            if context_type:
                stmt = stmt.where(ProcessedContext.context_type == context_type)
            stmt = stmt.limit(batch_size).offset(offset)

            result = await session.execute(stmt)
            contexts = list(result.scalars().all())
            if not contexts:
                break

            for context in contexts:
                vector_text = build_vector_text(
                    context.title or "",
                    context.summary or "",
                    context.keywords or [],
                    context_type=context.context_type,
                )
                if context.vector_text != vector_text:
                    context.vector_text = vector_text
                    total_updated += 1

            await session.flush()
            try:
                upsert_context_embeddings(contexts)
            except Exception as exc:  # pragma: no cover - external dependency
                logger.warning("Embedding upsert failed: {}", exc)

            await session.commit()

            total_seen += len(contexts)
            offset += batch_size
            batches += 1
            if max_batches and batches >= max_batches:
                break

    return {
        "status": "ok",
        "seen": total_seen,
        "updated": total_updated,
        "batches": batches,
        "next_offset": offset,
    }


@celery_app.task(name="maintenance.reembed_contexts")
def reembed_contexts(
    user_id: str | None = None,
    context_type: str | None = None,
    batch_size: int = 200,
    offset: int = 0,
    max_batches: int = 50,
) -> dict[str, int | str]:
    """Rebuild vector_text and upsert embeddings for processed contexts."""

    try:
        return asyncio.run(
            _reembed_contexts(
                user_id=user_id,
                context_type=context_type,
                batch_size=batch_size,
                offset=offset,
                max_batches=max_batches,
            )
        )
    except Exception as exc:  # pragma: no cover - avoid crashing beat
        logger.exception("Failed to reembed contexts: {}", exc)
        raise
