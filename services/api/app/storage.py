"""Storage provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Protocol
from urllib.parse import quote

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

    def _encode_object_key(self, key: str) -> str:
        return quote(key.lstrip("/"), safe="/")

    def _storage_base_url(self) -> str:
        return f"{str(self.settings.supabase_url).rstrip('/')}/storage/v1"

    def _build_storage_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith("/storage/v1/"):
            return f"{str(self.settings.supabase_url).rstrip('/')}{path}"
        base_url = self._storage_base_url()
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base_url}{path}"

    def _extract_signed_url(self, result: object) -> str:
        if isinstance(result, dict):
            return (
                result.get("signedURL")
                or result.get("signedUrl")
                or result.get("signed_url")
                or result.get("url")
                or ""
            )
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                return (
                    first.get("signedURL")
                    or first.get("signedUrl")
                    or first.get("signed_url")
                    or first.get("url")
                    or ""
                )
        return ""

    def _request_raw(
        self,
        method: str,
        path: str,
        json: Dict | None = None,
        headers: Dict[str, str] | None = None,
    ) -> httpx.Response:
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise RuntimeError("Supabase credentials not configured")

        base_url = str(self.settings.supabase_url).rstrip("/")
        url = f"{base_url}{path}"
        request_headers = {
            "apikey": self.settings.supabase_service_role_key,
            "Authorization": f"Bearer {self.settings.supabase_service_role_key}",
        }
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        resp = httpx.request(method, url, headers=request_headers, json=json, timeout=10)
        if resp.status_code >= 400:
            logger.error(
                "Supabase request failed method={} path={} status={} body={}",
                method,
                path,
                resp.status_code,
                resp.text,
            )
        return resp

    def _request(self, method: str, path: str, json: Dict | None = None) -> Dict:
        resp = self._request_raw(method, path, json=json)
        resp.raise_for_status()
        payload = resp.json()
        logger.debug("Supabase response path={} status={} payload={}", path, resp.status_code, payload)
        return payload

    def get_presigned_upload(self, key: str, content_type: str, expires_s: int) -> Dict[str, str]:
        bucket = self.settings.bucket_originals
        object_path = self._encode_object_key(key)
        path = f"/storage/v1/object/upload/sign/{bucket}/{object_path}"
        resp = self._request_raw("POST", path, headers={"x-upsert": "true"})
        resp.raise_for_status()
        result = resp.json()
        signed_url = self._extract_signed_url(result)
        if signed_url:
            full_url = self._build_storage_url(signed_url)
            return {
                "key": key,
                "url": full_url,
                "headers": {
                    "Content-Type": content_type,
                    "x-upsert": "true",
                    "cache-control": "3600",
                },
            }
        logger.warning("Supabase upload signing returned no URL: {}", result)
        return {
            "key": key,
            "url": "",
            "headers": {"Content-Type": content_type},
        }

    def get_presigned_download(self, key: str, expires_s: int) -> Dict[str, str]:
        payload = {"expiresIn": str(expires_s)}
        object_path = self._encode_object_key(key)
        path = f"/storage/v1/object/sign/{self.settings.bucket_originals}/{object_path}"
        result = self._request("POST", path, json=payload)
        signed_url = self._extract_signed_url(result)
        return {
            "key": key,
            "url": self._build_storage_url(signed_url) if signed_url else "",
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

        object_path = self._encode_object_key(key)
        base_url = str(self.settings.supabase_url).rstrip("/")
        url = f"{base_url}/storage/v1/object/{self.settings.bucket_originals}/{object_path}"
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
