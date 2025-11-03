"""Simple SQL-based migration runner."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterable, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .session import get_engine


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def _migration_files() -> Iterable[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def run_migrations(engine: AsyncEngine | None = None) -> List[str]:
    """Apply any pending SQL migrations in order."""

    engine = engine or get_engine()
    applied: List[str] = []

    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        result = await conn.execute(text("SELECT version FROM schema_migrations"))
        seen = {row[0] for row in result.fetchall()}

        for path in _migration_files():
            version, _, name = path.name.partition("_")
            if version in seen:
                continue
            sql = path.read_text()
            await conn.exec_driver_sql(sql)
            await conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, name) VALUES (:version, :name)"
                ),
                {"version": version, "name": name or path.stem},
            )
            applied.append(path.name)

    return applied


async def _main() -> None:
    engine = get_engine()
    applied = await run_migrations(engine)
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Database is up to date.")
    await engine.dispose()


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    asyncio.run(_main())
