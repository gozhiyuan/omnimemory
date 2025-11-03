"""Celery application configuration."""

from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue
from loguru import logger

from .config import get_settings


celery_app = Celery("lifelog")


def configure_celery() -> None:
    settings = get_settings()

    celery_app.conf.update(
        broker_url=settings.redis_url,
        result_backend=settings.redis_url,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        beat_scheduler="celery.beat:PersistentScheduler",
        beat_schedule={},
        task_queues=(
            Queue("default", Exchange("default"), routing_key="default"),
        ),
        task_default_queue="default",
        task_default_exchange="default",
        task_default_routing_key="default",
    )

    celery_app.autodiscover_tasks(["app.tasks"], force=True)
    logger.info("Celery configured with broker {}", settings.redis_url)


configure_celery()


@celery_app.task(name="health.ping")
def ping() -> str:
    """Simple ping task for monitoring."""

    return "pong"

