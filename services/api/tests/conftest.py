"""Pytest configuration and fixtures for the test suite."""

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.main import app

# Re-export helpers for use in tests
from tests.helpers import (
    FakeScalarResult,
    FakeResult,
    FakeSession,
    FakeStorage,
    FailingStorage,
    FakeCeleryTask,
    make_sample_item,
    override_get_session,
    override_current_user_id,
)


os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "lifelog")
os.environ.setdefault("POSTGRES_USER", "lifelog")
os.environ.setdefault("POSTGRES_PASSWORD", "lifelog")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_dependency_overrides():
    """Reset FastAPI dependency overrides before and after each test."""
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}


@pytest.fixture
def fake_session() -> FakeSession:
    """Provide a fresh FakeSession instance."""
    return FakeSession()


@pytest.fixture
def fake_storage() -> FakeStorage:
    """Provide a FakeStorage instance."""
    return FakeStorage()


@pytest.fixture
def failing_storage() -> FailingStorage:
    """Provide a FailingStorage instance."""
    return FailingStorage()


@pytest.fixture
def fake_celery_task() -> FakeCeleryTask:
    """Provide a FakeCeleryTask instance for capturing task calls."""
    return FakeCeleryTask()


@pytest.fixture
def sample_user_id() -> UUID:
    """Provide a standard test user UUID."""
    return UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def sample_item(sample_user_id: UUID) -> SimpleNamespace:
    """Factory fixture for creating SourceItem-like objects."""
    item_id = uuid4()
    created_at = datetime(2025, 12, 24, 6, 28, 51, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=item_id,
        user_id=sample_user_id,
        item_type="photo",
        captured_at=created_at,
        event_time_utc=created_at,
        created_at=created_at,
        processing_status="completed",
        storage_key="uploads/ui/example.png",
        content_type="image/png",
        original_filename="example.png",
        provider="ui",
    )
