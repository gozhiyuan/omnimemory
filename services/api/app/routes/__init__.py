"""API route registrations."""

from fastapi import APIRouter

from . import dashboard, google_photos, health, search, storage, timeline, upload


def get_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health.router, prefix="/health", tags=["health"])
    router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
    router.include_router(search.router, prefix="/search", tags=["search"])
    router.include_router(storage.router, prefix="/storage", tags=["storage"])
    router.include_router(timeline.router, prefix="/timeline", tags=["timeline"])
    router.include_router(upload.router, prefix="/upload", tags=["upload"])
    router.include_router(
        google_photos.router,
        prefix="/connections/google-photos",
        tags=["google-photos"],
    )
    return router


__all__ = ["get_api_router"]
