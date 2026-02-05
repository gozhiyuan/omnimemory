#!/usr/bin/env python3
"""Prune Pexels demo manifest + attribution after deleting files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune manifest/attribution for demo_media.")
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=Path("demo_media"),
        help="Root demo media directory (default: demo_media).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest.json (default: <media-dir>/manifest.json).",
    )
    parser.add_argument(
        "--attr",
        type=Path,
        default=None,
        help="Path to ATTRIBUTION.md (default: <media-dir>/ATTRIBUTION.md).",
    )
    parser.add_argument(
        "--delete-orphans",
        action="store_true",
        help="Delete files on disk that are not referenced in the manifest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing files.",
    )
    return parser.parse_args()


def resolve_entry_path(entry_path: str, manifest_path: Path) -> Path:
    path = Path(entry_path)
    if path.is_absolute():
        return path
    # First try relative to current working directory.
    if path.exists():
        return path
    # Fallback to manifest directory.
    return (manifest_path.parent / path).resolve()


def load_manifest(path: Path) -> List[Dict]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise SystemExit("Manifest JSON is not a list.")
    return data


def write_manifest(path: Path, entries: List[Dict]) -> None:
    path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n")


def write_attribution(path: Path, entries: List[Dict]) -> None:
    lines = sorted({entry.get("attribution", "").strip() for entry in entries if entry.get("attribution")})
    header = [
        "Photos and videos provided by Pexels.",
        "Attribution list:",
        "",
    ]
    path.write_text("\n".join(header + lines) + "\n")


def find_orphans(media_dir: Path, referenced: List[Path]) -> List[Path]:
    referenced_set = {p.resolve() for p in referenced}
    orphans: List[Path] = []
    for base in ("photos", "videos"):
        root = media_dir / base
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.resolve() not in referenced_set:
                orphans.append(path)
    return sorted(orphans, key=lambda p: p.as_posix())


def main() -> None:
    args = parse_args()
    manifest_path = args.manifest or (args.media_dir / "manifest.json")
    attr_path = args.attr or (args.media_dir / "ATTRIBUTION.md")

    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    entries = load_manifest(manifest_path)

    kept: List[Dict] = []
    missing: List[Tuple[str, str]] = []
    referenced_paths: List[Path] = []
    for entry in entries:
        filename = entry.get("filename")
        if not filename:
            continue
        resolved = resolve_entry_path(filename, manifest_path)
        if resolved.exists():
            kept.append(entry)
            referenced_paths.append(resolved)
        else:
            missing.append((filename, str(resolved)))

    orphans = find_orphans(args.media_dir, referenced_paths)

    print(f"Manifest entries: {len(entries)}")
    print(f"Kept entries:     {len(kept)}")
    print(f"Missing files:    {len(missing)}")
    print(f"Orphan files:     {len(orphans)}")

    if missing:
        print("\nMissing entries (pruned):")
        for original, resolved in missing:
            print(f"- {original} (resolved to {resolved})")

    if orphans:
        print("\nOrphan files on disk (not in manifest):")
        for path in orphans:
            print(f"- {path}")

    if args.dry_run:
        print("\nDry run: no files written.")
        return

    write_manifest(manifest_path, kept)
    write_attribution(attr_path, kept)
    print(f"\nUpdated {manifest_path} and {attr_path}.")

    if args.delete_orphans and orphans:
        for path in orphans:
            path.unlink()
        print(f"Deleted {len(orphans)} orphan files.")


if __name__ == "__main__":
    main()
