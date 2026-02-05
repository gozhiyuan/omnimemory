# RAG Details (OmniMemory)

This document explains how the current RAG (retrieval-augmented generation) flow works in OmniMemory and what metadata it can access (labels, context, keywords, entities, timestamps).

## Overview

RAG is built on top of `ProcessedContext` records created during ingestion. Each context has a label-like type (`context_type`), a title, a summary, keywords, entities, timestamps, and source item references. These are embedded into Qdrant for retrieval and then re-hydrated from Postgres for prompt building.

Key points:
- Qdrant stores vectors + a small payload for filtering/reranking.
- Postgres stores the full context text, labels, and metadata.
- Chat reuses recent daily summaries, chat history, and the retrieved contexts.

## Data used for retrieval

Each `ProcessedContext` record includes:
- `context_type`: label-like category (examples: `activity_context`, `daily_summary`, `memory_context`).
- `title`: human-readable label for the context.
- `summary`: primary text used for the final answer.
- `keywords`: normalized tags.
- `entities`: extracted entities (people, objects, places, etc).
- `event_time_utc`, `start_time_utc`, `end_time_utc`.
- `source_item_ids`: links back to `SourceItem` rows (photos, videos, audio).

During indexing, the system stores:
- `vector_text` (built from title/summary/keywords, stored in Postgres).
- Qdrant payload fields: `context_id`, `context_type`, `is_episode`, `event_time_utc`, `source_item_ids`, `entities`, `user_id`.

Related tables used by chat:
- `processed_content`: captions and transcripts.
- `derived_artifacts`: previews, keyframes, transcript storage keys.
- `daily_summaries`: recent daily summaries for prompt grounding.
- `chat_sessions`, `chat_messages`, `chat_attachments`: persisted chat history and images.

## Retrieval flow

1) **Intent classification** (heuristic + LLM):
   - `memory_query` → run retrieval
   - `meta_question` / `greeting` / `clarification` → skip retrieval
2) **Query understanding**:
   - Date parsing (heuristic first, LLM fallback)
   - Entity extraction (optional Gemini-based)
   - Query type classification (`fact`, `summary`, `browse`, `compare`, `count`)
3) **Hybrid retrieval**:
   - Qdrant vector search
   - Optional Postgres FTS search
   - Reciprocal Rank Fusion (RRF) to combine lists
4) **Hard filters + scoring**:
   - Date window filter (strict)
   - Optional context_type filter (from retrieval planner)
   - Entity match boosts, recap boosts, activity context boosts, daily penalties
5) **LLM rerank (optional)**:
   - Applied to top N candidates (default for `fact`/`summary`)
   - Recency queries get a final recency sort
6) **Evidence selection**:
   - Dedupe + trim to 6–8 contexts
7) **Prompt assembly**:
   - System prompt + daily summaries + history + memory blocks
8) **Optional verification**:
   - Grounding check for hallucination detection
9) **Telemetry**:
   - Query plan + candidate stats stored with chat messages

### Query parsing details

- **Date parsing:** supports explicit dates (YYYY-MM-DD, MM/DD, “Dec 30”), relative ranges (today/yesterday/last week/last month), and custom ranges.
- **Entity extraction:** optional Gemini-based entity extraction for the user query (`chat_entity_extraction_enabled`) to boost matching contexts.
- **Timezone:** query parsing and date filtering use `tz_offset_minutes` so “yesterday” matches the user’s local day.

## Prompt composition

The chat prompt is assembled by `response_generator.py` and includes:
- system instruction
- recent daily summaries (last 7 days, for recap-style queries)
- conversation history
- relevant memory blocks (timestamp, title, summary, context type)
- user question

Each memory block includes timestamp, title, summary, and location when available.

### Image-assisted queries

For `/chat/image`, the uploaded image is analyzed by a VLM to produce a short description. That description is appended to the query and used for retrieval. The image is stored as a chat attachment, not ingested into the memory pipeline.

## Can RAG access labels and context?

Yes.

- Labels are available through `context_type` and `title`.
- Context content includes `summary`, `keywords`, `entities`, and timestamps.
- Qdrant only stores the payload needed for filtering and reranking; the full label and context text are retrieved from Postgres after the vector search.

## Output and citations

The API response includes:
- `message`: the assistant’s answer.
- `sources`: a list of context-backed citations with `context_id`, `source_item_id`, `timestamp`, `thumbnail_url`, `snippet`, and `score`.

Thumbnails are resolved from `derived_artifacts` (preview images or keyframes) and signed via the storage provider.

When debug is enabled (`debug=true`), the response also includes:
- `query_plan`: structured plan (intent, query_type, time_range, entities)
- `debug`: candidate counts, evidence size, prompt budget, and retrieval config

## Diagram

```mermaid
flowchart TD
  A[Ingestion: photos, videos, audio] --> B[Pipeline: OCR / VLM / transcription]
  B --> C[ProcessedContext + ProcessedContent in Postgres]
  C --> D[Build vector_text + embeddings]
  D --> E[Qdrant vector index]

  F[User question] --> G[Parse date/entities]
  G --> H[Embed query + search Qdrant]
  H --> I[Re-rank hits]
  I --> J[Fetch full contexts from Postgres]
  J --> K[Prompt assembly]
  K --> L[LLM answer + citations]
```

## Configuration knobs

Common settings that affect RAG behavior:
- `chat_context_limit`: number of contexts to inject per query.
- `chat_history_limit`: number of chat turns to include.
- `chat_entity_extraction_enabled`: toggle query entity extraction.
- `chat_verification_enabled`: toggle grounding verification.
- `embedding_provider` / `embedding_model`: used to embed context and query.
- `qdrant_collection`: collection name for context vectors.

## Memory API (for shared toolset + agents)

These endpoints expose the same retrieval logic for external tools (OpenClaw, ADK agent, etc.):
- `POST /memory/search`
- `GET /memory/timeline/{date}`
- `GET /memory/episode/{episode_id}`
- `GET /memory/context/{context_id}`

## Notes and limits

- Only the top-K contexts are retrieved for each request.
- Context payload in Qdrant is minimal; the full text lives in Postgres.
- Surprise detection is not a dedicated pipeline step yet. It can be done on-demand in a downstream agent or added as an optional enrichment step during ingestion.
