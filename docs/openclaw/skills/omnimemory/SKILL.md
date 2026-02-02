---
name: omnimemory
description: Search and manage personal memories from OmniMemory lifelog app
homepage: https://github.com/gozhiyuan/omnimemory
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

OmniMemory is a personal memory assistant that stores and organizes your photos, videos, and life events. When connected, you can search and retrieve memories, customize prompts, and manage settings.

## Configuration

There are two ways to configure OmniMemory access:

### Option 1: OpenClaw Config (Recommended)

Add to `~/.openclaw/openclaw.json` under `skills.entries`:

```json
{
  "skills": {
    "entries": {
      "omnimemory": {
        "enabled": true,
        "env": {
          "OMNIMEMORY_API_URL": "http://localhost:8000",
          "OMNIMEMORY_API_TOKEN": "omni_sk_your_api_key_here"
        }
      }
    }
  }
}
```

### Option 2: Environment Variables

Set these in your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
export OMNIMEMORY_API_URL="http://localhost:8000"
export OMNIMEMORY_API_TOKEN="omni_sk_your_api_key_here"
```

Environment variables take precedence over the JSON config.

### Getting an API Key

Generate an API key from OmniMemory:

```bash
curl -X POST http://localhost:8000/settings/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name": "OpenClaw Integration"}'
```

The key (format: `omni_sk_...`) is shown only once - save it immediately!

## When to Use

Use OmniMemory tools when the user:
- Asks about past events, activities, or experiences
- Wants to see photos or videos from specific times or places
- Asks "what did I do" questions about their day/week/month
- Mentions people, places, or things from their life
- Wants to save a new photo/video/audio memory with annotations
- Wants to customize how memories are analyzed (prompts)
- Wants to update their preferences

## Available Tools

### omnimemory_search

Search memories by natural language query. Supports date ranges.

**Usage:**
```bash
./omnimemory_search.sh "query" [limit] [date_from] [date_to] [context_types]
```

**Examples:**
- "What did I eat last week?" -> `./omnimemory_search.sh "food meals eating" 10 2025-01-23`
- "Photos from Tokyo trip" -> `./omnimemory_search.sh "Tokyo Japan travel"`
- "Times with Alice" -> `./omnimemory_search.sh "Alice"`

### omnimemory_timeline

Get a day's summary with episodes and highlights.

**Usage:**
```bash
./omnimemory_timeline.sh "YYYY-MM-DD" [tz_offset_minutes]
```

**Examples:**
- "What did I do yesterday?" -> `./omnimemory_timeline.sh "2025-01-29"`
- "Summarize last Monday" -> `./omnimemory_timeline.sh "2025-01-27"`

### omnimemory_ingest

Add new media with optional annotations (description, tags, people, location).

**Usage:**
```bash
./omnimemory_ingest.sh "/path/to/file" [item_type] [options...]
```

**Options:**
- `--description "text"` - Add a description/annotation
- `--tags "tag1,tag2"` - Add comma-separated tags
- `--people "name1,name2"` - Add people in the memory
- `--location "name"` - Location name
- `--lat 12.34` - Latitude
- `--lng 56.78` - Longitude
- `--captured-at "ISO"` - Override capture timestamp (ISO 8601)
- `--auto-captured-at` - Try to extract timestamp from file metadata
- `--use-file-mtime` - Use file modified time as capture time

**Examples:**
- Basic upload: `./omnimemory_ingest.sh "/tmp/photo.jpg"`
- With annotation: `./omnimemory_ingest.sh "/tmp/photo.jpg" --description "Coffee with Alice at Blue Bottle" --tags "coffee,friends" --people "Alice"`
- With location: `./omnimemory_ingest.sh "/tmp/photo.jpg" --location "Blue Bottle Coffee" --lat 37.7749 --lng -122.4194`

### omnimemory_prompt

Manage prompt templates for memory analysis.

**Usage:**
```bash
./omnimemory_prompt.sh list                              # List all prompts
./omnimemory_prompt.sh get <name>                        # Get prompt content
./omnimemory_prompt.sh update <name> <content|- > [--sha256 <hash>] [--file <path>]  # Update prompt
./omnimemory_prompt.sh delete <name>                     # Delete user override
```

**Available prompts:**
- `image_analysis` - How photos are analyzed
- `video_chunk` - How video segments are analyzed
- `audio_chunk` - How audio segments are analyzed
- `episode_summary` - How episodes are summarized
- `chat_system` - Chat assistant personality
- `agent_system` - Memory agent behavior

**Updatable via OpenClaw API:**
- `image_analysis` - Photo understanding prompt (tags, summary, contexts)
- `video_chunk` - Video chunk understanding prompt (transcript + contexts)
- `audio_chunk` - Audio chunk understanding prompt (transcript + contexts)
- `episode_summary` - Episode summarization prompt
- `chat_system` - Chat assistant system prompt
- `agent_system` - Memory agent system prompt
- `agent_action` - Memory agent action selection prompt
- `agent_response` - Memory agent response prompt

**Examples:**
- List prompts: `./omnimemory_prompt.sh list`
- Get prompt: `./omnimemory_prompt.sh get image_analysis`
- Update (requires sha256 for safety):
  1. Get current: `./omnimemory_prompt.sh get image_analysis` (note the sha256)
  2. Update: `./omnimemory_prompt.sh update image_analysis "Your new prompt content..." --sha256 abc123...`

### omnimemory_settings

Manage user settings and preferences.

**Usage:**
```bash
./omnimemory_settings.sh get                    # Get current settings
./omnimemory_settings.sh set <key> <value>      # Set a setting (JSON or string)
./omnimemory_settings.sh patch '<json>'         # Patch multiple settings
```

**Allowed keys:** `openclaw`, `profile`, `preferences`

**Examples:**
- Get settings: `./omnimemory_settings.sh get`
- Enable sync: `./omnimemory_settings.sh set openclaw '{"syncMemory": true}'`
- Set preferences: `./omnimemory_settings.sh patch '{"preferences": {"timezone": "America/Los_Angeles", "language": "en"}}'`

### omnimemory_preferences

Manage lightweight user preferences for analysis focus and default annotations.

**Usage:**
```bash
./omnimemory_preferences.sh get
./omnimemory_preferences.sh set '{"focus_tags":["food","people"]}'
./omnimemory_preferences.sh focus --tags "food,people" --people "Alice"
./omnimemory_preferences.sh defaults --tags "food" --people "Alice" --description "Focus on meals"
```

### omnimemory_detect_time

Detect a likely capture timestamp from file metadata before ingestion.

**Usage:**
```bash
./omnimemory_detect_time.sh "/path/to/file"
```

## Response Formatting

When returning search results:
- Include dates and times for temporal context
- Mention thumbnail URLs if available so images can be displayed
- Summarize episodes rather than listing every item
- Cite specific memories when answering questions

When user adds annotations during ingest:
- Confirm what was saved
- Mention that the annotation will be preserved even if the photo is reprocessed

## Error Handling

If the OmniMemory API is not reachable:
1. Check if OmniMemory is running (`curl http://localhost:8000/health`)
2. Verify `OMNIMEMORY_API_URL` is set correctly
3. Check if authentication token is valid (if auth is enabled)

For prompt updates, 412 errors mean the sha256 doesn't match - get the latest version first.
