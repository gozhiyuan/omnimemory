"""Storage provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Protocol

import httpx
from loguru import logger

from .config import Settings, get_settings


class StorageProvider(Protocol):
    """Interface for generating presigned URLs and managing objects."""

    def get_presigned_upload(self, key: str, content_type: str, expires_s: int) -> Dict[str, str]:
        ...

    def get_presigned_download(self, key: str, expires_s: int) -> Dict[str, str]:
        ...

    def delete(self, key: str) -> None:
        ...

    def fetch(self, key: str) -> bytes:
        ...


@dataclass
class MemoryStorageProvider(StorageProvider):
    """Fallback provider that stores objects in-process (dev/testing)."""

    objects: Dict[str, bytes] = field(default_factory=dict)

    def get_presigned_upload(self, key: str, content_type: str, expires_s: int) -> Dict[str, str]:
        logger.warning("MemoryStorageProvider does not issue presigned URLs; returning key only")
        return {"key": key, "url": "", "headers": {"Content-Type": content_type}}

    def get_presigned_download(self, key: str, expires_s: int) -> Dict[str, str]:
        logger.warning("MemoryStorageProvider does not issue presigned URLs; returning key only")
        return {"key": key, "url": ""}

    def delete(self, key: str) -> None:  # pragma: no cover - no-op
        logger.info("MemoryStorageProvider delete called for key={}", key)

    def fetch(self, key: str) -> bytes:
        logger.info("MemoryStorageProvider fetch called for key={}", key)
        return self.objects.get(key, b"")

    def store(self, key: str, data: bytes) -> None:
        logger.info("MemoryStorageProvider store called for key={} size={}", key, len(data))
        self.objects[key] = data


@dataclass
class SupabaseStorageProvider(StorageProvider):
    """Supabase storage implementation using the REST API."""

    settings: Settings

    def _request(self, method: str, path: str, json: Dict | None = None) -> Dict:
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise RuntimeError("Supabase credentials not configured")

        url = f"{self.settings.supabase_url.rstrip('/')}{path}"
        headers = {
            "apikey": self.settings.supabase_service_role_key,
            "Authorization": f"Bearer {self.settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }
        resp = httpx.request(method, url, headers=headers, json=json, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        logger.debug("Supabase response path={} status={} payload={}", path, resp.status_code, payload)
        return payload

    def get_presigned_upload(self, key: str, content_type: str, expires_s: int) -> Dict[str, str]:
        payload = {
            "contentType": content_type,
            "expiresIn": expires_s,
            "object": key,
        }
        result = self._request(
            "POST",
            f"/storage/v1/object/sign/{self.settings.bucket_originals}?create=true",
            json=payload,
        )
        signed_url = result.get("signedURL")
        return {
            "key": key,
            "url": f"{self.settings.supabase_url}{signed_url}" if signed_url else "",
            "headers": {"Content-Type": content_type},
        }

    def get_presigned_download(self, key: str, expires_s: int) -> Dict[str, str]:
        payload = {"expiresIn": expires_s}
        result = self._request(
            "POST",
            f"/storage/v1/object/sign/{self.settings.bucket_originals}/{key}",
            json=payload,
        )
        signed_url = result.get("signedURL")
        return {
            "key": key,
            "url": f"{self.settings.supabase_url}{signed_url}" if signed_url else "",
        }

    def delete(self, key: str) -> None:
        payload = {"prefixes": [key]}
        self._request(
            "DELETE",
            f"/storage/v1/object/{self.settings.bucket_originals}",
            json=payload,
        )

    def fetch(self, key: str) -> bytes:
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise RuntimeError("Supabase credentials not configured")

        url = (
            f"{self.settings.supabase_url.rstrip('/')}/storage/v1/object/"
            f"{self.settings.bucket_originals}/{key}"
        )
        headers = {
            "apikey": self.settings.supabase_service_role_key,
            "Authorization": f"Bearer {self.settings.supabase_service_role_key}",
        }
        resp = httpx.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content


@lru_cache(maxsize=1)
def get_storage_provider() -> StorageProvider:
    settings = get_settings()
    if settings.storage_provider == "supabase":
        return SupabaseStorageProvider(settings=settings)
    return MemoryStorageProvider()

