"""Pipeline runner orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import SourceItem
from ..storage import get_storage_provider
from .steps import get_pipeline_steps
from .types import ArtifactStore, PipelineArtifacts, PipelineConfig


async def run_pipeline(
    session: AsyncSession,
    item: SourceItem,
    payload: Dict[str, Any],
) -> PipelineArtifacts:
    settings = get_settings()
    storage = get_storage_provider()
    config = PipelineConfig(
        session=session,
        storage=storage,
        settings=settings,
        payload=payload,
        now=datetime.now(timezone.utc),
    )
    artifacts = PipelineArtifacts(ArtifactStore(session, item))
    for step in get_pipeline_steps(item.item_type):
        if artifacts.skip_expensive and step.is_expensive:
            continue
        await step.run(item, artifacts, config)
        await session.commit()
    return artifacts
