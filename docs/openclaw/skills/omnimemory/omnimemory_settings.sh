#!/usr/bin/env bash
# omnimemory_settings.sh - Manage OmniMemory user settings
# Usage:
#   ./omnimemory_settings.sh get                    - Get current settings
#   ./omnimemory_settings.sh set <key> <value>      - Set a setting (JSON or string value)
#   ./omnimemory_settings.sh patch <json>           - Patch multiple settings
set -euo pipefail

# Configuration
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

# Build auth header
AUTH_HEADER=""
if [ -n "$TOKEN" ]; then
  AUTH_HEADER="Authorization: Bearer $TOKEN"
fi

# Parse command
COMMAND="${1:-}"

case "$COMMAND" in
  get)
    # Get current settings
    curl -sS -X GET "$API_URL/api/openclaw/settings" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"}
    ;;

  set)
    KEY="${2:?Error: key required. Usage: $0 set <key> <value>}"
    VALUE="${3:?Error: value required. Usage: $0 set <key> <value>}"

    # Build settings object (top-level keys only)
    if echo "$VALUE" | jq -e . >/dev/null 2>&1; then
      PAYLOAD=$(jq -n --arg key "$KEY" --argjson value "$VALUE" \
        '{settings: {($key): $value}}')
    else
      PAYLOAD=$(jq -n --arg key "$KEY" --arg value "$VALUE" \
        '{settings: {($key): $value}}')
    fi

    curl -sS -X PATCH "$API_URL/api/openclaw/settings" \
      -H "Content-Type: application/json" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
      -d "$PAYLOAD"
    ;;

  patch)
    JSON="${2:?Error: JSON required. Usage: $0 patch '<json>'}"

    # Wrap in settings object if not already
    if echo "$JSON" | jq -e '.settings' > /dev/null 2>&1; then
      PAYLOAD="$JSON"
    else
      PAYLOAD=$(jq -n --argjson settings "$JSON" '{settings: $settings}')
    fi

    curl -sS -X PATCH "$API_URL/api/openclaw/settings" \
      -H "Content-Type: application/json" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
      -d "$PAYLOAD"
    ;;

  *)
    echo '{"success": false, "error": "Unknown command. Usage: '"$0"' get|set|patch [args]"}' >&2
    exit 1
    ;;
esac
