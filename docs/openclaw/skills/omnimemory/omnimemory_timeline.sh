#!/usr/bin/env bash
# omnimemory_timeline.sh - Get day summary from OmniMemory
# Usage: ./omnimemory_timeline.sh "YYYY-MM-DD" [tz_offset_minutes]
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

DATE="${1:?Error: date required. Usage: $0 \"YYYY-MM-DD\" [tz_offset_minutes]}"
TZ_OFFSET="${2:-}"

# Build URL with optional tz_offset_minutes
URL="$API_URL/api/openclaw/timeline/$DATE"
if [ -n "$TZ_OFFSET" ]; then
  URL="${URL}?tz_offset_minutes=${TZ_OFFSET}"
fi

# Build curl command
CURL_OPTS=(-sS -X GET "$URL")

if [ -n "$TOKEN" ]; then
  CURL_OPTS+=(-H "Authorization: Bearer $TOKEN")
fi

# Execute request
curl "${CURL_OPTS[@]}"
