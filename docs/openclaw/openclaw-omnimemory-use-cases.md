# OpenClaw + OmniMemory Use Cases and Test Guide

This guide provides manual test flows for the OpenClaw integration work (prompt overrides, annotated ingest, settings proxy, etc.) and doubles as user-facing use cases.

## Prereqs
- OmniMemory services running (`omni start` or manual)
- Celery worker running for processing; Celery beat for daily summaries
- API base URL and token (if auth is enabled)
- OpenClaw skills installed (via `omni setup` or copy from `docs/openclaw/skills/omnimemory/`)

### Quick env setup
```bash
export OMNIMEMORY_API_URL="http://localhost:8000"
export OMNIMEMORY_API_TOKEN="YOUR_TOKEN_IF_AUTH_ENABLED"
```

If auth is disabled, omit the token and the `Authorization` header in examples.

## When to rerun omni link/setup/start
- Re-run `npm link` in `apps/cli` only if you changed CLI code or CLI dependencies.
- Re-run `omni setup` only if you changed `.env` values (storage, auth, prompts dir, etc.).
- Re-run `omni start` whenever backend/web code or environment values change; also after `omni setup`.
- If you edited prompt files on disk and `PROMPT_HOT_RELOAD=false`, restart the API or wait for the cache TTL (default 300s).

## Smoke tests (5–10 min)
1. Check service health:
   - `omni status` (CLI path), or
   - `make dev-ps` (manual path)
2. OpenClaw connection test:
```bash
curl -sS "$OMNIMEMORY_API_URL/api/openclaw/connection/test" \
  -H "Authorization: Bearer $OMNIMEMORY_API_TOKEN"
```
3. Prompt list + settings check:
```bash
curl -sS "$OMNIMEMORY_API_URL/api/openclaw/prompts" \
  -H "Authorization: Bearer $OMNIMEMORY_API_TOKEN"

curl -sS "$OMNIMEMORY_API_URL/api/openclaw/settings" \
  -H "Authorization: Bearer $OMNIMEMORY_API_TOKEN"
```

## Use case 1: Annotated ingest from OpenClaw (photo/video/audio)
Purpose: Add a memory with user-provided annotations (description/tags/people/location) that are preserved across reprocessing.

### Steps
1) Get a presigned upload URL
2) Upload the file
3) Call `/api/openclaw/ingest` with annotations
4) Search or timeline to confirm

### Example
```bash
API_URL="${OMNIMEMORY_API_URL:-http://localhost:8000}"
TOKEN="${OMNIMEMORY_API_TOKEN:-}"
AUTH_HEADER=""
[ -n "$TOKEN" ] && AUTH_HEADER="Authorization: Bearer $TOKEN"

FILE="/path/to/coffee.jpg"

UPLOAD_RESPONSE=$(curl -sS -X POST "$API_URL/storage/upload-url" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d "{\"filename\":\"$(basename "$FILE")\",\"content_type\":\"image/jpeg\",\"prefix\":\"openclaw\"}")

UPLOAD_URL=$(echo "$UPLOAD_RESPONSE" | jq -r '.url')
STORAGE_KEY=$(echo "$UPLOAD_RESPONSE" | jq -r '.key')

curl -sS -X PUT "$UPLOAD_URL" --data-binary "@$FILE"

curl -sS -X POST "$API_URL/api/openclaw/ingest" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d '{
    "storage_key": "'"$STORAGE_KEY"'",
    "item_type": "photo",
    "content_type": "image/jpeg",
    "original_filename": "'"$(basename "$FILE")"'",
    "captured_at": "2026-02-02T18:22:00Z",
    "description": "Coffee with Alice at Blue Bottle",
    "tags": ["coffee", "friends"],
    "people": ["Alice"],
    "location": {"name": "Blue Bottle Coffee", "lat": 37.776, "lng": -122.423}
  }'
```

Verify via search:
```bash
curl -sS -X POST "$API_URL/memory/search" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d '{"query":"coffee Alice","limit":5}'
```

## Use case 1b: Shared toolset search (recommended)
Purpose: Use the Memory API (shared toolset) for retrieval so OpenClaw and OmniMemory share the same RAG behavior.

```bash
./omnimemory_search.sh "coffee Alice" 5
```

Or call the Memory API directly:
```bash
curl -sS -X POST "$API_URL/memory/search" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d '{"query":"coffee Alice","limit":5}'
```

### Context merge behavior (explicit fields override)
```json
{
  "openclaw_context": {"description": "from client", "tags": ["a"]},
  "description": "explicit override"
}
```

## Use case 2: Prompt overrides (per user)
Purpose: Customize prompts (image/video/audio/episode/chat/agent) and update them safely with optimistic concurrency.

### Steps
1) List prompts and capture current `sha256`
2) Update a prompt with `If-Match: <sha256>`
3) Verify the source switches to `user`
4) Delete override to revert

### Example
```bash
SHA=$(curl -sS "$API_URL/api/openclaw/prompts" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} | jq -r '.prompts[] | select(.name=="image_analysis") | .sha256')

curl -sS -X PUT "$API_URL/api/openclaw/prompts/image_analysis" \
  -H "Content-Type: application/json" \
  -H "If-Match: $SHA" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d '{
    "content": "You are an image analysis assistant. Use OCR when available: {{ ocr_text }}.\nReturn concise tags and a short summary.",
    "metadata": {"version": "user-2026-02-02", "description": "Tighter photo tags"}
  }'

curl -sS "$API_URL/api/openclaw/prompts/image_analysis" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"}

curl -sS -X DELETE "$API_URL/api/openclaw/prompts/image_analysis" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"}
```

Expected behavior:
- Missing `If-Match` (or `sha256` in body) returns a precondition error.
- Stale `If-Match` returns a conflict (precondition failed).

## Use case 3: OpenClaw settings proxy
Purpose: Update OpenClaw-related settings via the API (whitelisted keys only).

```bash
curl -sS "$API_URL/api/openclaw/settings" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"}

curl -sS -X PATCH "$API_URL/api/openclaw/settings" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d '{"settings":{"openclaw":{"syncMemory":true,"workspace":"~/.openclaw"}}}'
```

## Use case 4: Timeline summaries for OpenClaw
Purpose: Provide a structured day summary with episodes and highlights for assistant tools.

```bash
curl -sS "$API_URL/memory/timeline/2026-02-02?tz_offset_minutes=-480" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"}
```

## Use case 5: Sync daily summaries to OpenClaw memory files (optional)
Purpose: Allow OpenClaw to read OmniMemory summaries from disk without API calls.

Steps:
1) Ensure settings `openclaw.syncMemory=true` (see Use case 3).
2) Run Celery beat so daily summary tasks execute.
3) Verify the file:
   - `~/.openclaw/workspace/memory/2026-02-02.md`
   - Look for the marker: `<!-- omnimemory-sync -->`

## Use case 6: Update analysis preferences (no prompt override)
Purpose: Bias analysis toward specific topics (food, people, places) without overwriting prompts.

Examples:
```bash
./omnimemory_preferences.sh focus --tags "food,people"
./omnimemory_preferences.sh defaults --tags "food" --people "Alice" --description "Focus on meals and people"
```

Expected behavior:
- Future analyses include preference guidance.
- Ingests without explicit tags/people can use defaults.

Notes:
- Defaults are empty (no bias) until you set them.
- Changes are stored in user settings and show up in the OmniMemory Settings UI after refresh.
- Browser timezone is auto-synced to preferences if none is set.

## Notes
- If auth is disabled, omit the `Authorization` header.
- Use absolute dates in tests (e.g., `2026-02-02`) to avoid timezone confusion.
- For deeper validation, you can query the DB for `context_type='user_annotation'` on new ingests.

## Future use cases to add to skills
These are documented for future skill expansion.

1) Find memories by people
   - "Show me everything with Alice last month."
   - Tool: `omnimemory_search` with `query="Alice"` and date range.

2) Location recall
   - "Memories near Golden Gate in January."
   - Tool: `omnimemory_search` with `query="Golden Gate"` + date range.

3) Food log recall
   - "What did I eat last week?"
   - Tool: `omnimemory_search` with `query="food meal dinner"` + date range.

4) Audio journal retrieval
   - "Find voice notes about product strategy."
   - Tool: `omnimemory_search` with `query="product strategy"` and optional `context_types`.

5) Meeting recap from audio
   - "List audio entries that mention roadmap."
   - Tool: `omnimemory_search` with `query="roadmap"`.

6) Prompt tuning workflow
   - Update `image_analysis`, re-ingest a photo, verify tags improved.
   - Tools: `omnimemory_prompt`, `omnimemory_ingest`.

7) Annotation preservation
   - Add description/tags, reprocess item, confirm annotation remains.
   - Tools: `omnimemory_ingest`, `omnimemory_search`.

8) Chat source verification
   - Ask chat a question, confirm results include thumbnail URLs.
   - Tools: chat UI + `omnimemory_search` for validation.

9) Daily summary verification
   - Confirm daily summary exists for yesterday and appears in timeline.
   - Tool: `omnimemory_timeline`.

10) Sync on schedule
   - Enable `openclaw.syncMemory`, run Celery beat, verify daily file updates.
   - Tools: `omnimemory_settings`, filesystem check.
