from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_session
from app.routes import timeline as timeline_module


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


class FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return self._results.pop(0)


class FakeStorage:
    def get_presigned_download(self, key: str, _expires_s: int):
        return {"url": f"http://example.test/{key}"}


def test_timeline_groups_items_and_signs_urls(monkeypatch):
    item_id = uuid4()
    captured_at = datetime(2025, 12, 24, 6, 50, 35, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=item_id,
        item_type="photo",
        captured_at=captured_at,
        event_time_utc=captured_at,
        created_at=captured_at,
        processing_status="completed",
        storage_key="uploads/ui/example.png",
        content_type="image/png",
        original_filename="example.png",
    )
    caption_row = SimpleNamespace(item_id=item_id, data={"text": "Example caption"})

    fake_session = FakeSession(
        [
            FakeResult(scalars=[item]),
            FakeResult(rows=[caption_row]),
            FakeResult(scalars=[]),
        ]
    )

    async def override_get_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr(timeline_module, "get_storage_provider", lambda: FakeStorage())

    client = TestClient(app)
    response = client.get("/timeline")
    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["item_count"] == 1
    first_item = payload[0]["items"][0]
    assert UUID(first_item["id"]) == item_id
    assert first_item["caption"] == "Example caption"
    assert first_item["download_url"] == "http://example.test/uploads/ui/example.png"
