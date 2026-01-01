"""Copy objects from Supabase Storage to a RustFS (S3-compatible) bucket."""

from __future__ import annotations

import argparse
import os
from typing import Iterable, Iterator
from urllib.parse import quote

import httpx

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
except ImportError as exc:  # pragma: no cover - script dependency check
    raise SystemExit("boto3 is required: pip install boto3") from exc


DEFAULT_LIMIT = 1000


class IteratorReader:
    """Wrap an iterator of bytes to provide a file-like read() API."""

    def __init__(self, iterator: Iterator[bytes]) -> None:
        self._iterator = iterator
        self._buffer = b""

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._buffer]
            self._buffer = b""
            chunks.extend(self._iterator)
            return b"".join(chunks)

        while len(self._buffer) < size:
            try:
                self._buffer += next(self._iterator)
            except StopIteration:
                break
        data = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return data


def env_or_default(value: str | None, fallback: str | None) -> str | None:
    return value if value else fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supabase-url", default=os.getenv("SUPABASE_URL"))
    parser.add_argument(
        "--supabase-key",
        default=os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        help="Supabase service role key",
    )
    parser.add_argument(
        "--supabase-bucket",
        default=env_or_default(os.getenv("SUPABASE_BUCKET"), os.getenv("BUCKET_ORIGINALS")),
    )
    parser.add_argument("--prefix", default=os.getenv("SUPABASE_PREFIX", ""))
    parser.add_argument(
        "--s3-endpoint-url",
        default=env_or_default(os.getenv("S3_ENDPOINT_URL"), os.getenv("RUSTFS_ENDPOINT")),
    )
    parser.add_argument(
        "--s3-access-key-id",
        default=env_or_default(os.getenv("S3_ACCESS_KEY_ID"), os.getenv("RUSTFS_ACCESS_KEY_ID")),
    )
    parser.add_argument(
        "--s3-secret-access-key",
        default=env_or_default(os.getenv("S3_SECRET_ACCESS_KEY"), os.getenv("RUSTFS_SECRET_ACCESS_KEY")),
    )
    parser.add_argument("--s3-region", default=os.getenv("S3_REGION", "us-east-1"))
    parser.add_argument(
        "--s3-bucket",
        default=env_or_default(os.getenv("S3_BUCKET"), os.getenv("BUCKET_ORIGINALS")),
    )
    parser.add_argument(
        "--force-path-style",
        action="store_true",
        default=os.getenv("S3_FORCE_PATH_STYLE", "true").lower() in {"1", "true", "yes"},
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--no-create-bucket",
        action="store_true",
        help="Skip creating the destination bucket if missing.",
    )
    return parser.parse_args()


def list_objects(
    client: httpx.Client,
    supabase_url: str,
    bucket: str,
    prefix: str,
    limit: int = DEFAULT_LIMIT,
) -> Iterable[dict]:
    offset = 0
    while True:
        payload = {"prefix": prefix, "limit": limit, "offset": offset, "search": ""}
        resp = client.post(f"{supabase_url}/storage/v1/object/list/{bucket}", json=payload, timeout=30)
        resp.raise_for_status()
        items = resp.json()
        if not items:
            break
        for item in items:
            yield item
        offset += len(items)


def is_folder(item: dict) -> bool:
    return not item.get("id") and item.get("metadata") is None


def walk_objects(
    client: httpx.Client, supabase_url: str, bucket: str, prefix: str
) -> Iterable[tuple[str, dict]]:
    for item in list_objects(client, supabase_url, bucket, prefix):
        name = item.get("name")
        if not name:
            continue
        if is_folder(item):
            next_prefix = f"{prefix}{name}/" if prefix else f"{name}/"
            yield from walk_objects(client, supabase_url, bucket, next_prefix)
            continue
        key = f"{prefix}{name}" if prefix else name
        yield key, item


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return False
        raise


def ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = (exc.response.get("Error") or {}).get("Code")
        if status != 404 and code not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    params: dict[str, object] = {"Bucket": bucket}
    if region and region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**params)
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code")
        if code in {"InvalidLocationConstraint", "IllegalLocationConstraintException"}:
            params.pop("CreateBucketConfiguration", None)
            s3.create_bucket(**params)
        else:
            raise


def download_and_upload(
    client: httpx.Client,
    s3,
    supabase_url: str,
    supabase_bucket: str,
    s3_bucket: str,
    key: str,
    content_type: str | None,
) -> None:
    url = f"{supabase_url}/storage/v1/object/{supabase_bucket}/{quote(key, safe='/')}"
    with client.stream("GET", url, timeout=None) as resp:
        resp.raise_for_status()
        reader = IteratorReader(resp.iter_bytes())
        extra_args = {"ContentType": content_type} if content_type else None
        s3.upload_fileobj(reader, s3_bucket, key, ExtraArgs=extra_args)


def main() -> int:
    args = parse_args()
    missing = [
        name
        for name, value in (
            ("--supabase-url", args.supabase_url),
            ("--supabase-key", args.supabase_key),
            ("--supabase-bucket", args.supabase_bucket),
            ("--s3-endpoint-url", args.s3_endpoint_url),
            ("--s3-access-key-id", args.s3_access_key_id),
            ("--s3-secret-access-key", args.s3_secret_access_key),
            ("--s3-bucket", args.s3_bucket),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required args: {', '.join(missing)}")

    supabase_url = args.supabase_url.rstrip("/")
    headers = {
        "apikey": args.supabase_key,
        "Authorization": f"Bearer {args.supabase_key}",
    }
    addressing_style = "path" if args.force_path_style else "virtual"
    config = BotoConfig(s3={"addressing_style": addressing_style})
    s3 = boto3.client(
        "s3",
        endpoint_url=args.s3_endpoint_url,
        aws_access_key_id=args.s3_access_key_id,
        aws_secret_access_key=args.s3_secret_access_key,
        region_name=args.s3_region,
        config=config,
    )
    if not args.no_create_bucket:
        ensure_bucket(s3, args.s3_bucket, args.s3_region)

    prefix = args.prefix.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    copied = 0
    skipped = 0
    with httpx.Client(headers=headers) as client:
        for key, item in walk_objects(client, supabase_url, args.supabase_bucket, prefix):
            content_type = (item.get("metadata") or {}).get("mimetype")
            if args.skip_existing and object_exists(s3, args.s3_bucket, key):
                skipped += 1
                print(f"skip  {key}")
                continue
            if args.dry_run:
                skipped += 1
                print(f"dry   {key}")
                continue
            download_and_upload(
                client,
                s3,
                supabase_url,
                args.supabase_bucket,
                args.s3_bucket,
                key,
                content_type,
            )
            copied += 1
            print(f"copy  {key}")

    print(f"done  copied={copied} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
