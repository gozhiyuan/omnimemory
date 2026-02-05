"""Reindex ProcessedContext embeddings and refresh Qdrant payloads."""

from __future__ import annotations

import argparse
import asyncio
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from app.db.models import ProcessedContext
from app.db.session import isolated_session
from app.vectorstore import upsert_context_embeddings


async def _reindex(
    *,
    user_id: Optional[UUID],
    context_type: Optional[str],
    batch_size: int,
) -> None:
    async with isolated_session() as session:
        offset = 0
        total = 0
        while True:
            stmt = select(ProcessedContext).order_by(ProcessedContext.created_at.asc())
            if user_id:
                stmt = stmt.where(ProcessedContext.user_id == user_id)
            if context_type:
                stmt = stmt.where(ProcessedContext.context_type == context_type)
            stmt = stmt.offset(offset).limit(batch_size)

            rows = await session.execute(stmt)
            contexts = list(rows.scalars().all())
            if not contexts:
                break

            upsert_context_embeddings(contexts)
            total += len(contexts)
            offset += len(contexts)
            print(f"Upserted {len(contexts)} contexts (total={total})")

        print(f"Done. Total contexts reindexed: {total}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex context embeddings in Qdrant.")
    parser.add_argument("--user-id", help="Filter by user UUID", default=None)
    parser.add_argument("--context-type", help="Filter by context_type", default=None)
    parser.add_argument("--batch-size", type=int, default=200, help="Batch size for reindexing")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    user_id = UUID(args.user_id) if args.user_id else None
    asyncio.run(
        _reindex(
            user_id=user_id,
            context_type=args.context_type,
            batch_size=args.batch_size,
        )
    )


if __name__ == "__main__":
    main()
