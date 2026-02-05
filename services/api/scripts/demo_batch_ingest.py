#!/usr/bin/env python3
"""Batch ingest demo media with realistic timestamps.

This script:
1) Uploads local files to object storage (presigned URL or direct Supabase).
2) Generates captured_at timestamps across the last N weeks with weekday/weekend patterns.
3) Calls /upload/ingest/batch to enqueue processing.

Example:
  uv run python scripts/demo_batch_ingest.py ./demo_media \
    --api-url http://127.0.0.1:8000 \
    --weeks 4 --limit 50 --seed 42
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import mimetypes
import os
from pathlib import Path
import random
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

import httpx


DEFAULT_TEST_USER_ID = "00000000-0000-0000-0000-000000000001"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
ITEM_TYPES = ("photo", "video")

EXT_CONTENT_TYPES = {
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
}


KEYWORD_CATEGORIES = {
    "commute": {"commute", "commuting", "train", "bus", "subway", "metro", "bike", "bicycle", "car", "traffic"},
    "work": {"work", "office", "desk", "laptop", "meeting", "cowork", "coworking"},
    "eat": {
        "food",
        "eat",
        "lunch",
        "dinner",
        "breakfast",
        "brunch",
        "coffee",
        "restaurant",
        "cafe",
        "kitchen",
    },
    "dog": {"dog", "puppy", "pet", "walk", "park", "leash"},
    "nightlife": {"night", "club", "bar", "party", "concert", "drinks"},
    "hiking": {"hike", "hiking", "trail", "mountain", "forest", "camp"},
    "social": {"friends", "social", "group", "hangout", "picnic", "family"},
}


CATEGORY_DAY_WEIGHTS = {
    "commute": 0.9,
    "work": 0.9,
    "eat": 0.6,
    "dog": 0.6,
    "nightlife": 0.25,
    "hiking": 0.1,
    "social": 0.4,
    "misc": 0.6,
}

# Time windows by category and day type (start_hour, end_hour).
TIME_WINDOWS = {
    "commute": {
        "weekday": [(7, 9), (17, 19)],
        "weekend": [(10, 12)],
    },
    "work": {
        "weekday": [(9, 12), (13, 17)],
        "weekend": [(10, 13)],
    },
    "eat": {
        "weekday": [(7, 9), (12, 13), (18, 20)],
        "weekend": [(9, 11), (12, 14), (18, 21)],
    },
    "dog": {
        "weekday": [(7, 9), (18, 21)],
        "weekend": [(9, 11), (17, 20)],
    },
    "nightlife": {
        "weekday": [(20, 23)],
        "weekend": [(20, 24)],
    },
    "hiking": {
        "weekday": [(7, 10)],
        "weekend": [(8, 14)],
    },
    "social": {
        "weekday": [(18, 22)],
        "weekend": [(12, 22)],
    },
    "misc": {
        "weekday": [(9, 19)],
        "weekend": [(10, 18)],
    },
}


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
        Path(".env.dev"),
        Path("../.env"),
        Path("../.env.dev"),
        Path("../../.env"),
        Path("../../.env.dev"),
        Path("../../../.env"),
        Path("../../../.env.dev"),
    ]
    for candidate in candidates:
        load_env_file(candidate)


@dataclass
class PlannedItem:
    file: Path
    item_type: str
    content_type: str
    captured_at: datetime
    category: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch ingest demo media with timestamps.")
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Files or folders containing media assets.",
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running FastAPI service (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--user-id",
        default=DEFAULT_TEST_USER_ID,
        help=f"User UUID to associate with the upload (default: {DEFAULT_TEST_USER_ID}).",
    )
    parser.add_argument(
        "--prefix",
        default="uploads/demo",
        help="Object storage prefix for the uploaded files.",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=4,
        help="How many weeks back to spread the demo timestamps (default: 4).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of files to ingest (default: 50).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible schedules.",
    )
    parser.add_argument(
        "--provider",
        default="demo",
        help="Provider label stored on source_items (default: demo).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="How many items to enqueue per /upload/ingest/batch call (default: 100).",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Bearer token if AUTH_ENABLED=true (optional).",
    )
    parser.add_argument(
        "--event-time-override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Force captured_at to be used as event time (default: true).",
    )
    parser.add_argument(
        "--tz-offset-minutes",
        type=int,
        default=None,
        help="Timezone offset in minutes (JS Date.getTimezoneOffset). Default uses local timezone.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned timestamps without uploading.",
    )
    parser.add_argument(
        "--direct-upload",
        action="store_true",
        help="Upload directly to Supabase using the service role key instead of presigned URLs.",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=30.0,
        help="HTTP client timeout in seconds for API/storage requests.",
    )
    return parser.parse_args()


def gather_files(paths: Sequence[Path]) -> List[Path]:
    collected: List[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file():
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        collected.append(candidate)
                        seen.add(resolved)
        elif path.is_file():
            resolved = path.resolve()
            if resolved not in seen:
                collected.append(path)
                seen.add(resolved)
    return sorted(collected, key=lambda p: p.as_posix())


def classify_item(path: Path) -> Optional[Tuple[str, str]]:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        item_type = "video"
    elif ext in PHOTO_EXTS:
        item_type = "photo"
    else:
        return None
    content_type = mimetypes.guess_type(path.name)[0] or EXT_CONTENT_TYPES.get(ext) or "application/octet-stream"
    return item_type, content_type


def guess_category(path: Path) -> str:
    tokens = [path.stem.lower()]
    tokens.extend(part.lower() for part in path.parts)
    for category, keywords in KEYWORD_CATEGORIES.items():
        if any(token in keywords for token in tokens):
            return category
    return "misc"


def build_date_buckets(today: date, weeks: int) -> Tuple[List[date], List[date]]:
    start = today - timedelta(days=weeks * 7 - 1)
    weekdays: List[date] = []
    weekends: List[date] = []
    cursor = start
    while cursor <= today:
        if cursor.weekday() < 5:
            weekdays.append(cursor)
        else:
            weekends.append(cursor)
        cursor += timedelta(days=1)
    return weekdays, weekends


def choose_day(category: str, weekdays: Sequence[date], weekends: Sequence[date]) -> date:
    weekday_weight = CATEGORY_DAY_WEIGHTS.get(category, CATEGORY_DAY_WEIGHTS["misc"])
    if random.random() < weekday_weight and weekdays:
        return random.choice(weekdays)
    if weekends:
        return random.choice(weekends)
    return random.choice(weekdays)


def choose_time(category: str, day_type: str) -> time:
    windows = TIME_WINDOWS.get(category, TIME_WINDOWS["misc"]).get(day_type, TIME_WINDOWS["misc"][day_type])
    start_hour, end_hour = random.choice(windows)
    if end_hour >= 24:
        end_hour = 23
    hour = random.randint(start_hour, max(start_hour, end_hour - 1))
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return time(hour=hour, minute=minute, second=second)


def build_timezone(offset_minutes: Optional[int]) -> Tuple[timezone, int]:
    if offset_minutes is None:
        local_dt = datetime.now().astimezone()
        tzinfo = local_dt.tzinfo or timezone.utc
        offset = tzinfo.utcoffset(local_dt) or timedelta()
        js_offset = int(-offset.total_seconds() // 60)
        return tzinfo, js_offset
    tzinfo = timezone(timedelta(minutes=-offset_minutes))
    return tzinfo, offset_minutes


def plan_items(
    files: Sequence[Path],
    weeks: int,
    limit: int,
    seed: Optional[int],
    tzinfo: timezone,
) -> List[PlannedItem]:
    if seed is not None:
        random.seed(seed)
    eligible: List[PlannedItem] = []
    today = datetime.now(tzinfo).date()
    weekdays, weekends = build_date_buckets(today, weeks)

    for path in files:
        classified = classify_item(path)
        if not classified:
            continue
        item_type, content_type = classified
        category = guess_category(path)
        day = choose_day(category, weekdays, weekends)
        day_type = "weekday" if day.weekday() < 5 else "weekend"
        chosen_time = choose_time(category, day_type)
        captured_at = datetime.combine(day, chosen_time, tzinfo=tzinfo)
        eligible.append(
            PlannedItem(
                file=path,
                item_type=item_type,
                content_type=content_type,
                captured_at=captured_at,
                category=category,
            )
        )

    if not eligible:
        return []

    if len(eligible) > limit:
        eligible = random.sample(eligible, k=limit)

    return sorted(eligible, key=lambda item: item.captured_at)


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


async def upload_bytes_supabase(key: str, data: bytes, content_type: str, timeout: float) -> None:
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


def chunked(items: Sequence[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


async def main() -> None:
    load_env_defaults()
    args = parse_args()

    tzinfo, js_offset = build_timezone(args.tz_offset_minutes)
    files = gather_files(args.paths)
    planned = plan_items(files, args.weeks, args.limit, args.seed, tzinfo)

    if not planned:
        raise SystemExit("No supported media files found.")

    if args.dry_run:
        print(f"Planned {len(planned)} items:")
        for item in planned:
            print(
                f"- {item.captured_at.isoformat()} [{item.item_type}] {item.category}: {item.file.as_posix()}"
            )
        return

    headers: Dict[str, str] = {}
    if args.auth_token:
        headers["Authorization"] = f"Bearer {args.auth_token}"

    ingest_items: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=args.http_timeout, headers=headers) as api_client:
        for item in planned:
            file_bytes = item.file.read_bytes()
            if args.direct_upload:
                storage_key = f"{args.prefix}/{uuid.uuid4()}-{item.file.name}"
                print(f"Uploading {item.file.name} ({len(file_bytes)} bytes) -> {storage_key}")
                await upload_bytes_supabase(storage_key, file_bytes, item.content_type, args.http_timeout)
            else:
                upload_meta = await request_upload_url(api_client, item.file.name, item.content_type, args.prefix)
                await upload_bytes(upload_meta, file_bytes, item.content_type, args.http_timeout)
                storage_key = upload_meta["key"]

            ingest_items.append(
                {
                    "storage_key": storage_key,
                    "item_type": item.item_type,
                    "provider": args.provider,
                    "captured_at": item.captured_at.isoformat(),
                    "content_type": item.content_type,
                    "original_filename": item.file.name,
                    "size_bytes": item.file.stat().st_size,
                    "client_tz_offset_minutes": js_offset,
                    "event_time_override": args.event_time_override,
                }
            )

        accepted = 0
        failed = 0
        for batch in chunked(ingest_items, args.batch_size):
            resp = await api_client.post("/upload/ingest/batch", json={"items": batch})
            resp.raise_for_status()
            data = resp.json()
            accepted += data.get("accepted", 0)
            failed += data.get("failed", 0)
            print(f"Batch queued: accepted={data.get('accepted')} failed={data.get('failed')}")

        print(f"Done. Total queued={accepted}, failed={failed}.")


if __name__ == "__main__":
    asyncio.run(main())
