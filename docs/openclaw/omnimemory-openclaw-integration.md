# OmniMemory + OpenClaw Integration Guide

> **Status:** Implementation in progress - Phase 1 & 3 completed
> **Last Updated:** 2026-01-31

---

## 1. Executive Summary

This document describes how OmniMemory (a personal memory/lifelog app) can integrate with OpenClaw (an AI agent gateway) to provide a superior conversational experience while maintaining standalone functionality.

**Key decisions:**
- **Primary integration:** OmniMemory as a OpenClaw **tool** (Option A)
- **Chat strategy:** OpenClaw as primary chat when connected; OmniMemory chat as fallback (Option C)
- **Memory sync:** Bidirectional memory sharing when connected (Option D)
- **Distribution:** Package tools via **Clawhub** with **fixed scripts** for local execution
- **Not pursuing:** Node-based integration (Option B) — adds complexity without clear benefit

**Benefits:**
- Users get powerful agent-loop chat via Telegram/WhatsApp/Discord when OpenClaw is connected
- OmniMemory remains fully functional standalone (simpler RAG chat)
- Memories are searchable from both systems
- No duplicate chat UI development effort

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              User Interfaces                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────────┐   │
│   │  Telegram   │    │  WhatsApp   │    │   OmniMemory Web App        │   │
│   │  Discord    │    │   Slack     │    │   ┌─────────────────────┐   │   │
│   │   (via      │    │   (via      │    │   │ Dashboard │Timeline │   │   │
│   │  OpenClaw)   │    │  OpenClaw)   │    │   │ Ingest   │ Chat*   │   │   │
│   └──────┬──────┘    └──────┬──────┘    │   └─────────────────────┘   │   │
│          │                  │           └─────────────┬───────────────┘   │
│          │                  │                         │                   │
│          └────────┬─────────┘                         │                   │
│                   │                                   │                   │
│                   ▼                                   │                   │
│   ┌───────────────────────────────┐                  │                   │
│   │       OpenClaw Gateway         │                  │                   │
│   │      (local, port 18789)      │                  │                   │
│   │                               │                  │                   │
│   │  ┌─────────────────────────┐ │                  │                   │
│   │  │     Agent Runtime       │ │                  │                   │
│   │  │                         │ │                  │                   │
│   │  │  Tools:                 │ │                  │                   │
│   │  │  ├─ omnimemory_search   │◄┼──────────────────┤                   │
│   │  │  ├─ omnimemory_timeline │ │   HTTP API       │                   │
│   │  │  ├─ omnimemory_ingest   │ │                  │                   │
│   │  │  └─ (other tools...)    │ │                  │                   │
│   │  └─────────────────────────┘ │                  │                   │
│   │                               │                  │                   │
│   │  Memory:                      │                  │                   │
│   │  └─ ~/.openclaw/memory/*.md ◄────┼──────────────────┤ Memory sync       │
│   │                               │                  │                   │
│   └───────────────────────────────┘                  │                   │
│                                                      │                   │
└──────────────────────────────────────────────────────┼───────────────────┘
                                                       │
                                                       ▼
                              ┌─────────────────────────────────────────────┐
                              │            OmniMemory Backend               │
                              │              (FastAPI + Celery)             │
                              │                                             │
                              │  ┌─────────────┐  ┌─────────────────────┐  │
                              │  │  Ingestion  │  │   RAG Chat (basic)  │  │
                              │  │  Pipeline   │  │   (non-agent loop)  │  │
                              │  └─────────────┘  └─────────────────────┘  │
                              │                                             │
                              │  ┌─────────────┐  ┌─────────────────────┐  │
                              │  │   Qdrant    │  │   Postgres + S3     │  │
                              │  │  (vectors)  │  │   (metadata/files)  │  │
                              │  └─────────────┘  └─────────────────────┘  │
                              └─────────────────────────────────────────────┘

* Chat tab in OmniMemory is a simpler RAG implementation (fallback when OpenClaw not connected)
```

Assumption for this plan: OpenClaw and OmniMemory run locally on the same host, so tool calls use
`http://localhost:8000` and OpenClaw gateway runs on port `18789`.

---

## 3. Integration Options Analysis

### Option A: OmniMemory as OpenClaw Tool ✅ **RECOMMENDED**

**Approach:** Create custom tools that OpenClaw's agent can invoke to search and interact with OmniMemory.

**Why this works best:**
- Clean separation of concerns (OmniMemory = memory storage, OpenClaw = conversational AI)
- Leverages OpenClaw's existing agent loop, tool execution, and channel integrations
- Simple HTTP API calls between systems
- No complex bidirectional protocols
- Easy to version and evolve independently

**Implementation (Clawhub + fixed scripts recommended):**

Prefer predictable scripts or HTTP wrappers rather than ad-hoc shell commands. OpenClaw can still
"run bash," but the skill should call known scripts so inputs and outputs are stable.

```typescript
// openclaw-omnimemory-tools.ts
// Register as a OpenClaw extension or add to workspace tools

import { defineTool } from "openclaw/plugin-sdk";

export const omnimemorySearchTool = defineTool({
  name: "omnimemory_search",
  description: `Search the user's personal memories, photos, videos, and life events stored in OmniMemory.
Use this tool when the user asks about:
- Past events, activities, or experiences
- Photos or videos from specific times/places
- What they did on a particular day
- People, places, or things from their life`,
  
  parameters: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: "Natural language search query"
      },
      date_from: {
        type: "string",
        description: "Start date filter (ISO format, e.g., 2025-01-01)"
      },
      date_to: {
        type: "string",
        description: "End date filter (ISO format)"
      },
      context_types: {
        type: "array",
        items: { type: "string" },
        description: "Filter by context type: activity, food, travel, social, emotion"
      },
      limit: {
        type: "number",
        description: "Max results to return (default: 10)"
      }
    },
    required: ["query"]
  },

  execute: async (params, context) => {
    const { query, date_from, date_to, context_types, limit = 10 } = params;
    
    // Get OmniMemory API URL from config or env
    const apiUrl = process.env.OMNIMEMORY_API_URL || "http://localhost:8000";
    const apiToken = await getOmniMemoryToken(context.userId);
    
    if (!apiToken) {
      return {
        success: false,
        error: "OmniMemory not connected. Please connect in OmniMemory settings."
      };
    }

    // NOTE: context_types filtering requires optional /api/openclaw wrapper.
    const response = await fetch(`${apiUrl}/search?${new URLSearchParams({
      q: query,
      limit: String(limit),
      start_date: date_from || "",
      end_date: date_to || ""
    })}`, {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${apiToken}`
      }
    });

    if (!response.ok) {
      return { success: false, error: `OmniMemory API error: ${response.status}` };
    }

    const results = await response.json();
    
    // Format results for the agent
    return {
      success: true,
      total: results.results?.length || 0,
      memories: (results.results || []).map(item => ({
        id: item.context_id,
        title: item.title,
        summary: item.summary,
        date: item.event_time_utc,
        type: item.context_type,
        score: item.score
      }))
    };
  }
});

export const omnimemoryTimelineTool = defineTool({
  name: "omnimemory_timeline",
  description: `Get a summary of what happened on a specific day or date range from OmniMemory.
Returns episodes, daily summary, and key moments.`,
  
  parameters: {
    type: "object",
    properties: {
      date: {
        type: "string",
        description: "The date to get timeline for (ISO format, e.g., 2025-01-15)"
      },
      include_episodes: {
        type: "boolean",
        description: "Include episode details (default: true)"
      },
      include_items: {
        type: "boolean",
        description: "Include individual items/photos (default: false)"
      }
    },
    required: ["date"]
  },

  execute: async (params, context) => {
    const { date, include_episodes = true, include_items = false } = params;
    
    const apiUrl = process.env.OMNIMEMORY_API_URL || "http://localhost:8000";
    const apiToken = await getOmniMemoryToken(context.userId);
    
    if (!apiToken) {
      return { success: false, error: "OmniMemory not connected." };
    }

    const response = await fetch(`${apiUrl}/timeline?${new URLSearchParams({
      start_date: date,
      end_date: date,
      limit: "200"
    })}`, { headers: { "Authorization": `Bearer ${apiToken}` } });

    if (!response.ok) {
      return { success: false, error: `API error: ${response.status}` };
    }

    const days = await response.json();
    const day = Array.isArray(days) ? days[0] : null;

    const episodes = include_episodes ? (day?.episodes || []) : [];

    return {
      success: true,
      date,
      daily_summary: day?.daily_summary?.summary || null,
      episode_count: episodes.length,
      episodes: episodes.map(ep => ({
        title: ep.title,
        time_range: `${ep.start_time_utc} - ${ep.end_time_utc}`,
        summary: ep.summary,
        item_count: ep.source_item_ids?.length || 0
      })),
      highlights: day?.highlight ? [day.highlight] : []
    };
  }
});

export const omnimemoryIngestTool = defineTool({
  name: "omnimemory_ingest",
  description: `Trigger ingestion of new media into OmniMemory.
Use when the user wants to add a photo/video/audio via chat.`,
  
  parameters: {
    type: "object",
    properties: {
      content_type: {
        type: "string",
        enum: ["photo", "video", "audio"],
        description: "Type of media to ingest"
      },
      content: {
        type: "string",
        description: "Local file path to upload (downloaded by OpenClaw from the chat channel)"
      },
      captured_at: {
        type: "string",
        description: "When this memory occurred (ISO format)"
      }
    },
    required: ["content_type", "content"]
  },

  execute: async (params, context) => {
    // Implementation for ingesting content via OpenClaw
    // 1) request presigned upload
    // 2) upload bytes
    // 3) call /upload/ingest with the storage key
    const apiUrl = process.env.OMNIMEMORY_API_URL || "http://localhost:8000";
    const apiToken = await getOmniMemoryToken(context.userId);
    
    if (!apiToken) {
      return { success: false, error: "OmniMemory not connected." };
    }

    const uploadRes = await fetch(`${apiUrl}/storage/upload-url`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiToken}`
      },
      body: JSON.stringify({
        filename: params.content.split("/").pop() || "upload",
        // Use the real content type to enable thumbnails in chat.
        content_type: "image/jpeg",
        prefix: "openclaw"
      })
    });

    if (!uploadRes.ok) {
      return { success: false, error: `Upload URL failed: ${uploadRes.status}` };
    }

    const upload = await uploadRes.json();
    // uploadFileBytes should read params.content and PUT bytes to upload.url
    await uploadFileBytes(upload.url, upload.headers, params.content);

    const ingestRes = await fetch(`${apiUrl}/upload/ingest`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiToken}`
      },
      body: JSON.stringify({
        storage_key: upload.key,
        item_type: params.content_type,
        provider: "openclaw",
        captured_at: params.captured_at
      })
    });

    if (!ingestRes.ok) {
      return { success: false, error: `Ingest failed: ${ingestRes.status}` };
    }

    const result = await ingestRes.json();
    return {
      success: true,
      item_id: result.item_id,
      message: "Memory saved to OmniMemory"
    };
  }
});

// Helper to get user's OmniMemory token from OpenClaw config
async function getOmniMemoryToken(userId: string): Promise<string | null> {
  // Could be stored in:
  // 1. OpenClaw's Clawhub credential store
  // 2. Environment variable (local dev only)
  // NOTE: OmniMemory does not currently issue API keys; auth is OIDC or disabled.
  return process.env.OMNIMEMORY_API_TOKEN || null;
}
```

**Shell wrapper scripts**

Templates live in the repo at `docs/openclaw/skills/omnimemory/`.
`omni setup` copies them into the OpenClaw workspace (default: `~/.openclaw/skills/omnimemory/`).

Requires `jq` for JSON parsing. Scripts support OIDC authentication via `OMNIMEMORY_API_TOKEN`.

Also included:
- `omnimemory_preferences.sh` for updating analysis preferences and default annotations (without prompt overrides).

### Preferences (no prompt override)

Preferences live in user settings (`settings.preferences`) and allow OpenClaw to bias analysis without changing prompts.

Example:
```bash
./omnimemory_preferences.sh focus --tags "food,people"
./omnimemory_preferences.sh defaults --tags "food" --people "Alice" --description "Focus on meals"
```

These preferences:
- add guidance to image/video/audio analysis and episode summaries
- provide default tags/people/description when ingest omits them
- auto-sync browser timezone into preferences when missing (UI refresh)

### Workspace defaults

`OPENCLAW_WORKSPACE` sets the default workspace path used by settings (and by `omni setup` when installing skills).
Users can override it later in Settings → OpenClaw workspace.

```bash
#!/usr/bin/env bash
# omnimemory_search.sh - Search OmniMemory for memories
# Usage: ./omnimemory_search.sh "query" [limit] [date_from] [date_to]
set -euo pipefail

API_URL="${OMNIMEMORY_API_URL:-http://localhost:8000}"
TOKEN="${OMNIMEMORY_API_TOKEN:-}"

QUERY="${1:?Error: query required}"
LIMIT="${2:-10}"
DATE_FROM="${3:-}"
DATE_TO="${4:-}"

# Build JSON payload
JSON_PAYLOAD=$(jq -n \
  --arg query "$QUERY" \
  --argjson limit "$LIMIT" \
  --arg date_from "$DATE_FROM" \
  --arg date_to "$DATE_TO" \
  '{query: $query, limit: $limit}
   + (if $date_from != "" then {date_from: $date_from} else {} end)
   + (if $date_to != "" then {date_to: $date_to} else {} end)')

CURL_OPTS=(-sS -X POST "$API_URL/api/openclaw/search" -H "Content-Type: application/json")
if [ -n "$TOKEN" ]; then
  CURL_OPTS+=(-H "Authorization: Bearer $TOKEN")
fi

curl "${CURL_OPTS[@]}" -d "$JSON_PAYLOAD"
```

```bash
#!/usr/bin/env bash
# omnimemory_timeline.sh - Get day summary from OmniMemory
# Usage: ./omnimemory_timeline.sh "YYYY-MM-DD" [tz_offset_minutes]
set -euo pipefail

API_URL="${OMNIMEMORY_API_URL:-http://localhost:8000}"
TOKEN="${OMNIMEMORY_API_TOKEN:-}"

DATE="${1:?Error: date required}"
TZ_OFFSET="${2:-0}"

URL="$API_URL/api/openclaw/timeline/$DATE?tz_offset_minutes=$TZ_OFFSET"

CURL_OPTS=(-sS -X GET "$URL")
if [ -n "$TOKEN" ]; then
  CURL_OPTS+=(-H "Authorization: Bearer $TOKEN")
fi

curl "${CURL_OPTS[@]}"
```

```bash
#!/usr/bin/env bash
# omnimemory_ingest.sh - Upload and ingest media to OmniMemory
# Usage: ./omnimemory_ingest.sh "/path/to/file" [item_type]
set -euo pipefail

API_URL="${OMNIMEMORY_API_URL:-http://localhost:8000}"
TOKEN="${OMNIMEMORY_API_TOKEN:-}"

FILE="${1:?Error: file path required}"
ITEM_TYPE="${2:-}"

# Auto-detect item type from extension if not provided
if [ -z "$ITEM_TYPE" ]; then
  EXT="${FILE##*.}"
  case "$(echo "$EXT" | tr '[:upper:]' '[:lower:]')" in
    jpg|jpeg|png|gif|webp|heic) ITEM_TYPE="photo" ;;
    mp4|mov|avi|mkv|webm) ITEM_TYPE="video" ;;
    mp3|m4a|wav|ogg|flac) ITEM_TYPE="audio" ;;
    *) echo "Cannot detect item type" >&2; exit 1 ;;
  esac
fi

AUTH_HEADER=""
[ -n "$TOKEN" ] && AUTH_HEADER="Authorization: Bearer $TOKEN"

# Step 1: Get presigned upload URL
UPLOAD_RESPONSE=$(curl -sS -X POST "$API_URL/storage/upload-url" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d "{\"filename\":\"$(basename "$FILE")\",\"content_type\":\"image/jpeg\",\"prefix\":\"openclaw\"}")

UPLOAD_URL=$(echo "$UPLOAD_RESPONSE" | jq -r '.url')
STORAGE_KEY=$(echo "$UPLOAD_RESPONSE" | jq -r '.key')

# Step 2: Upload file
curl -sS -X PUT "$UPLOAD_URL" --data-binary "@$FILE"

# Step 3: Trigger ingestion via new /api/openclaw/ingest endpoint
curl -sS -X POST "$API_URL/api/openclaw/ingest" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d "{\"storage_key\":\"$STORAGE_KEY\",\"item_type\":\"$ITEM_TYPE\",\"provider\":\"openclaw\"}"
```

**Skill file** (`~/.openclaw/skills/omnimemory/SKILL.md`):

```markdown
# OmniMemory Integration

OmniMemory is a personal memory assistant that stores and organizes photos, videos, 
and life events. When connected, you can search and retrieve memories using the 
omnimemory tools.

## Execution

Use the fixed scripts (`omnimemory_search.sh`, `omnimemory_ingest_file.sh`) instead of
generating ad-hoc shell commands.

## Available Tools

### omnimemory_search
Search for memories by natural language query. Supports date ranges (context filters may require wrappers).

Examples:
- "What did I eat last week?" → omnimemory_search(query="food meals eating", date_from="2025-01-23")
- "Photos from Tokyo trip" → omnimemory_search(query="Tokyo Japan travel")
- "Times with Alice" → omnimemory_search(query="Alice", context_types=["social"])

### omnimemory_timeline  
Get a day's summary with episodes and highlights.

Examples:
- "What did I do yesterday?" → omnimemory_timeline(date="2025-01-29")
- "Summarize last Monday" → omnimemory_timeline(date="2025-01-27")

### omnimemory_ingest
Add new media via chat (photos, videos, audio).

Examples:
- User sends a photo → omnimemory_ingest(content_type="photo", content="/tmp/telegram/photo.jpg")
- User sends a voice note → omnimemory_ingest(content_type="audio", content="/tmp/telegram/note.m4a")

## Response Formatting

When returning search results:
- Include dates and times for temporal context
- Mention thumbnail URLs if available so images can be displayed
- Summarize episodes rather than listing every item
- Cite specific memories when answering questions
```

---

### Option B: OmniMemory as OpenClaw Node ❌ **NOT RECOMMENDED**

**Why we're not pursuing this:**

1. **Complexity overhead:** Node protocol requires WebSocket connection management, heartbeats, command registration, and bidirectional state sync.

2. **Not the right abstraction:** Nodes are designed for **device capabilities** (camera, screen, location, local file access) that must run on a specific device. OmniMemory is a **service** with an HTTP API—tools are the right abstraction.

3. **No benefit over tools:** Everything a node could do, tools can do via HTTP calls, but simpler.

---

# Future use cases to add to skills

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

4. **Maintenance burden:** Would need to maintain WebSocket reconnection logic, handle gateway restarts, manage node pairing flow.

**When nodes make sense (not our case):**
- Device-specific capabilities (camera snap, screen record)
- Local-only resources that can't be exposed via HTTP
- Real-time bidirectional streaming (not needed for memory search)

---

### Option C: OpenClaw as Primary Chat, OmniMemory Chat as Fallback ✅ **RECOMMENDED**

**Approach:** 
- When OpenClaw is connected → Use OpenClaw channels (Telegram, WhatsApp) as the primary chat interface
- When OpenClaw is not connected → OmniMemory's built-in Chat tab works standalone

**Why both:**
- Not all users will run OpenClaw (especially early adopters just trying OmniMemory)
- OmniMemory Chat is simpler (RAG without full agent loop) but still useful
- OpenClaw Chat is more powerful (agent loop, tools, multi-turn reasoning, channel flexibility)

**Implementation in OmniMemory UI:**

```typescript
// OmniMemory Settings page
interface OpenClawConnection {
  enabled: boolean;
  gatewayUrl: string;  // e.g., "http://localhost:18789" or Tailscale URL
  apiToken: string;    // For authenticating tool calls back to OmniMemory
  syncMemory: boolean; // Option D: also sync to OpenClaw's memory files
  lastConnected: Date | null;
  status: "connected" | "disconnected" | "error";
}

// Settings UI component
const OpenClawIntegrationSettings = () => {
  const [config, setConfig] = useState<OpenClawConnection>(loadConfig());
  
  return (
    <div className="space-y-4">
      <h3 className="text-lg font-medium">OpenClaw Integration</h3>
      
      <p className="text-sm text-gray-600">
        Connect to OpenClaw to chat about your memories via Telegram, WhatsApp, 
        or Discord. When connected, OpenClaw becomes your primary chat interface.
      </p>
      
      <Toggle
        label="Enable OpenClaw Integration"
        checked={config.enabled}
        onChange={(enabled) => setConfig({ ...config, enabled })}
      />
      
      {config.enabled && (
        <>
          <Input
            label="Gateway URL"
            value={config.gatewayUrl}
            placeholder="http://localhost:18789"
            onChange={(gatewayUrl) => setConfig({ ...config, gatewayUrl })}
          />
          
          <Input
            label="API Token"
            type="password"
            value={config.apiToken}
            placeholder="Token for OpenClaw to call OmniMemory API"
            onChange={(apiToken) => setConfig({ ...config, apiToken })}
          />
          
          <Toggle
            label="Sync memories to OpenClaw"
            description="Also save daily summaries to OpenClaw's memory files"
            checked={config.syncMemory}
            onChange={(syncMemory) => setConfig({ ...config, syncMemory })}
          />
          
          <ConnectionStatus status={config.status} lastConnected={config.lastConnected} />
          
          <Button onClick={testConnection}>Test Connection</Button>
        </>
      )}
    </div>
  );
};
```

**Chat tab behavior:**

```typescript
// OmniMemory Chat component
const ChatTab = () => {
  const openclawConfig = useOpenClawConfig();
  
  // Show banner when OpenClaw is connected
  if (openclawConfig.enabled && openclawConfig.status === "connected") {
    return (
      <div className="flex flex-col h-full">
        <div className="bg-blue-50 border-b border-blue-200 p-4">
          <div className="flex items-center gap-2">
            <CheckCircle className="text-green-500" />
            <span className="font-medium">OpenClaw Connected</span>
          </div>
          <p className="text-sm text-gray-600 mt-1">
            For the best experience, chat via Telegram or WhatsApp where OpenClaw 
            can use its full agent capabilities.
          </p>
          <div className="flex gap-2 mt-2">
            <Button variant="outline" size="sm" onClick={openTelegram}>
              Open Telegram
            </Button>
            <Button variant="outline" size="sm" onClick={openWhatsApp}>
              Open WhatsApp
            </Button>
          </div>
        </div>
        
        {/* Still show basic RAG chat as fallback */}
        <div className="flex-1 opacity-75">
          <BasicRagChat />
        </div>
      </div>
    );
  }
  
  // Standalone mode: full chat UI
  return <BasicRagChat />;
};
```

**Comparison of chat capabilities:**

| Feature | OmniMemory Chat (Standalone) | OpenClaw Chat (Connected) |
|---------|------------------------------|---------------------------|
| Memory search | ✅ RAG retrieval | ✅ Via omnimemory_search tool |
| Multi-turn reasoning | ❌ Simple Q&A | ✅ Full agent loop |
| Tool use | ❌ No tools | ✅ All OpenClaw tools |
| Image generation | ❌ No | ✅ If configured |
| Code execution | ❌ No | ✅ Sandbox support |
| Channel flexibility | ❌ Web only | ✅ Telegram, WhatsApp, Discord |
| Proactive tasks | ❌ No | ✅ Heartbeat, cron |
| Offline support | ✅ Works without OpenClaw | ❌ Requires gateway |

---

### Option D: Shared Memory Backend ✅ **RECOMMENDED**

**Approach:** When OpenClaw integration is enabled, OmniMemory syncs daily summaries and key memories to OpenClaw's memory files (`~/.openclaw/memory/*.md`). This assumes OpenClaw and OmniMemory run on the same host with shared filesystem access.

**Why this matters:**
- OpenClaw's vector search will include OmniMemory content
- User can ask OpenClaw about memories even without using the specific tools
- Creates a unified "memory layer" across both systems
- OpenClaw's heartbeat/proactive features can reference recent memories

**Implementation:**

```python
# omnimemory/services/api/app/integrations/openclaw_sync.py

import aiofiles
import aiohttp
from pathlib import Path
from datetime import date, datetime
from typing import Optional
import json

class OpenClawMemorySync:
    """Syncs OmniMemory content to OpenClaw's memory files."""
    
    def __init__(
        self,
        openclaw_workspace: str = "~/.openclaw",
        enabled: bool = False
    ):
        self.workspace = Path(openclaw_workspace).expanduser()
        self.memory_dir = self.workspace / "memory"
        self.enabled = enabled
    
    async def sync_daily_summary(
        self,
        user_id: str,
        date: date,
        summary: str,
        episodes: list[dict],
        highlights: list[str]
    ) -> bool:
        """Sync a day's summary to OpenClaw's memory file."""
        if not self.enabled:
            return False
        
        # Ensure memory directory exists
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        # Format for OpenClaw's memory file
        memory_path = self.memory_dir / f"{date.isoformat()}.md"
        
        content = self._format_daily_memory(date, summary, episodes, highlights)
        
        # Append to existing file or create new
        try:
            mode = "a" if memory_path.exists() else "w"
            async with aiofiles.open(memory_path, mode) as f:
                if mode == "a":
                    await f.write("\n\n---\n\n")
                await f.write(content)
            return True
        except Exception as e:
            print(f"Failed to sync to OpenClaw memory: {e}")
            return False
    
    def _format_daily_memory(
        self,
        date: date,
        summary: str,
        episodes: list[dict],
        highlights: list[str]
    ) -> str:
        """Format content for OpenClaw's memory file."""
        lines = [
            f"## OmniMemory Daily Summary - {date.strftime('%A, %B %d, %Y')}",
            "",
            summary,
            ""
        ]
        
        if episodes:
            lines.append("### Episodes")
            for ep in episodes:
                time_range = f"{ep.get('start_time', '')} - {ep.get('end_time', '')}"
                lines.append(f"- **{ep.get('title', 'Untitled')}** ({time_range})")
                if ep.get('summary'):
                    lines.append(f"  {ep['summary']}")
            lines.append("")
        
        if highlights:
            lines.append("### Highlights")
            for h in highlights:
                lines.append(f"- {h}")
            lines.append("")
        
        lines.append(f"*Source: OmniMemory, synced at {datetime.utcnow().isoformat()}Z*")
        
        return "\n".join(lines)
    
    async def sync_memory_entry(
        self,
        user_id: str,
        context: dict,
        date: Optional[date] = None
    ) -> bool:
        """Sync a single memory/context to OpenClaw."""
        if not self.enabled:
            return False
        
        event_date = date or datetime.fromisoformat(
            context.get("event_time_utc", datetime.utcnow().isoformat())
        ).date()
        
        memory_path = self.memory_dir / f"{event_date.isoformat()}.md"
        
        entry = self._format_memory_entry(context)
        
        try:
            mode = "a" if memory_path.exists() else "w"
            async with aiofiles.open(memory_path, mode) as f:
                if mode == "a":
                    await f.write("\n\n")
                await f.write(entry)
            return True
        except Exception as e:
            print(f"Failed to sync memory entry: {e}")
            return False
    
    def _format_memory_entry(self, context: dict) -> str:
        """Format a single context as a memory entry."""
        timestamp = context.get("event_time_utc", "")
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
            except:
                time_str = ""
        else:
            time_str = ""
        
        title = context.get("title", "Memory")
        summary = context.get("summary", "")
        keywords = context.get("keywords", [])
        context_type = context.get("context_type", "")
        
        lines = [
            f"### {time_str} - {title}" if time_str else f"### {title}",
            "",
            summary,
            ""
        ]
        
        if keywords:
            lines.append(f"Keywords: {', '.join(keywords)}")
        
        if context_type:
            lines.append(f"Type: {context_type}")
        
        lines.append(f"Source: omnimemory:{context.get('id', 'unknown')}")
        
        return "\n".join(lines)


# Integration with OmniMemory's pipeline
class PipelineWithOpenClawSync:
    """Extended pipeline that syncs to OpenClaw when enabled."""
    
    def __init__(self, user_settings: dict):
        self.openclaw_sync = OpenClawMemorySync(
            openclaw_workspace=user_settings.get("openclaw_workspace", "~/.openclaw"),
            enabled=user_settings.get("openclaw_sync_enabled", False)
        )
    
    async def on_daily_summary_generated(
        self,
        user_id: str,
        date: date,
        summary: str,
        episodes: list[dict]
    ):
        """Called after daily summary is generated."""
        # Sync to OpenClaw if enabled
        highlights = self._extract_highlights(episodes)
        await self.openclaw_sync.sync_daily_summary(
            user_id=user_id,
            date=date,
            summary=summary,
            episodes=episodes,
            highlights=highlights
        )
    
    async def on_context_created(self, user_id: str, context: dict):
        """Called after a processed_context is created."""
        # Optionally sync individual memories (could be noisy)
        # Only sync "significant" memories
        if context.get("is_episode") or context.get("context_type") == "daily_summary":
            await self.openclaw_sync.sync_memory_entry(user_id, context)
    
    def _extract_highlights(self, episodes: list[dict]) -> list[str]:
        """Extract notable moments from episodes."""
        highlights = []
        for ep in episodes:
            if ep.get("highlight"):
                highlights.append(ep["highlight"])
            elif ep.get("title") and ep.get("summary"):
                # Take first sentence of summary as highlight
                summary = ep["summary"]
                first_sentence = summary.split(".")[0] + "." if "." in summary else summary
                highlights.append(f"{ep['title']}: {first_sentence}")
        return highlights[:5]  # Limit to 5 highlights
```

**Example synced memory file** (`~/.openclaw/memory/2025-01-29.md`):

```markdown
## OmniMemory Daily Summary - Wednesday, January 29, 2025

Started the day with a morning run in the park, followed by breakfast at home. 
Worked from the home office until lunch. Met Alice for coffee at Blue Bottle in 
the afternoon - we discussed the upcoming project deadline. Evening was spent 
cooking dinner and watching a documentary about space exploration.

### Episodes
- **Morning Run** (07:15 - 08:00)
  5K run through Central Park, good weather, saw the sunrise over the reservoir.
- **Work Session** (09:30 - 12:30)
  Focused work on the quarterly report, several video calls with the team.
- **Coffee with Alice** (14:00 - 15:30)
  Caught up at Blue Bottle, discussed project timeline and weekend plans.
- **Evening at Home** (18:00 - 22:00)
  Made pasta for dinner, watched "Our Universe" documentary.

### Highlights
- Beautiful sunrise during morning run
- Productive work session, finished draft report
- Great conversation with Alice about the Mars mission documentary
- New pasta recipe turned out well

*Source: OmniMemory, synced at 2025-01-30T02:00:00Z*
```

---

## 4. Configuration & Setup

### Authentication Model (Current)

- OmniMemory auth is **OIDC-only** when enabled.
- If `AUTH_ENABLED=false` (default for local dev), requests are accepted as the default local user.
- There is **no API key / PAT** issuance today. If auth is enabled, OpenClaw must present a valid OIDC bearer token.

### OmniMemory Settings Schema

```python
# omnimemory/services/api/app/models/settings.py

from pydantic import BaseModel
from typing import Optional

class OpenClawIntegrationSettings(BaseModel):
    """User settings for OpenClaw integration."""
    
    enabled: bool = False
    gateway_url: str = "http://localhost:18789"
    
    # Token for OpenClaw to call OmniMemory API (OIDC bearer if auth enabled)
    api_token: Optional[str] = None
    
    # Sync memories to OpenClaw's workspace
    sync_memory: bool = True
    openclaw_workspace: str = "~/.openclaw"
    
    # What to sync
    sync_daily_summaries: bool = True
    sync_episodes: bool = True
    sync_individual_items: bool = False  # Can be noisy
    
    # Connection status (read-only, set by system)
    last_connected: Optional[str] = None
    connection_status: str = "disconnected"


class UserSettings(BaseModel):
    """Full user settings model."""
    
    # ... other settings ...
    
    openclaw: OpenClawIntegrationSettings = OpenClawIntegrationSettings()
```

### OpenClaw Configuration

Add to `~/.openclaw/openclaw.json` (example key/value storage; Clawhub may manage this for you):

```json
{
  "integrations": {
    "omnimemory": {
      "enabled": true,
      "api_url": "http://localhost:8000",
      "api_token": "oidc_bearer_token_if_auth_enabled"
    }
  }
}
```

Or via environment variables:

```bash
export OMNIMEMORY_API_URL="http://localhost:8000"
# Optional when AUTH_ENABLED=false in OmniMemory
export OMNIMEMORY_API_TOKEN="oidc_bearer_token_if_auth_enabled"
```

### Clawhub Packaging (Recommended)

- Publish a skill package that bundles tool definitions plus the wrapper scripts.
- Declare required env vars/credentials (`OMNIMEMORY_API_URL`, optional `OMNIMEMORY_API_TOKEN`).
- Pin a version and provide upgrade notes (tool schema changes, new flags).

### Skill Registration ✅ IMPLEMENTED

Skill files created at `~/.openclaw/skills/omnimemory/`:

```
~/.openclaw/skills/omnimemory/
├── SKILL.md              # Skill metadata and documentation
├── omnimemory_search.sh  # Search wrapper script
├── omnimemory_timeline.sh # Timeline wrapper script
└── omnimemory_ingest.sh  # Ingest wrapper script
```

**SKILL.md** (implemented):

```markdown
---
name: omnimemory
description: Search and manage personal memories from OmniMemory lifelog app
homepage: https://github.com/zhiyuanparis/omnimemory
metadata:
  openclaw:
    emoji: "🧠"
    os: ["darwin", "linux"]
    requires:
      bins: ["curl", "jq"]
    env:
      - OMNIMEMORY_API_URL
      - OMNIMEMORY_API_TOKEN
---

# OmniMemory Integration

OmniMemory is a personal memory assistant that stores and organizes your photos,
videos, and life events. When connected, you can search and retrieve memories
using the tools below.

## Configuration

Set these environment variables:
- `OMNIMEMORY_API_URL` - OmniMemory API URL (default: http://localhost:8000)
- `OMNIMEMORY_API_TOKEN` - OIDC bearer token for authentication (optional if auth disabled)

## When to Use

Use OmniMemory tools when the user:
- Asks about past events, activities, or experiences
- Wants to see photos or videos from specific times
- Asks "what did I do" questions
- Mentions people, places, or events from their life
- Wants to save a new photo/video/audio memory

## Available Tools

### omnimemory_search
Search memories by natural language query. Supports date ranges.

**Usage:** `./omnimemory_search.sh "query" [limit] [date_from] [date_to]`

### omnimemory_timeline
Get a day's summary with episodes and highlights.

**Usage:** `./omnimemory_timeline.sh "YYYY-MM-DD" [tz_offset_minutes]`

### omnimemory_ingest
Add new media via chat (photos, videos, audio).

**Usage:** `./omnimemory_ingest.sh "/path/to/file" [item_type]`

## Examples

"What did I do last Tuesday?"
→ ./omnimemory_timeline.sh "2025-01-28"

"Show me photos from my Tokyo trip"
→ ./omnimemory_search.sh "Tokyo Japan travel trip"

"User sends a photo"
→ ./omnimemory_ingest.sh "/tmp/telegram/photo.jpg" "photo"
```

---

## 5. User Flow Examples

### Flow 1: Searching Memories via Telegram

```
User (Telegram): What did I eat in Kyoto?

OpenClaw Agent:
  1. Parses query, identifies memory/food intent
  2. Calls omnimemory_search(query="food eating Kyoto Japan", context_types=["food"])
  3. Receives results (thumbnails if using /api/openclaw wrapper)
  4. Formats response with citations

Response (Telegram):
  "Based on your memories from Kyoto (March 2025), you had several memorable meals:
  
  🍜 **Ramen at Ichiran** (March 15)
  Classic tonkotsu ramen, you noted it was the best you'd ever had.
  [thumbnail]
  
  🍣 **Sushi Omakase at Gion** (March 16) 
  10-course omakase dinner, highlights included otoro and uni.
  [thumbnail]
  
  🍵 **Matcha and wagashi** (March 17)
  Tea ceremony experience in the traditional tea house.
  [thumbnail]"
```

### Flow 2: Daily Summary with OpenClaw Heartbeat

```
# OpenClaw heartbeat triggers at 10 PM

OpenClaw reads HEARTBEAT.md:
  "Check if there are new OmniMemory daily summaries to acknowledge"

OpenClaw Agent:
  1. Calls omnimemory_timeline(date="2025-01-30")
  2. Gets today's summary

If user configured notifications:
  Sends to Telegram: 
  "📅 Your day in review:
  - Morning yoga session
  - 3 work meetings (4 hours total)
  - Lunch with the team at Thai Garden
  - Evening walk in the neighborhood
  
  Any memories you'd like to add or correct?"
```

### Flow 3: Adding Memory via WhatsApp

```
User (WhatsApp): [sends photo of a dish]
User (WhatsApp): Amazing ramen at the new place downtown!

OpenClaw Agent:
  1. Detects image + context
  2. Calls omnimemory_ingest(
       content_type="photo",
       content="/tmp/telegram/ramen.jpg"
     )
  3. OmniMemory processes the image, extracts contexts

Response (WhatsApp):
  "Got it! I've saved this to your memories. The ramen looks delicious! 🍜
  I've tagged it as: food, restaurant, downtown, January 2025.
  
  Want me to remember anything specific about this place?"
```

---

## 6. API Endpoints Used

### Core OmniMemory Endpoints (existing)

- `GET /search?q=...&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&limit=...`
- `GET /timeline?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&limit=200`
- `POST /storage/upload-url` → presigned upload
- `POST /upload/ingest` → ingest uploaded media

### OpenClaw-Optimized Endpoints ✅ IMPLEMENTED

Located at `services/api/app/routes/openclaw.py`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/openclaw/search` | POST | Search memories with truncated summaries (500 chars) and thumbnail URLs |
| `/api/openclaw/timeline/{date}` | GET | Day summary with episodes formatted for tools |
| `/api/openclaw/ingest` | POST | Ingest media by storage_key (after upload) |
| `/api/openclaw/connection/test` | GET | Health check, returns user ID if authenticated |

**Request/Response Models:**

```python
# Search Request
class OpenClawSearchRequest(BaseModel):
    query: str
    date_from: Optional[str] = None  # ISO format YYYY-MM-DD
    date_to: Optional[str] = None
    context_types: Optional[list[str]] = None
    limit: int = 10

# Search Response
class OpenClawSearchResponse(BaseModel):
    success: bool = True
    total: int
    items: list[OpenClawMemoryItem]

class OpenClawMemoryItem(BaseModel):
    id: str
    title: str
    summary: str  # Truncated to 500 chars
    date: Optional[str]
    type: str
    thumbnail_url: Optional[str]  # Presigned URL
    keywords: list[str]
    score: Optional[float] = None

# Timeline Response
class OpenClawTimelineResponse(BaseModel):
    success: bool = True
    date: str
    daily_summary: Optional[str]
    episode_count: int
    episodes: list[OpenClawEpisode]
    highlights: list[str]

class OpenClawEpisode(BaseModel):
    title: str
    time_range: str  # "HH:MM - HH:MM"
    summary: str
    item_count: int

# Ingest Request/Response
class OpenClawIngestRequest(BaseModel):
    storage_key: str
    item_type: str  # photo, video, audio
    captured_at: Optional[str] = None
    provider: str = "openclaw"

class OpenClawIngestResponse(BaseModel):
    success: bool
    item_id: Optional[str] = None
    message: str
```

### Design Reference (from original plan):

```python
# omnimemory/services/api/app/routers/openclaw.py

from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user
from app.models import SearchRequest, TimelineRequest, IngestFromChatRequest

router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])

@router.post("/search")
async def search_for_openclaw(
    request: SearchRequest,
    user = Depends(get_current_user)
):
    """
    Search memories - optimized response format for OpenClaw tools.
    Returns: { total, items: [{ id, title, summary, date, type, thumbnail_url, keywords }] }
    """
    # Use existing search logic but format for tool consumption
    results = await search_memories(
        user_id=user.id,
        query=request.query,
        filters=request.filters,
        limit=request.limit
    )
    
    return {
        "total": results.total,
        "items": [
            {
                "id": str(item.id),
                "title": item.title,
                "summary": item.summary[:500],  # Truncate for tool response
                "date": item.event_time_utc.isoformat() if item.event_time_utc else None,
                "type": item.context_type,
                "thumbnail_url": generate_signed_url(item.thumbnail_key) if item.thumbnail_key else None,
                "keywords": item.keywords[:10] if item.keywords else []
            }
            for item in results.items
        ]
    }

@router.get("/timeline")
async def timeline_for_openclaw(
    date: str,
    episodes: bool = True,
    items: bool = False,
    user = Depends(get_current_user)
):
    """
    Get day summary - optimized for OpenClaw tools.
    """
    timeline = await get_timeline_day(
        user_id=user.id,
        date=date,
        include_episodes=episodes,
        include_items=items
    )
    
    return {
        "date": date,
        "daily_summary": timeline.daily_summary,
        "episodes": [
            {
                "title": ep.title,
                "start_time": ep.start_time.strftime("%H:%M") if ep.start_time else None,
                "end_time": ep.end_time.strftime("%H:%M") if ep.end_time else None,
                "summary": ep.summary,
                "item_count": len(ep.source_item_ids) if ep.source_item_ids else 0
            }
            for ep in (timeline.episodes or [])
        ],
        "highlights": timeline.highlights or []
    }

@router.post("/ingest")
async def ingest_from_chat(
    request: IngestFromChatRequest,
    user = Depends(get_current_user)
):
    """
    Ingest media uploaded via OpenClaw (storage key already created).
    """
    item = await ingest_uploaded_media(
        user_id=user.id,
        storage_key=request.storage_key,
        item_type=request.item_type,
        captured_at=request.captured_at,
        provider="openclaw"
    )
    
    return {"success": True, "item_id": str(item.id), "message": "Memory saved"}
```

---

## 7. Implementation Roadmap

### Phase 1: Basic Tool Integration ✅ COMPLETED

**OmniMemory Backend:**
1. [x] Created `/api/openclaw/*` endpoints in `services/api/app/routes/openclaw.py`:
   - `POST /api/openclaw/search` - Search with truncated summaries + thumbnail URLs
   - `GET /api/openclaw/timeline/{date}` - Day summary with episodes
   - `POST /api/openclaw/ingest` - Ingest media via storage key
   - `GET /api/openclaw/connection/test` - Health check endpoint
2. [x] Registered router in `services/api/app/routes/__init__.py`

**OpenClaw Skills:**
3. [x] Created skill directory at `~/.openclaw/skills/omnimemory/`
4. [x] Created `SKILL.md` with metadata and documentation
5. [x] Created shell wrapper scripts:
   - `omnimemory_search.sh` - Search with JSON payload via curl
   - `omnimemory_timeline.sh` - Get day timeline
   - `omnimemory_ingest.sh` - Upload + ingest flow with presigned URLs

**Testing:**
6. [ ] Test search flow via OpenClaw CLI
7. [ ] Test via Telegram/WhatsApp channels

### Phase 2: Settings & Configuration (Pending)

1. [ ] Add `OpenClawIntegrationSettings` to OmniMemory settings model
2. [ ] Create Settings UI section for OpenClaw connection
3. [ ] Add OpenClaw-specific settings endpoints (`GET/PATCH /settings/openclaw`)
4. [ ] Store API token securely

### Phase 3: Memory Sync ✅ COMPLETED

1. [x] Implement `OpenClawMemorySync` class in `services/api/app/integrations/openclaw_sync.py`
2. [x] Hook into daily summary generation pipeline (`tasks/episodes.py:_update_daily_summary`)
3. [x] Sync runs inline (no separate Celery task needed - file writes are fast)
4. [ ] Add sync toggle to settings UI
5. [ ] Test memory files are created at `~/.openclaw/memory/*.md`

**Implementation Details:**
- Created `services/api/app/integrations/openclaw_sync.py` with `OpenClawMemorySync` class
- Added sync hook to `_update_daily_summary()` after embeddings upsert
- Handles both auto-generated and user-edited summaries
- Deletes OpenClaw memory files when no episodes exist for a day
- Reads config from user settings: `settings.openclaw.sync_memory`

**To enable sync, update user settings:**
```json
{
  "openclaw": {
    "sync_memory": true,
    "workspace": "~/.openclaw"
  }
}
```

### Phase 4: Chat UI Updates (Pending)

1. [ ] Add OpenClaw connection banner to Chat tab
2. [ ] Show "Use Telegram/WhatsApp" prompt when connected
3. [ ] Keep basic RAG chat functional as fallback

### Phase 5: Polish & Documentation (Pending)

1. [ ] Write user-facing setup guide
2. [ ] Add error handling for connection failures
3. [ ] Test end-to-end flows

### Phase 6: TypeScript Extension for Clawhub (Future)

1. [ ] Create TypeScript extension at `~/.openclaw/extensions/omnimemory/`
2. [ ] Implement tools using TypeBox schemas
3. [ ] Test with plugin API
4. [ ] Publish to Clawhub

---

## 8. Learning from OpenClaw's Setup & Onboarding

OpenClaw has a well-designed terminal-based onboarding flow that OmniMemory can learn from.

### OpenClaw's Two-Phase Onboarding

**Phase 1: Terminal CLI Wizard** (not LLM-based)

OpenClaw uses `@clack/prompts` for interactive terminal menus:

```bash
openclaw onboard --install-daemon
```

This wizard guides users through:
- Security acknowledgement
- Gateway mode selection (local/remote)
- Auth provider setup (Anthropic OAuth, OpenAI, etc.)
- Model selection
- Channel configuration (Telegram, WhatsApp, etc.)
- Gateway service installation

Implementation: `src/wizard/onboarding.ts` + `src/wizard/clack-prompter.ts`

**Phase 2: Agent Bootstrap Ritual** (LLM-based)

After the technical setup, OpenClaw runs an **agent-powered personalization flow**:

1. Seeds initial workspace files (`AGENTS.md`, `BOOTSTRAP.md`, `IDENTITY.md`)
2. Agent asks the user questions one at a time
3. Agent writes answers to `IDENTITY.md`, `USER.md`, `SOUL.md`
4. `BOOTSTRAP.md` is deleted after completion (only runs once)

This is where the LLM agent loop is used — to personalize the assistant based on user preferences.

### OmniMemory Takeaways

**1. Terminal-based Setup Wizard**

OmniMemory could add a CLI setup command using `@clack/prompts`:

```bash
omnimemory setup
```

Interactive steps:
- API key configuration (Gemini, OpenAI embeddings)
- Storage settings (local vs cloud)
- OpenClaw integration (optional)
- Google Photos OAuth (if desired)
- Ingestion preferences

```typescript
// Example using @clack/prompts
import { intro, select, text, confirm, outro } from "@clack/prompts";

async function runOmniMemorySetup() {
  await intro("OmniMemory Setup");
  
  const apiKey = await text({
    message: "Enter your Gemini API key:",
    placeholder: "AIza...",
    validate: (v) => v.startsWith("AIza") ? undefined : "Invalid key format"
  });
  
  const openclawEnabled = await confirm({
    message: "Connect to OpenClaw for chat via Telegram/WhatsApp?",
    initialValue: true
  });
  
  if (openclawEnabled) {
    const gatewayUrl = await text({
      message: "OpenClaw Gateway URL:",
      initialValue: "http://localhost:18789"
    });
    // Test connection...
  }
  
  const focusAreas = await multiselect({
    message: "What should I focus on when analyzing your photos?",
    options: [
      { value: "food", label: "Food & Dining", hint: "Identify dishes, cuisines, restaurants" },
      { value: "travel", label: "Travel & Places", hint: "Locations, landmarks, trips" },
      { value: "people", label: "People & Social", hint: "Who you're with, events" },
      { value: "fitness", label: "Fitness & Health", hint: "Workouts, activities" },
      { value: "fashion", label: "Fashion & Style", hint: "Outfits, accessories" }
    ],
    initialValues: ["food", "travel", "people"]
  });
  
  // Save settings...
  await outro("Setup complete! Run 'omnimemory start' to begin.");
}
```

**2. Agent-Powered Personalization (via OpenClaw)**

When OpenClaw integration is enabled, OmniMemory could trigger a personalization chat:

```markdown
<!-- ~/.openclaw/skills/omnimemory/BOOTSTRAP.md (one-time setup) -->

# OmniMemory Personalization

This is your first time using OmniMemory. Let me learn about you to better organize your memories.

## Questions to ask (one at a time)

1. "What are your main interests or hobbies? (This helps me categorize your activities)"
2. "Do you travel often? Which regions/countries do you visit most?"
3. "What kind of food do you enjoy? Any dietary preferences?"
4. "Who are the important people in your life I should recognize? (family, close friends)"
5. "Are there any specific things you want me to track or highlight in your photos?"

## After answering

- Write user preferences to OmniMemory settings via `omnimemory_settings` tool
- Create custom focus areas based on answers
- Remove this BOOTSTRAP.md file
```

This way, OpenClaw's agent loop handles the conversational onboarding, and OmniMemory receives structured preferences.

**3. Customizable Extraction Prompts**

Just like OpenClaw's workspace files customize the agent's behavior, OmniMemory could let users customize VLM prompts:

```
~/omnimemory/
├── prompts/
│   ├── image_analysis.md      # Custom VLM prompt for photos
│   ├── video_understanding.md # Custom prompt for video analysis
│   └── episode_summary.md     # How to summarize episodes
├── focus_areas/
│   ├── food.md                # "Always note cuisine type, dish name, restaurant"
│   ├── travel.md              # "Extract location, landmark, trip context"
│   └── fitness.md             # "Note exercise type, duration, metrics"
└── taxonomy.md                # Custom context types to extract
```

The ingestion pipeline reads these files and includes them in VLM prompts:

```python
async def build_vlm_prompt(user_id: str, item_type: str) -> str:
    """Build VLM prompt with user customizations."""
    base_prompt = load_default_prompt(item_type)
    
    # Load user's custom prompt if exists
    custom_prompt_path = Path(f"~/omnimemory/prompts/{item_type}_analysis.md")
    if custom_prompt_path.exists():
        custom_prompt = custom_prompt_path.read_text()
        base_prompt = f"{base_prompt}\n\n## User Customizations\n{custom_prompt}"
    
    # Load active focus areas
    focus_areas = await get_user_focus_areas(user_id)
    for area in focus_areas:
        area_path = Path(f"~/omnimemory/focus_areas/{area}.md")
        if area_path.exists():
            area_prompt = area_path.read_text()
            base_prompt = f"{base_prompt}\n\n### {area.title()} Focus\n{area_prompt}"
    
    return base_prompt
```

### Combined Setup Flow (OmniMemory + OpenClaw)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        OmniMemory + OpenClaw Setup                          │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  Step 1: Install OpenClaw (if not installed)                               │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  curl -fsSL https://openclaw.dev/install.sh | bash                  │  │
│  │  openclaw onboard --install-daemon                                   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  Step 2: Install OmniMemory                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  pip install omnimemory                                             │  │
│  │  omnimemory setup                                                   │  │
│  │                                                                     │  │
│  │  > OmniMemory Setup                                                 │  │
│  │  > ─────────────────                                                │  │
│  │  > Enter your Gemini API key: AIza...                               │  │
│  │  > Connect to OpenClaw? (Y/n): Y                                     │  │
│  │  > OpenClaw Gateway URL: http://localhost:18789                      │  │
│  │  > ✓ Connected to OpenClaw gateway                                   │  │
│  │  >                                                                  │  │
│  │  > Focus areas (space to select):                                   │  │
│  │  >   [x] Food & Dining                                              │  │
│  │  >   [x] Travel & Places                                            │  │
│  │  >   [ ] Fitness & Health                                           │  │
│  │  >   [ ] Fashion & Style                                            │  │
│  │  >                                                                  │  │
│  │  > ✓ Settings saved                                                 │  │
│  │  > ✓ OpenClaw tools registered (omnimemory_search, omnimemory_...)   │  │
│  │  > ✓ Memory sync enabled (~/.openclaw/memory/*.md)                      │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  Step 3: Start services                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  omnimemory start                  # Start API + workers            │  │
│  │  # OpenClaw gateway already running as daemon                        │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  Step 4: Agent-powered personalization (via OpenClaw)                      │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  User opens Telegram:                                               │  │
│  │                                                                     │  │
│  │  🤖 OpenClaw: "I see you've connected OmniMemory! Let me learn      │  │
│  │      about you to better organize your memories.                    │  │
│  │                                                                     │  │
│  │      What are your main hobbies or interests?"                      │  │
│  │                                                                     │  │
│  │  👤 User: "I love cooking, traveling to Asia, and hiking"          │  │
│  │                                                                     │  │
│  │  🤖 OpenClaw: "Great! I'll focus on food photography, Asian travel, │  │
│  │      and outdoor activities. Who are the important people I should  │  │
│  │      recognize in your photos?"                                     │  │
│  │                                                                     │  │
│  │  👤 User: "My wife Sarah and our dog Max"                          │  │
│  │                                                                     │  │
│  │  🤖 OpenClaw: "Got it! I've saved these preferences to OmniMemory.  │  │
│  │      You're all set! Try uploading some photos or ask me about     │  │
│  │      your memories anytime."                                        │  │
│  │                                                                     │  │
│  │  [Agent calls omnimemory_settings tool to save preferences]         │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### Benefits of This Approach

| Aspect | OmniMemory Standalone | OmniMemory + OpenClaw |
|--------|----------------------|---------------------|
| Setup | Web UI wizard | Terminal CLI + agent chat |
| Personalization | Form-based settings | Conversational via Telegram |
| Prompt customization | Settings page | Workspace files + agent edits |
| Ongoing refinement | Manual settings | "Hey, focus more on food" via chat |

---

## 9. Future Enhancements

### Bidirectional Sync
- OpenClaw conversations could feed back into OmniMemory as memory entries
- Chat history becomes searchable alongside photos/videos

### Shared Authentication
- OAuth flow where OmniMemory login also authorizes OpenClaw access
- Single sign-on across both systems

### Proactive Insights
- OpenClaw heartbeat queries OmniMemory for "this day last year" memories
- Weekly digest generated by OpenClaw using OmniMemory data

### Voice Integration
- OpenClaw voice channel (if added) can query memories
- Voice memos ingested via OpenClaw → OmniMemory

---

## References

### OpenClaw Documentation
- [OpenClaw Architecture Deep Dive](/concepts/architecture-deep-dive)
- [OpenClaw Gateway Execution & Memory](/concepts/gateway-execution-and-memory)
- [OpenClaw Onboarding (macOS)](/start/onboarding) — Agent bootstrap ritual
- [OpenClaw CLI Onboard](/cli/onboard) — Terminal wizard reference
- [OpenClaw Install](/install) — Installation guide

### OmniMemory Documentation
- [OmniMemory PRD](/concepts/lifelog-mvp-prd)
- [OmniMemory Dev Plan](/concepts/lifelog-mvp-dev-plan)

### Key Source Files
- `src/wizard/onboarding.ts` — OpenClaw terminal wizard
- `src/wizard/clack-prompter.ts` — Interactive prompts (select, confirm, text)
- `src/agents/workspace.ts` — Workspace bootstrap files
- `docs/reference/templates/AGENTS.md` — Default agent instructions template
