"""Tests for search routes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.auth import get_current_user_id
from app.db.session import get_session
from app.routes import search as search_module

from tests.helpers import FakeResult, FakeSession, override_get_session, override_current_user_id


TEST_USER_ID = UUID("12345678-1234-5678-1234-567812345678")


def _make_search_result(context_id: UUID, score: float = 0.85, is_episode: bool = False):
    """Helper to create a search result dict."""
    return {
        "context_id": str(context_id),
        "score": score,
        "payload": {
            "is_episode": is_episode,
            "event_time_utc": "2025-06-15T10:00:00Z",
        }
    }


def _make_context(
    context_id: UUID,
    title: str = "Test Context",
    summary: str = "A test summary",
    context_type: str = "caption",
):
    """Helper to create a ProcessedContext-like object."""
    return SimpleNamespace(
        id=context_id,
        title=title,
        summary=summary,
        context_type=context_type,
        event_time_utc=datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        source_item_ids=[uuid4()],
    )


# ---------------------------------------------------------------------------
# GET /search/ tests
# ---------------------------------------------------------------------------


def test_search_basic(monkeypatch):
    """Basic search returns results."""
    context_id = uuid4()
    context = _make_context(context_id, title="Beach Photo", summary="Sunset at the beach")

    def fake_search_contexts(*args, **kwargs):
        return [_make_search_result(context_id, is_episode=True)]

    fake_session = FakeSession([FakeResult(scalars=[context])])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get("/search/", params={"q": "beach sunset"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "beach sunset"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["title"] == "Beach Photo"


def test_search_empty_results(monkeypatch):
    """Search with no matches returns empty results."""
    def fake_search_contexts(*args, **kwargs):
        return []

    fake_session = FakeSession([])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get("/search/", params={"q": "nonexistent query"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "nonexistent query"
    assert payload["results"] == []


def test_search_with_date_filter(monkeypatch):
    """Search with date filter."""
    context_id = uuid4()
    context = _make_context(context_id)

    def fake_search_contexts(*args, **kwargs):
        # Verify date filters are passed
        assert kwargs.get("start_time") is not None
        assert kwargs.get("end_time") is not None
        return [_make_search_result(context_id, is_episode=True)]

    fake_session = FakeSession([FakeResult(scalars=[context])])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get(
        "/search/",
        params={"q": "test", "start_date": "2025-01-01", "end_date": "2025-01-31"}
    )

    assert response.status_code == 200


def test_search_with_limit(monkeypatch):
    """Search respects limit parameter."""
    contexts = [_make_context(uuid4(), title=f"Context {i}") for i in range(10)]
    results = [_make_search_result(c.id, is_episode=True) for c in contexts]

    def fake_search_contexts(*args, **kwargs):
        limit = kwargs.get("limit", 5)
        return results[:limit]

    fake_session = FakeSession([FakeResult(scalars=contexts[:3])])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get("/search/", params={"q": "test", "limit": 3})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) <= 3


def test_search_with_provider_filter(monkeypatch):
    """Search with provider filter."""
    context_id = uuid4()
    source_item_id = uuid4()
    context = SimpleNamespace(
        id=context_id,
        title="Google Photos Item",
        summary="From Google Photos",
        context_type="caption",
        event_time_utc=datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        source_item_ids=[source_item_id],
    )

    def fake_search_contexts(*args, **kwargs):
        return [_make_search_result(context_id, is_episode=True)]

    # First query gets contexts, second gets source items filtered by provider
    fake_session = FakeSession([
        FakeResult(scalars=[context]),
        FakeResult(rows=[(source_item_id,)]),
    ])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get(
        "/search/",
        params={"q": "photos", "provider": "google_photos"}
    )

    assert response.status_code == 200


def test_search_fallback_to_non_episodes(monkeypatch):
    """Search falls back to non-episode results when episode results are insufficient."""
    episode_context_id = uuid4()
    regular_context_id = uuid4()
    episode_context = _make_context(episode_context_id, title="Episode")
    regular_context = _make_context(regular_context_id, title="Regular")

    call_count = [0]

    def fake_search_contexts(*args, **kwargs):
        call_count[0] += 1
        if kwargs.get("is_episode") is True:
            # First call for episodes returns only 1 result
            return [_make_search_result(episode_context_id, is_episode=True)]
        else:
            # Fallback call returns both
            return [
                _make_search_result(episode_context_id, is_episode=True),
                _make_search_result(regular_context_id, is_episode=False),
            ]

    fake_session = FakeSession([
        FakeResult(scalars=[episode_context, regular_context]),
    ])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get("/search/", params={"q": "test", "limit": 5})

    assert response.status_code == 200
    # Should have made at least 2 calls (episode search + fallback)
    assert call_count[0] >= 2


def test_search_enriches_results_with_db_data(monkeypatch):
    """Search results are enriched with database context data."""
    context_id = uuid4()
    context = _make_context(
        context_id,
        title="My Trip",
        summary="A wonderful vacation",
        context_type="episode",
    )

    def fake_search_contexts(*args, **kwargs):
        return [_make_search_result(context_id, is_episode=True)]

    fake_session = FakeSession([FakeResult(scalars=[context])])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get("/search/", params={"q": "trip vacation"})

    assert response.status_code == 200
    payload = response.json()
    result = payload["results"][0]
    assert result["title"] == "My Trip"
    assert result["summary"] == "A wonderful vacation"
    assert result["context_type"] == "episode"
    assert result["event_time_utc"] is not None


def test_search_handles_invalid_context_ids(monkeypatch):
    """Search handles results with invalid context IDs gracefully."""
    def fake_search_contexts(*args, **kwargs):
        return [
            {"context_id": "not-a-uuid", "score": 0.5, "payload": {}},
            {"context_id": None, "score": 0.4, "payload": {}},
        ]

    fake_session = FakeSession([FakeResult(scalars=[])])

    app.dependency_overrides[get_session] = override_get_session(fake_session)
    app.dependency_overrides[get_current_user_id] = override_current_user_id(TEST_USER_ID)
    monkeypatch.setattr(search_module, "search_contexts", fake_search_contexts)

    client = TestClient(app)
    response = client.get("/search/", params={"q": "test"})

    # Should not crash
    assert response.status_code == 200
