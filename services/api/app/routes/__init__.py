"""API route registrations."""

from fastapi import APIRouter

from . import agent, chat, dashboard, devices, health, integrations, memory, openclaw, search, settings, storage, timeline, upload


def get_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health.router, prefix="/health", tags=["health"])
    router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
    router.include_router(chat.router, prefix="/chat", tags=["chat"])
    router.include_router(agent.router, prefix="/agent", tags=["agent"])
    router.include_router(memory.router, prefix="/memory", tags=["memory"])
    router.include_router(integrations.router, tags=["integrations"])
    router.include_router(openclaw.router, prefix="/api/openclaw", tags=["openclaw"])
    router.include_router(search.router, prefix="/search", tags=["search"])
    router.include_router(settings.router, tags=["settings"])
    router.include_router(devices.router, prefix="/devices", tags=["devices"])
    router.include_router(storage.router, prefix="/storage", tags=["storage"])
    router.include_router(timeline.router, prefix="/timeline", tags=["timeline"])
    router.include_router(upload.router, prefix="/upload", tags=["upload"])
    return router


__all__ = ["get_api_router"]
