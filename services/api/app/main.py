"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .config import get_settings
from .routes import get_api_router


settings = get_settings()
app = FastAPI(title=settings.api_title, version=settings.api_version)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


REQUEST_COUNT = Counter(
    "lifelog_request_total",
    "Number of HTTP requests processed",
    ["method", "endpoint", "http_status"],
)
REQUEST_LATENCY = Histogram(
    "lifelog_request_latency_seconds",
    "Latency of HTTP requests",
    ["method", "endpoint"],
)


@app.middleware("http")
async def metrics_middleware(request, call_next):  # pragma: no cover - instrumentation
    endpoint = request.url.path
    method = request.method
    with REQUEST_LATENCY.labels(method=method, endpoint=endpoint).time():
        response = await call_next(request)
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, http_status=response.status_code).inc()
    return response


@app.get("/metrics")
def metrics() -> Response:
    data = generate_latest()
    return Response(data, media_type=CONTENT_TYPE_LATEST)


app.include_router(get_api_router())


@app.on_event("startup")
async def on_startup():  # pragma: no cover - runtime logging
    logger.info("Starting {} v{}", settings.api_title, settings.api_version)


@app.on_event("shutdown")
async def on_shutdown():  # pragma: no cover - runtime logging
    logger.info("Shutting down {}", settings.api_title)
