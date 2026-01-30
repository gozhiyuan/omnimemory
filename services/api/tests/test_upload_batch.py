"""Tests for the batch upload ingest endpoint."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import get_session
from app.main import app
from app.routes import upload as upload_module

from tests.helpers import FakeSession, override_get_session


def test_batch_ingest_queues_items(monkeypatch):
    fake_session = FakeSession()
    tasks = []

    def fake_delay(payload):
        tasks.append(payload)
        return SimpleNamespace(id=f"task-{payload['item_id']}")

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    monkeypatch.setattr(upload_module.process_item, "delay", fake_delay)

    client = TestClient(app)
    response = client.post(
        "/upload/ingest/batch",
        json={
            "items": [
                {
                    "storage_key": "uploads/ui/2025/03/01/test-a.jpg",
                    "item_type": "photo",
                    "content_type": "image/jpeg",
                    "original_filename": "test-a.jpg",
                    "size_bytes": 1234,
                },
                {
                    "storage_key": "uploads/ui/2025/03/01/test-b.jpg",
                    "item_type": "photo",
                    "content_type": "image/jpeg",
                    "original_filename": "test-b.jpg",
                    "size_bytes": 2345,
                },
            ]
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] == 2
    assert payload["failed"] == 0
    assert len(payload["results"]) == 2
    assert all(result["status"] == "queued" for result in payload["results"])
    assert len(tasks) == 2


def test_batch_ingest_rejects_invalid_items(monkeypatch):
    fake_session = FakeSession()
    tasks = []

    def fake_delay(payload):
        tasks.append(payload)
        return SimpleNamespace(id=f"task-{payload['item_id']}")

    settings = get_settings()

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    monkeypatch.setattr(upload_module.process_item, "delay", fake_delay)

    client = TestClient(app)
    response = client.post(
        "/upload/ingest/batch",
        json={
            "items": [
                {
                    "storage_key": "uploads/ui/2025/03/01/too-big.jpg",
                    "item_type": "photo",
                    "content_type": "image/jpeg",
                    "original_filename": "too-big.jpg",
                    "size_bytes": settings.media_max_bytes + 1,
                }
            ]
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] == 0
    assert payload["failed"] == 1
    assert payload["results"][0]["status"] == "rejected"
    assert "max size" in payload["results"][0]["error"]
    assert tasks == []
