"""Storage provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Protocol
from urllib.parse import quote

try:  # Optional dependency for S3-compatible storage.
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - handled at runtime if S3 provider is used.
    boto3 = None
    BotoConfig = None
    ClientError = None

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

    def store(self, key: str, data: bytes, content_type: str) -> None:
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

    def store(self, key: str, data: bytes, content_type: str) -> None:
        logger.info("MemoryStorageProvider store called for key={} size={}", key, len(data))
        self.objects[key] = data


@dataclass
class S3StorageProvider(StorageProvider):
    """S3-compatible storage implementation (RustFS/MinIO/AWS)."""

    settings: Settings
    client: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if boto3 is None or BotoConfig is None:
            raise RuntimeError("boto3 is required for S3 storage; install boto3")

        if self.settings.s3_endpoint_url and (
            not self.settings.s3_access_key_id or not self.settings.s3_secret_access_key
        ):
            raise RuntimeError("S3 endpoint requires access key and secret access key")

        addressing_style = "path" if self.settings.s3_force_path_style else "virtual"
        config = BotoConfig(s3={"addressing_style": addressing_style})
        client_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": self.settings.s3_region,
            "endpoint_url": str(self.settings.s3_endpoint_url)
            if self.settings.s3_endpoint_url
            else None,
            "config": config,
        }
        if self.settings.s3_access_key_id and self.settings.s3_secret_access_key:
            client_kwargs["aws_access_key_id"] = self.settings.s3_access_key_id
            client_kwargs["aws_secret_access_key"] = self.settings.s3_secret_access_key
        self.client = boto3.client(**client_kwargs)

    def _bucket(self) -> str:
        return self.settings.bucket_originals

    def get_presigned_upload(self, key: str, content_type: str, expires_s: int) -> Dict[str, str]:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket(), "Key": key, "ContentType": content_type},
            ExpiresIn=expires_s,
        )
        return {"key": key, "url": url, "headers": {"Content-Type": content_type}}

    def get_presigned_download(self, key: str, expires_s: int) -> Dict[str, str]:
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket(), "Key": key},
            ExpiresIn=expires_s,
        )
        return {"key": key, "url": url}

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self._bucket(), Key=key)
        except ClientError as exc:
            logger.error("S3 delete failed key={} error={}", key, exc)
            raise

    def fetch(self, key: str) -> bytes:
        try:
            resp = self.client.get_object(Bucket=self._bucket(), Key=key)
        except ClientError as exc:
            logger.error("S3 fetch failed key={} error={}", key, exc)
            raise
        body = resp.get("Body")
        return body.read() if body else b""

    def store(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self.client.put_object(
                Bucket=self._bucket(),
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except ClientError as exc:
            logger.error("S3 store failed key={} error={}", key, exc)
            raise


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

    def store(self, key: str, data: bytes, content_type: str) -> None:
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise RuntimeError("Supabase credentials not configured")

        object_path = self._encode_object_key(key)
        base_url = str(self.settings.supabase_url).rstrip("/")
        url = f"{base_url}/storage/v1/object/{self.settings.bucket_originals}/{object_path}"
        headers = {
            "apikey": self.settings.supabase_service_role_key,
            "Authorization": f"Bearer {self.settings.supabase_service_role_key}",
            "Content-Type": content_type,
            "x-upsert": "true",
        }
        resp = httpx.post(url, headers=headers, content=data, timeout=60)
        if resp.status_code >= 400:
            logger.error(
                "Supabase upload failed status={} body={}", resp.status_code, resp.text
            )
        resp.raise_for_status()


@lru_cache(maxsize=1)
def get_storage_provider() -> StorageProvider:
    settings = get_settings()
    if settings.storage_provider == "supabase":
        return SupabaseStorageProvider(settings=settings)
    if settings.storage_provider == "s3":
        return S3StorageProvider(settings=settings)
    return MemoryStorageProvider()
