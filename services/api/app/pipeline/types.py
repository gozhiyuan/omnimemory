"""Shared pipeline types and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Protocol, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import DerivedArtifact, SourceItem
from ..storage import StorageProvider


@dataclass
class PipelineConfig:
    session: AsyncSession
    storage: StorageProvider
    settings: Settings
    payload: Dict[str, Any]
    now: datetime


class ArtifactStore:
    """Lookup and persist derived artifacts with caching."""

    def __init__(self, session: AsyncSession, item: SourceItem) -> None:
        self._session = session
        self._item = item
        self._cache: dict[Tuple[str, str, str, str], Optional[DerivedArtifact]] = {}

    async def get(
        self,
        artifact_type: str,
        producer: str,
        producer_version: str,
        input_fingerprint: str,
    ) -> Optional[DerivedArtifact]:
        key = (artifact_type, producer, producer_version, input_fingerprint)
        if key in self._cache:
            return self._cache[key]
        stmt = select(DerivedArtifact).where(
            DerivedArtifact.source_item_id == self._item.id,
            DerivedArtifact.artifact_type == artifact_type,
            DerivedArtifact.producer == producer,
            DerivedArtifact.producer_version == producer_version,
            DerivedArtifact.input_fingerprint == input_fingerprint,
        )
        result = await self._session.execute(stmt)
        artifact = result.scalar_one_or_none()
        self._cache[key] = artifact
        return artifact

    async def upsert(
        self,
        artifact_type: str,
        producer: str,
        producer_version: str,
        input_fingerprint: str,
        payload: Dict[str, Any],
        storage_key: Optional[str] = None,
    ) -> tuple[DerivedArtifact, bool]:
        existing = await self.get(artifact_type, producer, producer_version, input_fingerprint)
        if existing:
            return existing, False
        artifact = DerivedArtifact(
            user_id=self._item.user_id,
            source_item_id=self._item.id,
            artifact_type=artifact_type,
            producer=producer,
            producer_version=producer_version,
            input_fingerprint=input_fingerprint,
            payload=payload,
            storage_key=storage_key,
        )
        self._session.add(artifact)
        await self._session.flush()
        key = (artifact_type, producer, producer_version, input_fingerprint)
        self._cache[key] = artifact
        return artifact, True


class PipelineArtifacts:
    """Runtime state for a pipeline run."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store
        self.data: dict[str, Any] = {}
        self.skip_expensive = False

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


class PipelineStep(Protocol):
    name: str
    version: str
    is_expensive: bool

    async def run(self, item: SourceItem, artifacts: PipelineArtifacts, config: PipelineConfig) -> None:
        ...
