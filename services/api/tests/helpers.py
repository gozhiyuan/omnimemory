"""Shared test helpers and mock classes for the test suite."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from uuid import UUID, uuid4


class FakeScalarResult:
    """Mock for scalar query results."""

    def __init__(self, items: list[Any]):
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class FakeResult:
    """Flexible mock for query results supporting scalars, rows, and single scalar."""

    def __init__(
        self,
        scalars: Optional[list[Any]] = None,
        rows: Optional[list[Any]] = None,
        scalar: Any = None,
    ):
        self._scalars = scalars or []
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._scalars)

    def fetchall(self) -> list[Any]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def one(self) -> Any:
        if self._rows:
            return self._rows[0]
        return self._scalar


class FakeSession:
    """Mock async database session that returns pre-configured results.

    Results are returned from a queue in order. When the queue is exhausted,
    falls back to returning existing_users as scalars (useful for user lookups).
    """

    def __init__(self, results: Optional[list[FakeResult]] = None, existing_users: Optional[list[Any]] = None):
        self._results = list(results) if results else []
        self._existing_users = existing_users or []
        self.added: list[Any] = []
        self.committed = False
        self._refresh_calls: list[Any] = []

    async def execute(self, _stmt: Any) -> FakeResult:
        if self._results:
            return self._results.pop(0)
        return FakeResult(scalars=self._existing_users)

    async def get(self, _model: Any, entity_id: Any) -> Any:
        for user in self._existing_users:
            if getattr(user, "id", None) == entity_id:
                return user
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: list[Any]) -> None:
        self.added.extend(objs)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj: Any) -> None:
        self._refresh_calls.append(obj)
        if not hasattr(obj, "id") or obj.id is None:
            obj.id = uuid4()


class FakeStorage:
    """Mock storage provider for presigned URL generation."""

    def get_presigned_download(self, key: str, _expires_s: int) -> dict[str, str]:
        return {"url": f"http://example.test/{key}"}

    def get_presigned_upload(self, key: str, content_type: str, _expires_s: int) -> dict[str, Any]:
        return {
            "url": f"https://storage.example.test/{key}?upload=true",
            "key": key,
            "headers": {"Content-Type": content_type},
        }


class FailingStorage:
    """Mock storage provider that raises errors."""

    def get_presigned_download(self, _key: str, _expires_s: int) -> dict[str, str]:
        raise RuntimeError("signing failed")

    def get_presigned_upload(self, _key: str, _content_type: str, _expires_s: int) -> dict[str, Any]:
        raise RuntimeError("upload signing failed")


class FakeCeleryTask:
    """Mock Celery task for capturing task dispatch calls."""

    def __init__(self):
        self.calls: list[Any] = []
        self._task_counter = 0

    def delay(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self._task_counter += 1
        call_info = {"args": args, "kwargs": kwargs}
        self.calls.append(call_info)
        task_id = f"fake-task-{self._task_counter}"
        if args and isinstance(args[0], dict) and "item_id" in args[0]:
            task_id = f"task-{args[0]['item_id']}"
        return SimpleNamespace(id=task_id)


def make_sample_item(
    item_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    item_type: str = "photo",
    captured_at: Optional[datetime] = None,
    storage_key: str = "uploads/ui/example.png",
    content_type: str = "image/png",
    original_filename: str = "example.png",
    processing_status: str = "completed",
    provider: str = "ui",
) -> SimpleNamespace:
    """Helper function to create SourceItem-like objects with custom values."""
    item_id = item_id or uuid4()
    user_id = user_id or UUID("12345678-1234-5678-1234-567812345678")
    created_at = captured_at or datetime(2025, 12, 24, 6, 28, 51, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=item_id,
        user_id=user_id,
        item_type=item_type,
        captured_at=created_at,
        event_time_utc=created_at,
        created_at=created_at,
        processing_status=processing_status,
        storage_key=storage_key,
        content_type=content_type,
        original_filename=original_filename,
        provider=provider,
    )


def override_get_session(fake_session: FakeSession):
    """Helper to create a session override for FastAPI dependency injection."""
    async def _override():
        yield fake_session
    return _override


def override_current_user_id(user_id: UUID):
    """Helper to create a user ID override for auth dependency."""
    async def _override():
        return user_id
    return _override
