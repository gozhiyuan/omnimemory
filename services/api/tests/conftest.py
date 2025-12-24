import os

import pytest

from app.main import app


os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "lifelog")
os.environ.setdefault("POSTGRES_USER", "lifelog")
os.environ.setdefault("POSTGRES_PASSWORD", "lifelog")


@pytest.fixture(autouse=True)
def reset_dependency_overrides():
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}
