#!/usr/bin/env bash
# omnimemory_preferences.sh - Manage OmniMemory user preferences
# Usage:
#   ./omnimemory_preferences.sh get
#   ./omnimemory_preferences.sh set '<json>'
#   ./omnimemory_preferences.sh focus --tags "food,people" --people "Alice"
#   ./omnimemory_preferences.sh defaults --tags "food" --people "Alice" --description "Focus on meals"
set -euo pipefail

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

AUTH_HEADER=""
if [ -n "$TOKEN" ]; then
  AUTH_HEADER="Authorization: Bearer $TOKEN"
fi

COMMAND="${1:-}"
shift || true

case "$COMMAND" in
  get)
    curl -sS -X GET "$API_URL/api/openclaw/settings" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"} | jq '.settings.preferences'
    ;;

  set)
    JSON="${1:?Error: JSON required. Usage: $0 set '<json>'}"
    if echo "$JSON" | jq -e . >/dev/null 2>&1; then
      PAYLOAD=$(jq -n --argjson prefs "$JSON" '{settings: {preferences: $prefs}}')
    else
      echo '{"success": false, "error": "Invalid JSON"}' >&2
      exit 1
    fi
    curl -sS -X PATCH "$API_URL/api/openclaw/settings" \
      -H "Content-Type: application/json" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
      -d "$PAYLOAD"
    ;;

  focus)
    TAGS=""
    PEOPLE=""
    PLACES=""
    TOPICS=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --tags) TAGS="$2"; shift 2 ;;
        --people) PEOPLE="$2"; shift 2 ;;
        --places) PLACES="$2"; shift 2 ;;
        --topics) TOPICS="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    PAYLOAD=$(jq -n \
      --arg tags "$TAGS" \
      --arg people "$PEOPLE" \
      --arg places "$PLACES" \
      --arg topics "$TOPICS" \
      '{
        settings: {
          preferences: {}
        }
      }
      + (if $tags != "" then {settings: {preferences: {focus_tags: ($tags | split(",") | map(gsub("^\\s+|\\s+$"; "")) )}}} else {} end)
      + (if $people != "" then {settings: {preferences: {focus_people: ($people | split(",") | map(gsub("^\\s+|\\s+$"; "")) )}}} else {} end)
      + (if $places != "" then {settings: {preferences: {focus_places: ($places | split(",") | map(gsub("^\\s+|\\s+$"; "")) )}}} else {} end)
      + (if $topics != "" then {settings: {preferences: {focus_topics: ($topics | split(",") | map(gsub("^\\s+|\\s+$"; "")) )}}} else {} end)')
    curl -sS -X PATCH "$API_URL/api/openclaw/settings" \
      -H "Content-Type: application/json" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
      -d "$PAYLOAD"
    ;;

  defaults)
    TAGS=""
    PEOPLE=""
    DESC=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --tags) TAGS="$2"; shift 2 ;;
        --people) PEOPLE="$2"; shift 2 ;;
        --description) DESC="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    PAYLOAD=$(jq -n \
      --arg tags "$TAGS" \
      --arg people "$PEOPLE" \
      --arg description "$DESC" \
      '{
        settings: {
          preferences: {
            annotation_defaults: {}
          }
        }
      }
      + (if $tags != "" then {settings: {preferences: {annotation_defaults: {tags: ($tags | split(",") | map(gsub("^\\s+|\\s+$"; "")) )}}}} else {} end)
      + (if $people != "" then {settings: {preferences: {annotation_defaults: {people: ($people | split(",") | map(gsub("^\\s+|\\s+$"; "")) )}}}} else {} end)
      + (if $description != "" then {settings: {preferences: {annotation_defaults: {description_prefix: $description}}}} else {} end)')
    curl -sS -X PATCH "$API_URL/api/openclaw/settings" \
      -H "Content-Type: application/json" \
      ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
      -d "$PAYLOAD"
    ;;

  *)
    echo '{"success": false, "error": "Unknown command. Usage: '"$0"' get|set|focus|defaults [args]"}' >&2
    exit 1
    ;;
esac
