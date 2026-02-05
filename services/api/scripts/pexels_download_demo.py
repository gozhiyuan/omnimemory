#!/usr/bin/env python3
"""Download demo media from the Pexels API and organize into folders."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx


API_BASE = "https://api.pexels.com"
DEFAULT_CATEGORIES = [
    "commute",
    "work",
    "eat",
    "dog",
    "nightlife",
    "hiking",
    "social",
]

CATEGORY_QUERIES: Dict[str, List[str]] = {
    "commute": ["commute", "train station", "subway", "bus", "cycling to work", "traffic"],
    "work": ["office", "coworking", "working on laptop", "team meeting", "desk workspace"],
    "eat": ["coffee", "breakfast", "lunch", "dinner", "restaurant", "cafe"],
    "dog": ["dog walking", "dog park", "puppy", "pet dog"],
    "nightlife": ["nightlife", "bar", "concert", "city night", "club"],
    "hiking": ["hiking", "trail", "mountain trail", "forest hike", "outdoor adventure"],
    "social": ["friends hanging out", "group of friends", "picnic", "social gathering", "family time"],
}

PHOTO_SIZES = {"original", "large2x", "large", "medium", "small", "portrait", "landscape", "tiny"}
ORIENTATIONS = {"landscape", "portrait", "square"}
VIDEO_QUALITIES = {"sd", "hd", "any"}


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download demo media from Pexels.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PEXELS_API_KEY"),
        help="Pexels API key (or set PEXELS_API_KEY).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("demo_media"),
        help="Output directory for downloaded assets.",
    )
    parser.add_argument(
        "--categories",
        default=",".join(DEFAULT_CATEGORIES),
        help="Comma-separated categories to fetch.",
    )
    parser.add_argument(
        "--photo-total",
        type=int,
        default=35,
        help="Total number of photos to download.",
    )
    parser.add_argument(
        "--video-total",
        type=int,
        default=15,
        help="Total number of videos to download.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=80,
        help="Results per API request (max 80).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="Max pages to scan per category to meet counts.",
    )
    parser.add_argument(
        "--orientation",
        choices=sorted(ORIENTATIONS),
        default="landscape",
        help="Desired media orientation.",
    )
    parser.add_argument(
        "--photo-size",
        choices=sorted(PHOTO_SIZES),
        default="large",
        help="Photo size variant to download.",
    )
    parser.add_argument(
        "--video-quality",
        choices=sorted(VIDEO_QUALITIES),
        default="sd",
        help="Preferred video quality to download.",
    )
    parser.add_argument(
        "--locale",
        default="en-US",
        help="Search locale.",
    )
    parser.add_argument(
        "--max-video-duration",
        type=int,
        default=25,
        help="Max video duration in seconds.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible selections.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan items without downloading files.",
    )
    return parser.parse_args()


def split_counts(total: int, categories: Sequence[str]) -> Dict[str, int]:
    if total <= 0:
        return {category: 0 for category in categories}
    base = total // len(categories)
    extra = total % len(categories)
    counts: Dict[str, int] = {}
    for index, category in enumerate(categories):
        counts[category] = base + (1 if index < extra else 0)
    return counts


def pick_query(category: str) -> str:
    queries = CATEGORY_QUERIES.get(category, [category])
    return random.choice(queries)


def build_photo_params(query: str, args: argparse.Namespace, page: int) -> Dict[str, Any]:
    return {
        "query": query,
        "per_page": args.per_page,
        "page": page,
        "orientation": args.orientation,
        "size": args.photo_size,
        "locale": args.locale,
    }


def build_video_params(query: str, args: argparse.Namespace, page: int) -> Dict[str, Any]:
    return {
        "query": query,
        "per_page": args.per_page,
        "page": page,
        "orientation": args.orientation,
        "size": "small",
        "locale": args.locale,
    }


def slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).stem
    return name.replace("pexels-", "").replace("video-", "").replace("photo-", "")


def extension_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    ext = Path(path).suffix
    return ext if ext else ""


def pick_photo_src(photo: Dict[str, Any], size: str) -> Optional[str]:
    src = photo.get("src") or {}
    return src.get(size) or src.get("large") or src.get("original")


def pick_video_file(video: Dict[str, Any], quality: str) -> Optional[Dict[str, Any]]:
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"]
    if not files:
        return None
    if quality != "any":
        preferred = [f for f in files if f.get("quality") == quality]
        files = preferred or files
    return min(files, key=lambda f: (f.get("width", 0) * f.get("height", 0), f.get("fps", 0)))


async def fetch_json(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    response = await client.get(path, params=params)
    response.raise_for_status()
    return response.json()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


async def download_file(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    ensure_dir(dest.parent)
    response = await client.get(url)
    response.raise_for_status()
    dest.write_bytes(response.content)


def build_attribution(kind: str, creator_name: str, pexels_url: str) -> str:
    label = "Photo" if kind == "photo" else "Video"
    return f"{label} by {creator_name} on Pexels ({pexels_url})"


async def gather_media_for_category(
    client: httpx.AsyncClient,
    category: str,
    kind: str,
    count: int,
    args: argparse.Namespace,
) -> List[Dict[str, Any]]:
    if count <= 0:
        return []
    results: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for page in range(1, args.max_pages + 1):
        query = pick_query(category)
        if kind == "photo":
            params = build_photo_params(query, args, page)
            data = await fetch_json(client, "/v1/search", params)
            items = data.get("photos", [])
        else:
            params = build_video_params(query, args, page)
            data = await fetch_json(client, "/videos/search", params)
            items = data.get("videos", [])

        for item in items:
            item_id = item.get("id")
            if not isinstance(item_id, int) or item_id in seen:
                continue
            if kind == "video" and item.get("duration") and item["duration"] > args.max_video_duration:
                continue
            seen.add(item_id)
            item["_query"] = query
            results.append(item)
            if len(results) >= count:
                return results
    return results


def infer_filename(kind: str, item: Dict[str, Any], url: str) -> str:
    item_id = item.get("id")
    slug = slug_from_url(item.get("url", "")) or str(item_id)
    ext = extension_from_url(url)
    if kind == "video" and not ext:
        ext = ".mp4"
    if kind == "photo" and not ext:
        ext = ".jpg"
    return f"pexels_{item_id}_{slug}{ext}"


def media_dir(out_dir: Path, kind: str, category: str) -> Path:
    base = "photos" if kind == "photo" else "videos"
    return out_dir / base / category


async def main() -> None:
    load_env_defaults()
    args = parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    if not args.api_key:
        raise SystemExit("Missing API key. Pass --api-key or set PEXELS_API_KEY.")

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    if not categories:
        raise SystemExit("No categories provided.")

    photo_counts = split_counts(args.photo_total, categories)
    video_counts = split_counts(args.video_total, categories)

    manifest: List[Dict[str, Any]] = []
    attribution_lines: List[str] = []

    headers = {"Authorization": args.api_key}
    async with httpx.AsyncClient(base_url=API_BASE, headers=headers, timeout=60.0) as client:
        for category in categories:
            photo_items = await gather_media_for_category(
                client, category, "photo", photo_counts[category], args
            )
            video_items = await gather_media_for_category(
                client, category, "video", video_counts[category], args
            )

            for item in photo_items:
                src_url = pick_photo_src(item, args.photo_size)
                if not src_url:
                    continue
                filename = infer_filename("photo", item, src_url)
                dest = media_dir(args.out_dir, "photo", category) / filename
                if not args.dry_run:
                    await download_file(client, src_url, dest)
                attribution = build_attribution("photo", item.get("photographer", "Unknown"), item.get("url", ""))
                attribution_lines.append(attribution)
                manifest.append(
                    {
                        "id": item.get("id"),
                        "type": "photo",
                        "category": category,
                        "query": item.get("_query"),
                        "pexels_url": item.get("url"),
                        "creator_name": item.get("photographer"),
                        "creator_url": item.get("photographer_url"),
                        "download_url": src_url,
                        "filename": str(dest),
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "attribution": attribution,
                    }
                )

            for item in video_items:
                file_info = pick_video_file(item, args.video_quality)
                if not file_info:
                    continue
                src_url = file_info.get("link")
                if not src_url:
                    continue
                filename = infer_filename("video", item, src_url)
                dest = media_dir(args.out_dir, "video", category) / filename
                if not args.dry_run:
                    await download_file(client, src_url, dest)
                user = item.get("user") or {}
                attribution = build_attribution("video", user.get("name", "Unknown"), item.get("url", ""))
                attribution_lines.append(attribution)
                manifest.append(
                    {
                        "id": item.get("id"),
                        "type": "video",
                        "category": category,
                        "query": item.get("_query"),
                        "pexels_url": item.get("url"),
                        "creator_name": user.get("name"),
                        "creator_url": user.get("url"),
                        "download_url": src_url,
                        "filename": str(dest),
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "duration": item.get("duration"),
                        "attribution": attribution,
                    }
                )

    ensure_dir(args.out_dir)
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

    attribution_lines = sorted(set(attribution_lines))
    attribution_header = [
        "Photos and videos provided by Pexels.",
        "Attribution list:",
        "",
    ]
    (args.out_dir / "ATTRIBUTION.md").write_text("\n".join(attribution_header + attribution_lines) + "\n")

    print(f"Downloaded {len(manifest)} assets into {args.out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
