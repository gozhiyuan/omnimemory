from collections import namedtuple
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_session
from app.routes import dashboard as dashboard_module


class FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeResult:
    def __init__(self, scalars=None, rows=None, scalar=None):
        self._scalars = scalars or []
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return FakeScalarResult(self._scalars)

    def fetchall(self):
        return self._rows

    def scalar_one(self):
        return self._scalar

    def one(self):
        if self._rows:
            return self._rows[0]
        return self._scalar


class FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return self._results.pop(0)


class FakeStorage:
    def get_presigned_download(self, key: str, _expires_s: int):
        return {"url": f"http://example.test/{key}"}


class FailingStorage:
    def get_presigned_download(self, _key: str, _expires_s: int):
        raise RuntimeError("signing failed")


def test_dashboard_stats_returns_activity_and_recent_items(monkeypatch):
    item_id = uuid4()
    created_at = datetime(2025, 12, 24, 6, 28, 51, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=item_id,
        item_type="photo",
        captured_at=created_at,
        event_time_utc=created_at,
        created_at=created_at,
        processing_status="completed",
        storage_key="uploads/ui/example.png",
        content_type="image/png",
        original_filename="example.png",
    )
    caption_row = SimpleNamespace(item_id=item_id, data={"text": "Example caption"})
    ActivityRow = namedtuple("ActivityRow", ["day", "count"])
    UsageRow = SimpleNamespace(prompt_tokens=10, output_tokens=5, total_tokens=15, cost_usd=0.0012)

    fake_session = FakeSession(
        [
            FakeResult(scalar=4),
            FakeResult(scalar=2),
            FakeResult(scalar=1),
            FakeResult(scalar=0),
            FakeResult(scalar=3),
            FakeResult(scalar=4285357),
            FakeResult(rows=[UsageRow]),
            FakeResult(rows=[UsageRow]),
            FakeResult(scalars=[item]),
            FakeResult(rows=[caption_row]),
            FakeResult(scalars=[]),
            FakeResult(rows=[]),
            FakeResult(rows=[ActivityRow(day=date.today(), count=4)]),
            FakeResult(rows=[]),
        ]
    )

    async def override_get_session():
        yield fake_session

    async def fake_cache_get(_key: str):
        return None

    async def fake_cache_set(_key: str, _payload, _ttl: int):
        return None

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr(dashboard_module, "get_storage_provider", lambda: FakeStorage())
    monkeypatch.setattr(dashboard_module, "get_cache_json", fake_cache_get)
    monkeypatch.setattr(dashboard_module, "set_cache_json", fake_cache_set)

    client = TestClient(app)
    response = client.get("/dashboard/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_items"] == 4
    assert payload["uploads_last_7_days"] == 3
    assert payload["storage_used_bytes"] == 4285357
    assert payload["recent_items"][0]["caption"] == "Example caption"
    assert payload["recent_items"][0]["download_url"] == "http://example.test/uploads/ui/example.png"
    assert len(payload["activity"]) == 7


def test_dashboard_handles_signing_failures(monkeypatch):
    item_id = uuid4()
    created_at = datetime(2025, 12, 24, 6, 28, 51, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=item_id,
        item_type="photo",
        captured_at=created_at,
        event_time_utc=created_at,
        created_at=created_at,
        processing_status="completed",
        storage_key="uploads/ui/example.png",
        content_type="image/png",
        original_filename="example.png",
    )
    ActivityRow = namedtuple("ActivityRow", ["day", "count"])
    UsageRow = SimpleNamespace(prompt_tokens=0, output_tokens=0, total_tokens=0, cost_usd=0.0)

    fake_session = FakeSession(
        [
            FakeResult(scalar=1),
            FakeResult(scalar=1),
            FakeResult(scalar=0),
            FakeResult(scalar=0),
            FakeResult(scalar=1),
            FakeResult(scalar=0),
            FakeResult(rows=[UsageRow]),
            FakeResult(rows=[UsageRow]),
            FakeResult(scalars=[item]),
            FakeResult(rows=[]),
            FakeResult(scalars=[]),
            FakeResult(rows=[]),
            FakeResult(rows=[ActivityRow(day=date.today(), count=1)]),
            FakeResult(rows=[]),
        ]
    )

    async def override_get_session():
        yield fake_session

    async def fake_cache_get(_key: str):
        return None

    async def fake_cache_set(_key: str, _payload, _ttl: int):
        return None

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr(dashboard_module, "get_storage_provider", lambda: FailingStorage())
    monkeypatch.setattr(dashboard_module, "get_cache_json", fake_cache_get)
    monkeypatch.setattr(dashboard_module, "set_cache_json", fake_cache_set)

    client = TestClient(app)
    response = client.get("/dashboard/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["recent_items"][0]["download_url"] is None


def test_dashboard_stats_uses_cache(monkeypatch):
    cached_payload = {
        "total_items": 8,
        "processed_items": 8,
        "failed_items": 0,
        "active_connections": 1,
        "uploads_last_7_days": 3,
        "storage_used_bytes": 1024,
        "recent_items": [],
        "activity": [],
        "usage_this_week": {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        },
        "usage_all_time": {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        },
        "usage_daily": [],
    }

    class FailingSession:
        async def execute(self, _stmt):
            raise AssertionError("DB queries should not run on cache hit.")

    async def override_get_session():
        yield FailingSession()

    async def fake_cache_get(_key: str):
        return cached_payload

    async def fake_cache_set(_key: str, _payload, _ttl: int):
        raise AssertionError("Cache write should not run on cache hit.")

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr(dashboard_module, "get_cache_json", fake_cache_get)
    monkeypatch.setattr(dashboard_module, "set_cache_json", fake_cache_set)

    client = TestClient(app)
    response = client.get("/dashboard/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_items"] == 8
