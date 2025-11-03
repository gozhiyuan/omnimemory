"""API route registrations."""

from fastapi import APIRouter

from . import health, search, storage, upload


def get_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health.router, prefix="/health", tags=["health"])
    router.include_router(search.router, prefix="/search", tags=["search"])
    router.include_router(storage.router, prefix="/storage", tags=["storage"])
    router.include_router(upload.router, prefix="/upload", tags=["upload"])
    return router


__all__ = ["get_api_router"]

