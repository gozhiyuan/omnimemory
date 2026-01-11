"""Background maintenance tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update

from ..celery_app import celery_app
from ..db.models import Device
from ..db.session import isolated_session


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
