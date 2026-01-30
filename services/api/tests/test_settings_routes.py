"""Tests for settings routes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.auth import get_current_user_id
from app.db.session import get_session
from app.routes import settings as settings_module

from tests.helpers import FakeResult, FakeSession, override_get_session, override_current_user_id


TEST_USER_ID = UUID("12345678-1234-5678-1234-567812345678")


# ---------------------------------------------------------------------------
# GET /settings tests
# ---------------------------------------------------------------------------


def test_get_settings_empty(monkeypatch):
    """Get settings when none exist returns empty dict."""
    fake_session = FakeSession([FakeResult(scalar=None)])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)

    client = TestClient(app)
    response = client.get("/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"] == {}
    assert payload["updated_at"] is None


def test_get_settings_existing(monkeypatch):
    """Get settings returns existing settings."""
    now = datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    existing_settings = SimpleNamespace(
        user_id=TEST_USER_ID,
        settings={"theme": "dark", "notifications": {"email": True}},
        updated_at=now,
    )
    fake_session = FakeSession([FakeResult(scalar=existing_settings)])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)

    client = TestClient(app)
    response = client.get("/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["theme"] == "dark"
    assert payload["settings"]["notifications"]["email"] is True
    assert payload["updated_at"] is not None


# ---------------------------------------------------------------------------
# PUT /settings tests
# ---------------------------------------------------------------------------


def test_update_settings_create_new(monkeypatch):
    """Update settings creates new record."""
    fake_session = FakeSession([FakeResult()])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)

    client = TestClient(app)
    response = client.put(
        "/settings",
        json={"settings": {"theme": "light"}}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["theme"] == "light"
    assert payload["updated_at"] is not None
    assert fake_session.committed


def test_update_settings_replace_existing(monkeypatch):
    """Update settings replaces existing record."""
    fake_session = FakeSession([FakeResult()])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)

    client = TestClient(app)
    response = client.put(
        "/settings",
        json={"settings": {"theme": "dark", "language": "en"}}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["theme"] == "dark"
    assert payload["settings"]["language"] == "en"


def test_update_settings_empty_object(monkeypatch):
    """Update settings with empty object is allowed."""
    fake_session = FakeSession([FakeResult()])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)

    client = TestClient(app)
    response = client.put(
        "/settings",
        json={"settings": {}}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"] == {}


# ---------------------------------------------------------------------------
# POST /settings/weekly-recap tests
# ---------------------------------------------------------------------------


def test_weekly_recap_disabled_rejected(monkeypatch):
    """Weekly recap is rejected when disabled in settings."""
    existing_settings = SimpleNamespace(
        user_id=TEST_USER_ID,
        settings={"notifications": {"weeklySummary": False}},
        updated_at=datetime.now(timezone.utc),
    )
    fake_session = FakeSession([FakeResult(scalar=existing_settings)])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)

    client = TestClient(app)
    response = client.post(
        "/settings/weekly-recap",
        json={}
    )

    assert response.status_code == 400
    assert "disabled" in response.json()["detail"].lower()


def test_weekly_recap_force_bypass(monkeypatch):
    """Weekly recap force=true bypasses settings check."""
    existing_settings = SimpleNamespace(
        user_id=TEST_USER_ID,
        settings={"notifications": {"weeklySummary": False}},
        updated_at=datetime.now(timezone.utc),
    )
    fake_session = FakeSession([FakeResult(scalar=existing_settings)])

    tasks = []

    def fake_delay(*args, **kwargs):
        tasks.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(id="task-123")

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(settings_module.weekly_recap_for_user, "delay", fake_delay)

    client = TestClient(app)
    response = client.post(
        "/settings/weekly-recap",
        json={"force": True}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "task-123"
    assert payload["status"] == "queued"
    assert len(tasks) == 1


def test_weekly_recap_task_dispatch(monkeypatch):
    """Weekly recap dispatches Celery task."""
    existing_settings = SimpleNamespace(
        user_id=TEST_USER_ID,
        settings={"notifications": {"weeklySummary": True}},
        updated_at=datetime.now(timezone.utc),
    )
    fake_session = FakeSession([FakeResult(scalar=existing_settings)])

    tasks = []

    def fake_delay(*args, **kwargs):
        tasks.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(id="task-456")

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(settings_module.weekly_recap_for_user, "delay", fake_delay)

    client = TestClient(app)
    response = client.post(
        "/settings/weekly-recap",
        json={}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "task-456"
    assert len(tasks) == 1
    # Verify user_id was passed to task
    assert str(TEST_USER_ID) in tasks[0]["args"]


def test_weekly_recap_with_custom_dates(monkeypatch):
    """Weekly recap accepts custom date range."""
    existing_settings = SimpleNamespace(
        user_id=TEST_USER_ID,
        settings={"notifications": {"weeklySummary": True}},
        updated_at=datetime.now(timezone.utc),
    )
    fake_session = FakeSession([FakeResult(scalar=existing_settings)])

    tasks = []

    def fake_delay(*args, **kwargs):
        tasks.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(id="task-789")

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(settings_module.weekly_recap_for_user, "delay", fake_delay)

    client = TestClient(app)
    response = client.post(
        "/settings/weekly-recap",
        json={"start_date": "2025-01-01", "end_date": "2025-01-07"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["start_date"] == "2025-01-01"
    assert payload["end_date"] == "2025-01-07"


def test_weekly_recap_respects_timezone(monkeypatch):
    """Weekly recap uses user's timezone preference."""
    existing_settings = SimpleNamespace(
        user_id=TEST_USER_ID,
        settings={
            "notifications": {"weeklySummary": True},
            "preferences": {"timezone": "America/New_York"}
        },
        updated_at=datetime.now(timezone.utc),
    )
    fake_session = FakeSession([FakeResult(scalar=existing_settings)])

    tasks = []

    def fake_delay(*args, **kwargs):
        tasks.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(id="task-tz")

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(settings_module.weekly_recap_for_user, "delay", fake_delay)

    client = TestClient(app)
    response = client.post(
        "/settings/weekly-recap",
        json={}
    )

    assert response.status_code == 200
    assert len(tasks) == 1
    # Timezone should be passed to task
    assert tasks[0]["kwargs"]["tz_name"] == "America/New_York"
