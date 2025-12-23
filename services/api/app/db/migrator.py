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

            def _split_statements(script: str) -> list[str]:
                statements: list[str] = []
                buffer: list[str] = []
                in_single = False
                in_double = False
                dollar_tag: str | None = None
                i = 0
                while i < len(script):
                    ch = script[i]
                    if dollar_tag:
                        if script.startswith(dollar_tag, i):
                            buffer.append(dollar_tag)
                            i += len(dollar_tag)
                            dollar_tag = None
                            continue
                        buffer.append(ch)
                        i += 1
                        continue

                    if ch == "'" and not in_double:
                        in_single = not in_single
                        buffer.append(ch)
                        i += 1
                        continue
                    if ch == '"' and not in_single:
                        in_double = not in_double
                        buffer.append(ch)
                        i += 1
                        continue
                    if ch == "$" and not in_single and not in_double:
                        j = i + 1
                        while j < len(script) and (script[j].isalnum() or script[j] == "_"):
                            j += 1
                        if j < len(script) and script[j] == "$":
                            tag = script[i : j + 1]
                            dollar_tag = tag
                            buffer.append(tag)
                            i = j + 1
                            continue
                    if ch == ";" and not in_single and not in_double:
                        statement = "".join(buffer).strip()
                        if statement:
                            statements.append(statement)
                        buffer = []
                        i += 1
                        continue
                    buffer.append(ch)
                    i += 1
                if buffer:
                    statement = "".join(buffer).strip()
                    if statement:
                        statements.append(statement)
                return statements

            for stmt in _split_statements(sql):
                await conn.exec_driver_sql(stmt)
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
