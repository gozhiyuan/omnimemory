#!/usr/bin/env bash
# omnimemory_detect_time.sh - Detect capture timestamp from a media file
# Usage: ./omnimemory_detect_time.sh "/path/to/file"
set -euo pipefail

FILE="${1:?Error: file path required. Usage: $0 \"/path/to/file\"}"

if [ ! -f "$FILE" ]; then
  echo '{"success": false, "error": "File not found"}' >&2
  exit 1
fi

get_file_mtime_iso() {
  if stat -f "%m" "$FILE" >/dev/null 2>&1; then
    local epoch
    epoch=$(stat -f "%m" "$FILE")
    date -u -r "$epoch" +"%Y-%m-%dT%H:%M:%SZ"
  elif stat -c "%Y" "$FILE" >/dev/null 2>&1; then
    local epoch
    epoch=$(stat -c "%Y" "$FILE")
    date -u -d "@$epoch" +"%Y-%m-%dT%H:%M:%SZ"
  else
    echo ""
  fi
}

get_exif_datetime_iso() {
  if command -v exiftool >/dev/null 2>&1; then
    local value
    value=$(exiftool -s -s -s -DateTimeOriginal -d "%Y-%m-%dT%H:%M:%S%z" "$FILE" 2>/dev/null || true)
    if [ -n "$value" ]; then
      echo "$value"
      return
    fi
  fi
  if command -v mdls >/dev/null 2>&1; then
    local value
    value=$(mdls -raw -name kMDItemContentCreationDate "$FILE" 2>/dev/null || true)
    if [ -n "$value" ] && [ "$value" != "(null)" ]; then
      date -u -j -f "%Y-%m-%d %H:%M:%S %z" "$value" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || true
      return
    fi
  fi
  echo ""
}

EXIF_TIME=$(get_exif_datetime_iso)
if [ -n "$EXIF_TIME" ]; then
  echo "{\"success\": true, \"timestamp\": \"$EXIF_TIME\", \"source\": \"metadata\"}"
  exit 0
fi

MTIME=$(get_file_mtime_iso)
if [ -n "$MTIME" ]; then
  echo "{\"success\": true, \"timestamp\": \"$MTIME\", \"source\": \"file_mtime\"}"
  exit 0
fi

echo '{"success": false, "error": "No timestamp detected"}'
