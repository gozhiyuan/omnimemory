"""Health and readiness endpoints."""

from fastapi import APIRouter

from ..celery_app import ping
from ..db.session import database_healthcheck


router = APIRouter()


@router.get("/", summary="Health check")
def health() -> dict[str, str]:
    """Return basic health signal."""

    return {"status": "ok"}


@router.get("/celery", summary="Celery health check")
def celery_health() -> dict[str, str]:
    """Dispatch a ping task and return quickly."""

    async_result = ping.delay()
    try:
        response = async_result.get(timeout=2)
    except Exception:  # pragma: no cover - failure path
        return {"status": "unavailable"}
    return {"status": response}


@router.get("/db", summary="Database health check")
async def db_health() -> dict[str, str]:
    """Verify the API can reach the database."""

    try:
        healthy = await database_healthcheck()
    except Exception:  # pragma: no cover - unexpected connectivity failure
        return {"status": "unavailable"}
    return {"status": "ok" if healthy else "unavailable"}
