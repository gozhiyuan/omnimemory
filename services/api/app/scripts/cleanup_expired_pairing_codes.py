"""Clear expired device pairing codes."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.models import Device
from app.db.session import isolated_session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clear expired device pairing codes.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the number of expired pairing codes without modifying data.",
    )
    parser.add_argument(
        "--delete-orphans",
        action="store_true",
        help="Delete devices that have no token and only an expired pairing code.",
    )
    return parser.parse_args()


async def cleanup() -> None:
    args = parse_args()
    now = datetime.now(timezone.utc)

    async with isolated_session() as session:
        expired = await session.execute(
            select(Device).where(
                Device.pairing_code_expires_at.isnot(None),
                Device.pairing_code_expires_at < now,
            )
        )
        expired_devices = expired.scalars().all()
        if args.dry_run:
            print(f"Expired pairing codes: {len(expired_devices)}")
            return

        await session.execute(
            update(Device)
            .where(Device.pairing_code_expires_at.isnot(None), Device.pairing_code_expires_at < now)
            .values(pairing_code_hash=None, pairing_code_expires_at=None, updated_at=now)
        )

        if args.delete_orphans:
            for device in expired_devices:
                if device.device_token_hash is None:
                    await session.delete(device)

        await session.commit()
        print(f"Cleared expired pairing codes: {len(expired_devices)}")


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    asyncio.run(cleanup())
