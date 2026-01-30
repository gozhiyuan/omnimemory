"""Tests for the timeline routes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_session
from app.routes import timeline as timeline_module

from tests.helpers import FakeResult, FakeSession, FakeStorage, override_get_session


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
            FakeResult(scalars=[]),
            FakeResult(rows=[caption_row]),
            FakeResult(scalars=[]),
            FakeResult(rows=[]),
            FakeResult(rows=[]),
            FakeResult(scalars=[]),
            FakeResult(scalars=[]),
        ]
    )

    app.dependency_overrides[get_session] = override_get_session(fake_session)
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
