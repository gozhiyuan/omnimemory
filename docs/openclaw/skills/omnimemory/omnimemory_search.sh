#!/usr/bin/env bash
# omnimemory_search.sh - Search OmniMemory for memories
# Usage: ./omnimemory_search.sh "query" [limit] [date_from] [date_to] [context_types] [tz_offset_minutes]
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

QUERY="${1:?Error: query required. Usage: $0 \"query\" [limit] [date_from] [date_to] [context_types]}"
LIMIT="${2:-10}"
DATE_FROM="${3:-}"
DATE_TO="${4:-}"
CONTEXT_TYPES="${5:-}"
TZ_OFFSET="${6:-}"

if ! [[ "$LIMIT" =~ ^[0-9]+$ ]]; then
  echo '{"success": false, "error": "limit must be a number"}' >&2
  exit 1
fi

# Build JSON payload
JSON_PAYLOAD=$(jq -n \
  --arg query "$QUERY" \
  --argjson limit "$LIMIT" \
  --arg date_from "$DATE_FROM" \
  --arg date_to "$DATE_TO" \
  --arg context_types "$CONTEXT_TYPES" \
  --arg tz_offset "$TZ_OFFSET" \
  '{
    query: $query,
    limit: $limit
  } + (if $date_from != "" then {date_from: $date_from} else {} end)
    + (if $date_to != "" then {date_to: $date_to} else {} end)
    + (if $context_types != "" then {context_types: ($context_types | split(",") | map(gsub("^\\s+|\\s+$"; "")))} else {} end)
    + (if $tz_offset != "" then {tz_offset_minutes: ($tz_offset | tonumber)} else {} end)')

# Build curl command
CURL_OPTS=(-sS -X POST "$API_URL/api/openclaw/search-advanced" -H "Content-Type: application/json")

if [ -n "$TOKEN" ]; then
  CURL_OPTS+=(-H "Authorization: Bearer $TOKEN")
fi

# Execute request
curl "${CURL_OPTS[@]}" -d "$JSON_PAYLOAD"
