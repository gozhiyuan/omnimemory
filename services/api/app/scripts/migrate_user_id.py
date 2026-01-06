"""Migrate data from the default local user to an OIDC user."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text, update

from app.db.models import DEFAULT_TEST_USER_ID, User, UserSettings
from app.db.session import isolated_session


TABLES_WITH_USER_ID = [
    "data_connections",
    "source_items",
    "derived_artifacts",
    "processed_contexts",
    "timeline_day_highlights",
    "memory_nodes",
    "memory_edges",
    "daily_summaries",
    "chat_sessions",
    "chat_messages",
    "chat_feedback",
    "chat_attachments",
    "ai_usage_events",
]


@dataclass(frozen=True)
class UserTarget:
    id: UUID
    email: str | None
    display_name: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate data to a new user_id.")
    parser.add_argument(
        "--old-user-id",
        default=str(DEFAULT_TEST_USER_ID),
        help="Existing user_id to migrate from (default: local dev user).",
    )
    parser.add_argument(
        "--new-user-id",
        help="Target user_id to migrate to (UUID).",
    )
    parser.add_argument(
        "--new-email",
        help="Target user email to migrate to (used if user_id is not provided).",
    )
    parser.add_argument(
        "--new-display-name",
        help="Optional display name for a newly created user.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if the target user already has data.",
    )
    parser.add_argument(
        "--delete-old-user",
        action="store_true",
        help="Delete the old user row after migrating.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without modifying data.",
    )
    return parser.parse_args()


async def resolve_target_user(
    session,
    new_user_id: str | None,
    new_email: str | None,
    new_display_name: str | None,
) -> UserTarget:
    target_user: User | None = None
    resolved_id: UUID | None = None

    if new_user_id:
        resolved_id = UUID(new_user_id)
        target_user = await session.get(User, resolved_id)

    if target_user is None and new_email:
        result = await session.execute(select(User).where(User.email == new_email))
        target_user = result.scalar_one_or_none()
        if target_user:
            resolved_id = target_user.id

    if target_user is None:
        if resolved_id is None:
            resolved_id = uuid4()
        target_user = User(
            id=resolved_id,
            email=new_email,
            display_name=new_display_name,
        )
        session.add(target_user)
        await session.commit()
    else:
        updated = False
        if new_email and target_user.email != new_email:
            target_user.email = new_email
            updated = True
        if new_display_name and target_user.display_name != new_display_name:
            target_user.display_name = new_display_name
            updated = True
        if updated:
            await session.commit()

    return UserTarget(
        id=target_user.id,
        email=target_user.email,
        display_name=target_user.display_name,
    )


async def count_rows(session, table: str, user_id: UUID) -> int:
    result = await session.execute(
        text(f"SELECT COUNT(1) FROM {table} WHERE user_id = :user_id"),
        {"user_id": str(user_id)},
    )
    return int(result.scalar() or 0)


async def merge_user_settings(session, old_id: UUID, new_id: UUID, dry_run: bool) -> None:
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id.in_([old_id, new_id]))
    )
    records = result.scalars().all()
    old_record = next((row for row in records if row.user_id == old_id), None)
    new_record = next((row for row in records if row.user_id == new_id), None)

    if old_record is None:
        return

    if dry_run:
        return

    now = datetime.now(timezone.utc)
    if new_record is None:
        await session.execute(
            update(UserSettings)
            .where(UserSettings.user_id == old_id)
            .values(user_id=new_id, updated_at=now)
        )
        return

    merged_settings = {**new_record.settings, **old_record.settings}
    await session.execute(
        update(UserSettings)
        .where(UserSettings.user_id == new_id)
        .values(settings=merged_settings, updated_at=now)
    )
    await session.execute(delete(UserSettings).where(UserSettings.user_id == old_id))


async def migrate() -> None:
    args = parse_args()
    old_user_id = UUID(args.old_user_id)

    async with isolated_session() as session:
        target = await resolve_target_user(
            session,
            args.new_user_id,
            args.new_email,
            args.new_display_name,
        )

        if old_user_id == target.id:
            raise SystemExit("Old user_id and new user_id are the same. Nothing to migrate.")

        print(f"Old user_id: {old_user_id}")
        print(f"New user_id: {target.id}")
        if target.email:
            print(f"New user email: {target.email}")

        existing_new = {}
        for table in TABLES_WITH_USER_ID + ["user_settings"]:
            existing_new[table] = await count_rows(session, table, target.id)

        if not args.force:
            conflicts = {table: count for table, count in existing_new.items() if count > 0}
            if conflicts:
                conflict_list = ", ".join(f"{table}={count}" for table, count in conflicts.items())
                raise SystemExit(
                    "Target user already has data. Re-run with --force to proceed. "
                    f"Existing rows: {conflict_list}"
                )

        existing_old = {}
        for table in TABLES_WITH_USER_ID + ["user_settings"]:
            existing_old[table] = await count_rows(session, table, old_user_id)

        print("Rows to migrate:")
        for table, count in existing_old.items():
            print(f"  {table}: {count}")

        if args.dry_run:
            print("Dry run only; no changes applied.")
            return

        await merge_user_settings(session, old_user_id, target.id, dry_run=False)

        for table in TABLES_WITH_USER_ID:
            await session.execute(
                text(f"UPDATE {table} SET user_id = :new_id WHERE user_id = :old_id"),
                {"new_id": str(target.id), "old_id": str(old_user_id)},
            )

        await session.commit()

        if args.delete_old_user:
            await session.execute(delete(User).where(User.id == old_user_id))
            await session.commit()

        print("Migration complete.")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
