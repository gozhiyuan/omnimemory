import os

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "lifelog")
os.environ.setdefault("POSTGRES_USER", "lifelog")
os.environ.setdefault("POSTGRES_PASSWORD", "lifelog")

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_ok():
    client = TestClient(app)
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
