"""Fix demo item event_time_utc to match captured_at."""

from __future__ import annotations

import argparse
import asyncio
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select, update

from app.db.models import SourceItem
from app.db.session import isolated_session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize demo event_time_utc to captured_at.")
    parser.add_argument(
        "--provider",
        default="demo",
        help="Provider label to update (default: demo).",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="Optional user_id to scope the update (UUID).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Update all matching rows even if event_time_source is not metadata.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts and sample rows without modifying data.",
    )
    return parser.parse_args()


def _build_filters(provider: str, user_id: UUID | None, update_all: bool) -> Sequence:
    filters = [
        SourceItem.provider == provider,
        SourceItem.captured_at.isnot(None),
    ]
    if user_id:
        filters.append(SourceItem.user_id == user_id)
    if not update_all:
        filters.append(SourceItem.event_time_source == "metadata")
    return filters


async def main() -> None:
    args = parse_args()
    user_id = UUID(args.user_id) if args.user_id else None
    filters = _build_filters(args.provider, user_id, args.all)

    async with isolated_session() as session:
        count_stmt = select(func.count()).select_from(SourceItem).where(*filters)
        total = int((await session.execute(count_stmt)).scalar() or 0)
        print(f"Matching items: {total}")
        if total == 0:
            return

        sample_stmt = (
            select(
                SourceItem.id,
                SourceItem.captured_at,
                SourceItem.event_time_utc,
                SourceItem.event_time_source,
            )
            .where(*filters)
            .order_by(SourceItem.created_at.desc())
            .limit(5)
        )
        rows = (await session.execute(sample_stmt)).all()
        print("Sample rows:")
        for row in rows:
            print(
                f"- {row.id} captured_at={row.captured_at} "
                f"event_time_utc={row.event_time_utc} source={row.event_time_source}"
            )

        if args.dry_run:
            print("Dry run only; no changes applied.")
            return

        update_stmt = (
            update(SourceItem)
            .where(*filters)
            .values(
                event_time_utc=SourceItem.captured_at,
                event_time_source="manual",
                event_time_confidence=0.95,
            )
        )
        await session.execute(update_stmt)
        await session.commit()
        print("Event times updated.")


if __name__ == "__main__":
    asyncio.run(main())
