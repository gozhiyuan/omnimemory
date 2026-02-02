#!/usr/bin/env bash
# omnimemory_prompt.sh - Manage OmniMemory prompt templates
# Usage:
#   ./omnimemory_prompt.sh list                     - List all prompts
#   ./omnimemory_prompt.sh get <name>               - Get prompt content
#   ./omnimemory_prompt.sh update <name> <content|- > [--sha256 <hash>] [--file <path>]  - Update prompt
#   ./omnimemory_prompt.sh delete <name>            - Delete user override
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
  list)
    # List all prompts
    curl -sS -X GET "$API_URL/api/openclaw/prompts" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"}
    ;;

  get)
    NAME="${2:?Error: prompt name required. Usage: $0 get <name>}"
    curl -sS -X GET "$API_URL/api/openclaw/prompts/$NAME" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"}
    ;;

  update)
    NAME="${2:?Error: prompt name required. Usage: $0 update <name> <content> [--sha256 <hash>]}"
    CONTENT_ARG="${3:-}"
    SHA256=""
    FILE_PATH=""

    # Parse optional --sha256
    shift 3
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --sha256)
          SHA256="$2"
          shift 2
          ;;
        --file)
          FILE_PATH="$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done

    if [ -n "$FILE_PATH" ]; then
      if [ ! -f "$FILE_PATH" ]; then
        echo '{"success": false, "error": "File not found: '"$FILE_PATH"'"}' >&2
        exit 1
      fi
      CONTENT=$(cat "$FILE_PATH")
    elif [ "$CONTENT_ARG" = "-" ]; then
      CONTENT=$(cat)
    else
      if [ -z "$CONTENT_ARG" ]; then
        echo '{"success": false, "error": "Content required. Usage: '"$0"' update <name> <content|- > [--sha256 <hash>] [--file <path>]"}' >&2
        exit 1
      fi
      CONTENT="$CONTENT_ARG"
    fi

    # Build payload
    PAYLOAD=$(jq -n --arg content "$CONTENT" --arg sha256 "$SHA256" \
      '{content: $content} + (if $sha256 != "" then {sha256: $sha256} else {} end)')

    # Make request with If-Match header if sha256 provided
    if [ -n "$SHA256" ]; then
      curl -sS -X PUT "$API_URL/api/openclaw/prompts/$NAME" \
        -H "Content-Type: application/json" \
        -H "If-Match: $SHA256" \
        ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
        -d "$PAYLOAD"
    else
      curl -sS -X PUT "$API_URL/api/openclaw/prompts/$NAME" \
        -H "Content-Type: application/json" \
        ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
        -d "$PAYLOAD"
    fi
    ;;

  delete)
    NAME="${2:?Error: prompt name required. Usage: $0 delete <name>}"
    curl -sS -X DELETE "$API_URL/api/openclaw/prompts/$NAME" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"}
    ;;

  *)
    echo '{"success": false, "error": "Unknown command. Usage: '"$0"' list|get|update|delete [args]"}' >&2
    exit 1
    ;;
esac
