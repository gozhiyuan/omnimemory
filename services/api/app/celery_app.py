"""Celery application configuration."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
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
        beat_schedule={
            "health-ping": {
                "task": "health.ping",
                "schedule": 60.0,
            },
            "google-photos-sync": {
                "task": "integrations.google_photos.sync",
                "schedule": 3600.0,
            },
            "lifecycle-cleanup": {
                "task": "maintenance.cleanup",
                "schedule": 3600.0,
            },
            "devices-pairing-cleanup": {
                "task": "devices.cleanup_pairing_codes",
                "schedule": 3600.0,
            },
            "weekly-recap": {
                "task": "recaps.weekly",
                "schedule": crontab(day_of_week="sun", hour=9, minute=0),
            },
        },
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
