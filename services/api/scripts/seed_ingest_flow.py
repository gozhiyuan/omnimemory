#!/usr/bin/env python3
"""Utility script to exercise the ingest pipeline end-to-end.

Steps performed:
1. Request a presigned upload URL from the FastAPI service.
2. Upload the provided asset to the object store using the signed URL.
3. Call the /upload/ingest endpoint so the Celery worker picks up the item.
4. Poll Postgres until the item is processed, then report row counts from
   source_items and processed_content to validate the flow.

Usage example:
    uv run python scripts/seed_ingest_flow.py ./fixtures/sample.jpg \
        --api-url http://localhost:8000 \
        --postgres-dsn postgresql://lifelog:lifelog@localhost:5432/lifelog
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import os
from pathlib import Path
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote

import asyncpg
import httpx


DEFAULT_TEST_USER_ID = "00000000-0000-0000-0000-000000000001"
ITEM_TYPES = ("photo", "video", "audio", "document")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_env_defaults() -> None:
    candidates = [
        Path(".env"),
        Path(".env.dev"),  # Legacy fallback
        Path("../.env"),
        Path("../.env.dev"),
        Path("../../.env"),
        Path("../../.env.dev"),
        Path("../../../.env"),
        Path("../../../.env.dev"),
    ]
    for candidate in candidates:
        load_env_file(candidate)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the ingest pipeline with a manual upload.")
    parser.add_argument("file", type=Path, help="Path to the asset to upload.")
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running FastAPI service (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--postgres-dsn",
        default="postgresql://lifelog:lifelog@localhost:5432/lifelog",
        help="Postgres DSN used for verification queries.",
    )
    parser.add_argument(
        "--user-id",
        default=DEFAULT_TEST_USER_ID,
        help=f"User UUID to associate with the upload (default: {DEFAULT_TEST_USER_ID}).",
    )
    parser.add_argument(
        "--item-type",
        choices=ITEM_TYPES,
        default="photo",
        help="Item type passed to /upload/ingest.",
    )
    parser.add_argument(
        "--prefix",
        default="uploads/seed-flow",
        help="Object storage prefix for the uploaded file.",
    )
    parser.add_argument(
        "--captured-at",
        default=None,
        help="ISO 8601 timestamp for captured_at (default: omitted).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Seconds to wait for processing to complete before failing.",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=30.0,
        help="HTTP client timeout in seconds for API/storage requests.",
    )
    parser.add_argument(
        "--direct-upload",
        action="store_true",
        help="Upload directly to Supabase using the service role key instead of presigned URLs.",
    )
    return parser.parse_args()


async def request_upload_url(
    api_client: httpx.AsyncClient, filename: str, content_type: str, prefix: str
) -> Dict[str, Any]:
    payload = {"filename": filename, "content_type": content_type, "prefix": prefix}
    resp = await api_client.post("/storage/upload-url", json=payload)
    resp.raise_for_status()
    return resp.json()


async def upload_bytes(upload_meta: Dict[str, Any], data: bytes, content_type: str, timeout: float) -> None:
    url = upload_meta.get("url")
    if not url:
        raise RuntimeError("Upload URL not provided by storage provider; configure Supabase for this script.")

    headers = upload_meta.get("headers", {}).copy()
    headers.setdefault("Content-Type", content_type)

    async with httpx.AsyncClient(timeout=timeout) as storage_client:
        response = await storage_client.put(url, content=data, headers=headers)
        response.raise_for_status()


async def upload_bytes_supabase(
    key: str,
    data: bytes,
    content_type: str,
    timeout: float,
) -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    bucket = (os.environ.get("BUCKET_ORIGINALS") or "originals").strip()
    if not supabase_url or not service_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for direct Supabase uploads")

    object_path = quote(key.lstrip("/"), safe="/")
    url = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=timeout) as storage_client:
        response = await storage_client.post(url, content=data, headers=headers)
        response.raise_for_status()


async def trigger_ingest(
    api_client: httpx.AsyncClient,
    storage_key: str,
    item_type: str,
    user_id: str,
    content_type: str,
    original_filename: str,
    captured_at: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "storage_key": storage_key,
        "item_type": item_type,
        "user_id": user_id,
        "content_type": content_type,
        "original_filename": original_filename,
    }
    if captured_at:
        payload["captured_at"] = captured_at

    resp = await api_client.post("/upload/ingest", json=payload)
    resp.raise_for_status()
    return resp.json()


async def wait_for_completion(pool: asyncpg.pool.Pool, item_id: uuid.UUID, timeout: int) -> Dict[str, Any]:
    deadline = time.time() + timeout
    query = """
        SELECT processing_status, processing_error, processed_at
        FROM source_items
        WHERE id = $1
    """

    while time.time() < deadline:
        row = await pool.fetchrow(query, item_id)
        if row and row["processing_status"] in ("completed", "failed"):
            return dict(row)
        await asyncio.sleep(2)

    raise TimeoutError("Timed out waiting for process_item to finish")


async def collect_counts(pool: asyncpg.pool.Pool) -> Dict[str, Any]:
    counts = await pool.fetch("SELECT processing_status, COUNT(*) FROM source_items GROUP BY processing_status")
    content_counts = await pool.fetch("SELECT content_role, COUNT(*) FROM processed_content GROUP BY content_role")
    return {
        "source_items": {row["processing_status"]: row["count"] for row in counts},
        "processed_content": {row["content_role"]: row["count"] for row in content_counts},
    }


async def main() -> None:
    load_env_defaults()
    args = parse_args()
    if not args.file.exists():
        raise SystemExit(f"File not found: {args.file}")

    file_bytes = args.file.read_bytes()
    content_type = mimetypes.guess_type(args.file.name)[0] or "application/octet-stream"

    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=args.http_timeout) as api_client:
        if args.direct_upload:
            storage_key = f"{args.prefix}/{uuid.uuid4()}-{args.file.name}"
            print(f"Uploading {args.file.stat().st_size} bytes to Supabase as {storage_key} …")
            await upload_bytes_supabase(storage_key, file_bytes, content_type, args.http_timeout)
        else:
            print(f"Requesting upload URL for {args.file} …")
            try:
                upload_meta = await request_upload_url(api_client, args.file.name, content_type, args.prefix)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 501:
                    raise RuntimeError(
                        "Presigned uploads are unavailable; re-run with --direct-upload if needed."
                    ) from exc
                raise

            print(f"Uploading {args.file.stat().st_size} bytes to storage …")
            await upload_bytes(upload_meta, file_bytes, content_type, args.http_timeout)
            storage_key = upload_meta["key"]

        print("Enqueuing ingest task …")
        ingest_resp = await trigger_ingest(
            api_client,
            storage_key=storage_key,
            item_type=args.item_type,
            user_id=args.user_id,
            content_type=content_type,
            original_filename=args.file.name,
            captured_at=args.captured_at,
        )

    item_id = uuid.UUID(ingest_resp["item_id"])
    print(f"Ingest queued: item_id={ingest_resp['item_id']} task_id={ingest_resp['task_id']}")

    pool = await asyncpg.create_pool(dsn=args.postgres_dsn)
    try:
        status_row = await wait_for_completion(pool, item_id, args.timeout)
        print(
            f"Item status: {status_row['processing_status']} "
            f"(processed_at={status_row['processed_at']} error={status_row['processing_error']})"
        )

        counts = await collect_counts(pool)
        print("source_items counts:", counts["source_items"])
        print("processed_content counts:", counts["processed_content"])
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
