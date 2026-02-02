#!/usr/bin/env bash
# omnimemory_ingest.sh - Upload and ingest media to OmniMemory
# Usage: ./omnimemory_ingest.sh "/path/to/file" [item_type] [options...]
# item_type: photo, video, or audio (default: auto-detect from extension)
# Options:
#   --description "text"  - Add description/annotation
#   --tags "tag1,tag2"    - Add comma-separated tags
#   --people "name1,name2" - Add people in the memory
#   --location "name"     - Location name
#   --lat 12.34           - Latitude
#   --lng 56.78           - Longitude
#   --captured-at "ISO"   - Override capture timestamp (ISO 8601)
#   --auto-captured-at    - Try to extract timestamp from file metadata
#   --use-file-mtime      - Use file modified time as capture time
set -euo pipefail

# Configuration: env vars take precedence, fallback to openclaw.json
OPENCLAW_CONFIG="${HOME}/.openclaw/openclaw.json"

get_config_value() {
  local key="$1"
  local default="$2"
  if [ -f "$OPENCLAW_CONFIG" ]; then
    local value
    value=$(jq -r ".skills.entries.omnimemory.$key // empty" "$OPENCLAW_CONFIG" 2>/dev/null || true)
    if [ -n "$value" ] && [ "$value" != "null" ]; then
      echo "$value"
      return
    fi
  fi
  echo "$default"
}

# Load configuration
if [ -n "${OMNIMEMORY_API_URL:-}" ]; then
  API_URL="$OMNIMEMORY_API_URL"
else
  API_URL=$(get_config_value "apiUrl" "http://localhost:8000")
fi

if [ -n "${OMNIMEMORY_API_TOKEN:-}" ]; then
  TOKEN="$OMNIMEMORY_API_TOKEN"
else
  TOKEN=$(get_config_value "apiToken" "")
fi

# Parse arguments
FILE=""
ITEM_TYPE=""
DESCRIPTION=""
TAGS=""
PEOPLE=""
LOCATION_NAME=""
LAT=""
LNG=""
CAPTURED_AT=""
AUTO_CAPTURED_AT="false"
USE_FILE_MTIME="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --description)
      DESCRIPTION="$2"
      shift 2
      ;;
    --tags)
      TAGS="$2"
      shift 2
      ;;
    --people)
      PEOPLE="$2"
      shift 2
      ;;
    --location)
      LOCATION_NAME="$2"
      shift 2
      ;;
    --lat)
      LAT="$2"
      shift 2
      ;;
    --lng)
      LNG="$2"
      shift 2
      ;;
    --captured-at)
      CAPTURED_AT="$2"
      shift 2
      ;;
    --auto-captured-at)
      AUTO_CAPTURED_AT="true"
      shift 1
      ;;
    --use-file-mtime)
      USE_FILE_MTIME="true"
      shift 1
      ;;
    -*)
      echo '{"success": false, "error": "Unknown option: '"$1"'"}' >&2
      exit 1
      ;;
    *)
      if [ -z "$FILE" ]; then
        FILE="$1"
      elif [ -z "$ITEM_TYPE" ]; then
        ITEM_TYPE="$1"
      fi
      shift
      ;;
  esac
done

if [ -z "$FILE" ]; then
  echo '{"success": false, "error": "File path required. Usage: '"$0"' \"/path/to/file\" [item_type] [--description \"...\"] [--tags \"...\"]"}' >&2
  exit 1
fi

# Best-effort capture time extraction
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
    echo "$value"
    return
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

if [ -z "$CAPTURED_AT" ] && [ "$AUTO_CAPTURED_AT" = "true" ]; then
  CAPTURED_AT=$(get_exif_datetime_iso)
fi
if [ -z "$CAPTURED_AT" ] && [ "$USE_FILE_MTIME" = "true" ]; then
  CAPTURED_AT=$(get_file_mtime_iso)
fi

# Check file exists
if [ ! -f "$FILE" ]; then
  echo '{"success": false, "error": "File not found: '"$FILE"'"}' >&2
  exit 1
fi

# Auto-detect item type from extension if not provided
if [ -z "$ITEM_TYPE" ]; then
  EXT="${FILE##*.}"
  EXT_LOWER=$(echo "$EXT" | tr '[:upper:]' '[:lower:]')
  case "$EXT_LOWER" in
    jpg|jpeg|png|gif|webp|heic|heif)
      ITEM_TYPE="photo"
      ;;
    mp4|mov|avi|mkv|webm)
      ITEM_TYPE="video"
      ;;
    mp3|m4a|wav|ogg|flac|aac)
      ITEM_TYPE="audio"
      ;;
    *)
      echo '{"success": false, "error": "Cannot detect item type from extension: '"$EXT"'. Please specify item_type."}' >&2
      exit 1
      ;;
  esac
fi

# Get filename
FILENAME=$(basename "$FILE")
EXT="${FILENAME##*.}"
EXT_LOWER=$(echo "$EXT" | tr '[:upper:]' '[:lower:]')

# Best-effort content type for uploads + ingestion
CONTENT_TYPE="application/octet-stream"
case "$EXT_LOWER" in
  jpg|jpeg) CONTENT_TYPE="image/jpeg" ;;
  png) CONTENT_TYPE="image/png" ;;
  gif) CONTENT_TYPE="image/gif" ;;
  webp) CONTENT_TYPE="image/webp" ;;
  heic|heif) CONTENT_TYPE="image/heic" ;;
  mp4) CONTENT_TYPE="video/mp4" ;;
  mov) CONTENT_TYPE="video/quicktime" ;;
  webm) CONTENT_TYPE="video/webm" ;;
  mp3) CONTENT_TYPE="audio/mpeg" ;;
  m4a) CONTENT_TYPE="audio/mp4" ;;
  wav) CONTENT_TYPE="audio/wav" ;;
  ogg) CONTENT_TYPE="audio/ogg" ;;
  flac) CONTENT_TYPE="audio/flac" ;;
  aac) CONTENT_TYPE="audio/aac" ;;
esac

# Build auth header for curl
AUTH_HEADER=""
if [ -n "$TOKEN" ]; then
  AUTH_HEADER="Authorization: Bearer $TOKEN"
fi

# Step 1: Get presigned upload URL
UPLOAD_RESPONSE=$(curl -sS -X POST "$API_URL/storage/upload-url" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d "{\"filename\":\"$FILENAME\",\"content_type\":\"$CONTENT_TYPE\",\"prefix\":\"openclaw\"}")

# Parse response
UPLOAD_URL=$(echo "$UPLOAD_RESPONSE" | jq -r '.url // empty')
STORAGE_KEY=$(echo "$UPLOAD_RESPONSE" | jq -r '.key // empty')

if [ -z "$UPLOAD_URL" ] || [ -z "$STORAGE_KEY" ]; then
  echo '{"success": false, "error": "Failed to get upload URL", "response": '"$UPLOAD_RESPONSE"'}' >&2
  exit 1
fi

# Step 2: Upload file to storage
HTTP_STATUS=$(curl -sS -o /dev/null -w "%{http_code}" -X PUT "$UPLOAD_URL" \
  -H "Content-Type: $CONTENT_TYPE" \
  --data-binary "@$FILE")

if [ "$HTTP_STATUS" -lt 200 ] || [ "$HTTP_STATUS" -ge 300 ]; then
  echo '{"success": false, "error": "Upload failed with status '"$HTTP_STATUS"'"}' >&2
  exit 1
fi

# Step 3: Build ingest payload with optional context fields
INGEST_PAYLOAD=$(jq -n \
  --arg storage_key "$STORAGE_KEY" \
  --arg item_type "$ITEM_TYPE" \
  --arg content_type "$CONTENT_TYPE" \
  --arg original_filename "$FILENAME" \
  --arg captured_at "$CAPTURED_AT" \
  --arg description "$DESCRIPTION" \
  --arg tags "$TAGS" \
  --arg people "$PEOPLE" \
  --arg location_name "$LOCATION_NAME" \
  --arg lat "$LAT" \
  --arg lng "$LNG" \
  '{
    storage_key: $storage_key,
    item_type: $item_type,
    content_type: $content_type,
    original_filename: $original_filename,
    provider: "openclaw"
  }
  + (if $captured_at != "" then {captured_at: $captured_at} else {} end)
  + (if $description != "" then {description: $description} else {} end)
  + (if $tags != "" then {tags: ($tags | split(",") | map(gsub("^\\s+|\\s+$"; "")))} else {} end)
  + (if $people != "" then {people: ($people | split(",") | map(gsub("^\\s+|\\s+$"; "")))} else {} end)
  + (if $location_name != "" or $lat != "" or $lng != "" then {
      location: (
        {}
        + (if $location_name != "" then {name: $location_name} else {} end)
        + (if $lat != "" then {lat: ($lat | tonumber)} else {} end)
        + (if $lng != "" then {lng: ($lng | tonumber)} else {} end)
      )
    } else {} end)')

# Step 4: Trigger ingestion
INGEST_RESPONSE=$(curl -sS -X POST "$API_URL/api/openclaw/ingest" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d "$INGEST_PAYLOAD")

echo "$INGEST_RESPONSE"
