# Lifelog AI - MVP Product Requirements Document

> **Status:** MVP-focused version emphasizing core data and memory architecture
> **Last Updated:** December 31, 2025
>
> **Canonical detailed design:** `docs/minecontext/lifelog_ingestion_rag_design.md`

---

## 1. Executive Summary

Build an AI-powered personal memory assistant that ingests multimodal data from 3rd-party sources and user uploads, processes and organizes it into a queryable knowledge base, and provides conversational access through a web-based chat interface.

**MVP Scope (8-12 weeks):** React + Vite single-page web app (tabs: Dashboard, Timeline, Chat, Ingest) backed by FastAPI + Celery, focusing on manual uploads, Google Photos picker-based ingest, the ingestion pipeline, timeline/day summaries, and RAG-powered chat.

---

## 2. MVP Goals & Success Metrics

### Goals
- Prove the data ingestion → processing → memory → retrieval → chat flow end-to-end
- Establish solid data foundations that won't require reprocessing
- Demonstrate accurate memory retrieval for temporal, spatial, and entity-based queries
- Enable users to connect 3+ data sources and chat about their memories within 2 weeks

### Success Metrics
- **Data completeness:** 95%+ of historical data successfully ingested from connected sources
- **Query accuracy:** 80%+ of user queries retrieve relevant context (measured via feedback)
- **Response time:** < 3s for chat responses
- **User satisfaction:** 4/5 rating from pilot users (n=20)

**Measurement & Instrumentation**
- Supabase event tables + Prometheus counters for ingestion success/failure, latency, and queue depth
- AI usage events (tokens + cost) logged per model call and surfaced in the Dashboard
- Frontend feedback modal with structured thumbs-up/down + free-form notes logged per chat turn
- OpenTelemetry traces stitched across upload → processing → retrieval for p95 latency tracking
- Weekly metric review dashboard (Metabase) covering completeness, accuracy, retention, and cost per user

---

## 3. MVP Feature Scope

### IN SCOPE ✅

**3.1 Data Layer**
- Manual upload for photos/videos/audio with batch support and optional time window overrides per day
- Google Photos connector (OAuth + Picker API): user launches the picker, selects items, we copy the chosen media into Supabase Storage for rendering, and enqueue ingestion; no automated full-library/delta sync (API limitation)
- Metadata extraction (MVP): content type, file size, EXIF/container metadata, provider metadata, normalized timestamps (`event_time_utc`)
- Deduplication (MVP): exact dedup (SHA256) + near-duplicate dedup for images (pHash) to control costs for frequent capture sources
- Multi-context extraction per media item (1..N contexts) using a taxonomy (activity/social/location/food/emotion/entity/knowledge)

**3.2 Processing Pipeline**
- Shared pre-processing: download/fetch bytes, sniff MIME, extract technical metadata, compute `content_hash` and perceptual hash where applicable
- Image: OCR + VLM batch analysis → 1..N contexts per image → optional semantic merge into episode contexts → text embeddings → Qdrant upsert (context IDs)
- Video: keyframes + t=0 poster → chunked Gemini understanding (transcript + contexts per chunk) → contexts → embeddings → Qdrant upsert
- Audio: chunked Gemini understanding (transcript + contexts per chunk) → contexts → embeddings → Qdrant upsert
- Document: MVP stores metadata + a generic context (full text extraction is post-MVP)
- Derived artifacts are versioned and cacheable so reprocessing is possible without re-uploading media (see `docs/minecontext/lifelog_ingestion_rag_design.md`)
- Media guardrails + tooling (MVP defaults):
  - Hard limits: `media_max_bytes=1GB`, `video_max_duration_sec=300`, `audio_max_duration_sec=3600`
  - Gemini request caps enforced: `video_understanding_max_bytes≈19MB`, `audio_understanding_max_bytes≈19MB`
  - Chunking targets: `media_chunk_target_bytes≈10MB`, `video_chunk_duration_sec=60`, `audio_chunk_duration_sec=300`, `media_chunk_max_chunks=240`
  - `ffmpeg` is required for media metadata, keyframes, and chunking; if missing, media-understanding steps are skipped
  - Transcript payloads > `transcription_storage_max_bytes` are stored in object storage and referenced by key

**3.3 Memory Layer**
- **Vector store (Qdrant):** store embeddings for `processed_contexts` (and optional transcript/doc chunks) keyed by **context_id**
- **Postgres:** source item metadata + derived artifacts + processed contexts + daily summaries; supports time/entity filtering
- **Episode contexts:** merge raw contexts into higher-signal “episodes” (time-window clustering + LLM merge decision)
- **Daily summaries:** updated whenever episodes change (stored as `processed_contexts` with `context_type=daily_summary`); embedded + indexed for fast “broad” queries
- **Retrieval-first principle:** pre-filter by time/type/entity, then vector search within the filtered set, then synthesize with citations

**3.4 Model Layer**
- LLM/VLM/ASR: Gemini 2.5 Flash-Lite for image/video/audio understanding + summarization
- Text embeddings: Gemini `gemini-embedding-001` stored in Qdrant
- Chat (web): Gemini via `@google/genai` with RAG citations

**3.5 Application Layer (Web Only)**
- User authentication (Supabase Auth: email/password + Google OAuth) gating a single React + Vite SPA shell (`Layout` + `App.tsx` view switcher)
- **Ingest Tab:** Combined drag-and-drop upload interface and Google Photos connection card with OAuth + Picker launch, selection count, and ingest status
- **Chat Tab:** Conversational UI with memory-powered responses, source citations, and daily summary context chips
- **Timeline Tab:** Day/week/month/year views, episode list with drill-down details, daily summary, search bar, and per-day upload flow
- **Dashboard Tab:** Ingestion stats, storage usage, connected-source health indicators, and AI usage (tokens + cost) with date-range filtering

**3.6 Infrastructure**
- **Auth/DB/Storage:** Supabase (Postgres + Auth + Object Storage)
- **Vector DB:** Qdrant Cloud (start) → self-hosted Qdrant (scale)
- **API Backend:** FastAPI (Python)
- **Task Queue:** Celery + Redis for async processing
- **Web Frontend:** React 19 + TypeScript SPA bundled with Vite (local dev `npm run dev`, prod via Cloud Storage + Cloud CDN or Cloud Run static hosting)
- **Local Tooling:** Make targets wrap `orchestration/docker-compose.dev.yml` to launch Postgres/Redis/Qdrant; backend uses `uv` for dependency management
- **Cloud:** Start with Supabase + Qdrant Cloud + Cloud Run (FastAPI + Celery) while serving the SPA from Cloud Storage/Cloud CDN; migrate to more GCP-native services if commercialization requires
- **Security Baseline:** Encrypt at rest/in transit, store OAuth tokens with AES-256 + rotation, implement user data deletion workflow within 24h, document GDPR-compliant privacy policy

### OUT OF SCOPE (Post-MVP) ❌
- Mobile apps (iOS/Android)
- Desktop capture agent
- Automated screenshot/video capture
- Vlog generation
- Automated Google Photos full-library backfill/delta sync (Picker API does not allow it)
- Additional connectors beyond Google Photos (Apple Photos export/agent, Google Drive, Oura Ring, Spotify, etc.)
- IoT/device ingestion (e.g., ESP32 camera uploading periodic snapshots)
- Shareable mini-chatbots
- Advanced graph visualizations
- Multi-user collaboration
- Face/voice identity (enrollment, matching, confirmation)

---

## 4. Architecture Deep Dive

### 4.1 Data Layer Architecture

#### Problem: How to handle 3rd-party data ingestion?

**Current approach (MVP):** Picker-based, user-selected Google Photos ingest

**Rationale:**
1. **API constraint:** Google Photos API does not allow unattended full-library sync; Picker requires user selection.
2. **Reliable rendering:** Copying selected items into Supabase Storage avoids expired Google URLs and enables signed delivery in the app.
3. **Deterministic ingestion:** User explicitly chooses what to ingest; dedupe skips already imported media IDs.

**Implementation Pattern:**

```
User-driven ingest (per picker session):
1. User connects Google Photos via OAuth, then opens the Picker.
2. Picker returns selected media IDs; backend fetches bytes for those items.
3. Copy media to Supabase Storage (previews/original as configured) and create source_item records.
4. Enqueue processing (captions/OCR/embeddings) and emit timeline entries.
```

> **Note:** Automated backfill/delta sync is blocked by the Picker API. To achieve passive ingestion later, consider an additional capture path (e.g., ESP32 camera agent) or future connector work once API/permissions allow.

#### MVP ingestion pipeline (current implementation)

1) **Canonical timeline timestamp**
   - `event_time_utc`, `event_time_source`, `event_time_confidence` stored on `source_items`.
   - Priority: EXIF/container → provider metadata → client-provided → server receive time.
   - Client upload includes `client_tz_offset_minutes` to resolve naive EXIF timestamps.

2) **Dedup gates (cost control)**
   - Exact dedup: `content_hash` (SHA256) per user/provider.
   - Near-duplicate: `pHash` for images with rolling-window comparison.

3) **Versioned derived artifacts**
   - All step outputs persist as `derived_artifacts` with `producer_version` + `input_fingerprint`.
   - Pipeline is idempotent and cacheable.

4) **Multi-context extraction**
   - Images: OCR + Gemini VLM → 1..N contexts (taxonomy in `docs/minecontext/lifelog_ingestion_rag_design.md`).
   - Video/audio: chunked Gemini understanding → transcript + contexts per chunk.

5) **Context-level embeddings in Qdrant**
   - Embeddings stored per `processed_contexts` (context IDs).
   - Payload includes `user_id`, `context_type`, `event_time_utc`, `source_item_ids`, `is_episode`.

6) **Episodes + daily summaries**
   - Episodes generated from item contexts (time gap + similarity).
   - Daily summaries updated when episodes change, stored as `processed_contexts` (`context_type=daily_summary`).

**Storage Strategy:**
- **Source metadata:** Store provider IDs, timestamps, and normalized metadata in Postgres
- **Processed artifacts:** Store captions, contexts, OCR text, transcripts, and embeddings metadata in Postgres + Qdrant
- **Original files:** For Google Photos picker selections and manual uploads, copy media into Supabase Storage (originals + previews/posters) so the app can render reliably; keep provider IDs for dedupe

#### Storage Policy (MVP)

Objectives: minimize storage cost, avoid duplicate originals, ensure fast UX with thumbnails/previews, and keep derived artifacts for retrieval.

- Google Photos (Picker)
  - Mirror user-selected items into Supabase Storage for thumbnails/timeline playback; keep provider IDs to avoid duplicate ingests.
  - Fetch only selected items; full-library or unattended delta sync is out-of-scope/blocked by API.
- Future connectors (Apple Photos, Notion, others) follow the same rule set once implemented, but are deferred until after the MVP.

- Manual Uploads
  - Store originals in object storage with default 30‑day retention, keep thumbnails/previews + derived artifacts permanently.
  - User toggle “Keep Originals” to disable auto‑deletion.

- Derived Artifacts (always kept)
  - Thumbnails and keyframes (compressed), captions/OCR/transcripts (text), embeddings in vector DB.

- Lifecycle & Quotas
  - Nightly job enforces retention (delete originals older than retention window if “Keep Originals” is off).
  - Per‑user storage usage surfaced in dashboard with "Optimize storage" action (delete originals, keep artifacts).

- Access & Security
  - Serve media via short‑lived signed URLs; encrypt tokens/keys; enforce RLS on metadata.
  - Bucket structure: `originals/{user_id}/{item_id}`, `previews/{user_id}/{item_id}`, `thumbnails/{user_id}/{item_id}`.

**Data Schema (Target MVP; implement starting Week 3):**

The current repo schema is intentionally minimal. Week 3 ingestion work should add missing fields/tables via migrations and keep the model in sync with `docs/minecontext/lifelog_ingestion_rag_design.md`.

```sql
-- Connections table (OAuth + provider config)
CREATE TABLE data_connections (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  provider TEXT, -- 'google_photos', ...
  status TEXT, -- 'connected', 'syncing', 'error'
  config JSONB,
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now()
);

-- Source items table (raw occurrences)
CREATE TABLE source_items (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  connection_id UUID REFERENCES data_connections(id),
  provider TEXT,              -- 'manual_upload' | 'google_photos' | 'device'
  external_id TEXT,           -- provider media ID (for dedupe)
  item_type TEXT,             -- 'photo' | 'video' | 'audio' | 'document'
  storage_key TEXT,           -- object storage key/path (raw)
  content_type TEXT,
  original_filename TEXT,
  content_hash TEXT,          -- sha256 for exact dedupe
  phash TEXT,                 -- perceptual hash for near-dup (images)
  captured_at TIMESTAMP,      -- original timestamp if available
  event_time_utc TIMESTAMP,   -- normalized timeline timestamp (canonical)
  event_time_source TEXT,     -- exif|provider|client|server
  event_time_confidence FLOAT,
  location JSONB,
  metadata JSONB,
  processing_status TEXT,     -- 'pending' | 'processing' | 'completed' | 'failed'
  processing_error TEXT,
  processed_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now(),
  UNIQUE(connection_id, external_id)
);

-- Derived artifacts table (versioned, cacheable; idempotent pipeline outputs)
CREATE TABLE derived_artifacts (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  source_item_id UUID REFERENCES source_items(id),
  artifact_type TEXT,          -- exif|hash|phash|ocr|transcript|keyframes|vlm_observations...
  producer TEXT,               -- whisper|ffmpeg|vlm|embedder...
  producer_version TEXT,
  input_fingerprint TEXT,      -- enables caching/idempotency
  payload JSONB,
  storage_key TEXT,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(source_item_id, artifact_type, producer, producer_version, input_fingerprint)
);

-- Retrieval unit table (1..N per item; also stores merged episode contexts)
CREATE TABLE processed_contexts (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  context_type TEXT,           -- activity_context|food_context|...
  title TEXT,
  summary TEXT,
  keywords JSONB,
  entities JSONB,
  location JSONB,
  event_time_utc TIMESTAMP,
  start_time_utc TIMESTAMP,
  end_time_utc TIMESTAMP,
  is_episode BOOLEAN DEFAULT false,
  source_item_ids UUID[],
  merged_from_context_ids UUID[],
  vector_text TEXT,
  processor_versions JSONB,
  created_at TIMESTAMP DEFAULT now()
);
```

#### Batch Upload Implementation

**User uploads a folder of 1000 photos:**

```python
# Recommended MVP pattern: presigned upload + ingest enqueue.
# (The API provides presigned URLs; the browser uploads bytes directly.)

async def batch_upload(files: list[UploadFile], user: User):
    batch_id = str(uuid.uuid4())
    for file in files:
        # 1) Request a presigned upload URL from the API.
        signed = await api.post(
            "/storage/upload-url",
            json={"filename": file.filename, "content_type": file.content_type},
        )

        # 2) Upload bytes directly to object storage.
        await http.put(signed["url"], data=file.file, headers=signed["headers"])

        # 3) Create a SourceItem + enqueue processing.
        await api.post(
            "/upload/ingest",
            json={
                "storage_key": signed["key"],
                "item_type": "photo",
                "captured_at": None,  # server will normalize from EXIF/provider if available
                "content_type": file.content_type,
                "original_filename": file.filename,
            },
        )

    return {"batch_id": batch_id, "queued": len(files)}
```

---

### 4.2 Memory Layer Architecture

#### Problem: How to organize data for precise retrieval?

**Recommendation:** Multi-tier memory system with hybrid retrieval

**Architecture:**

```
┌─────────────────────────────────────────────────────────┐
│                     User Query                          │
│            "What did I do last Tuesday?"                │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│              Query Understanding Layer                   │
│  • Parse temporal refs ("last Tuesday" → 2025-10-29)   │
│  • Extract entities (people, places)                    │
│  • Identify query type (when/where/what/who/how)       │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│              Hybrid Retrieval Layer                      │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   Vector     │  │  Temporal    │  │   Entity     │ │
│  │   Search     │  │   Filter     │  │   Graph      │ │
│  │  (Qdrant)    │  │  (Postgres)  │  │  (Postgres)  │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                  │                  │         │
│         └──────────────────┼──────────────────┘         │
│                            │                            │
└────────────────────────────┼────────────────────────────┘
                             │
                             ▼
                    ┌────────────────┐
                    │ Context Fusion │
                    │  & Reranking   │
                    └────────┬───────┘
                             │
                             ▼
                    ┌────────────────┐
                    │  LLM Response  │
                    │   Generation   │
                    └────────────────┘
```

**Implementation Strategy:**

**1. Daily Event Clustering:**

Run nightly job to cluster items into events:

```python
@celery.task
def generate_daily_events(user_id: str, date: datetime.date):
    # Get all items for this user on this date
    items = db.query(source_items).filter(
        user_id=user_id,
        date(captured_at)=date
    ).all()
    
    # Cluster by time proximity (e.g., 2-hour windows)
    events = cluster_by_time(items, max_gap_minutes=120)
    
    for event_items in events:
        # Extract common location, people, themes
        location = most_common_location(event_items)
        people = extract_people(event_items)
        
        # Generate event summary using LLM
        summary = await generate_event_summary(event_items)
        
        # Create event record
        event_id = db.insert(events, {
            'user_id': user_id,
            'title': summary['title'],
            'start_time': min(i.captured_at for i in event_items),
            'end_time': max(i.captured_at for i in event_items),
            'location': location,
            'people': people,
            'summary': summary['text'],
            'source_item_ids': [i.id for i in event_items]
        })
        
        # Update graph
        update_memory_graph(event_id, people, location)
```

**2. Memory Graph Schema:**

```sql
CREATE TABLE memory_nodes (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  node_type TEXT, -- 'person', 'place', 'event', 'topic'
  name TEXT,
  attributes JSONB, -- {face_encoding, coordinates, description}
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  mention_count INTEGER DEFAULT 1,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE memory_edges (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  source_node_id UUID REFERENCES memory_nodes(id),
  target_node_id UUID REFERENCES memory_nodes(id),
  relation_type TEXT, -- 'attended', 'located_at', 'with', 'related_to'
  strength FLOAT DEFAULT 1.0, -- Co-occurrence frequency
  first_connected TIMESTAMP,
  last_connected TIMESTAMP,
  created_at TIMESTAMP DEFAULT now()
);

-- Example: "I had lunch with Alice at Cafe Blue"
-- Nodes: [Event: Lunch], [Person: Alice], [Place: Cafe Blue]
-- Edges: 
--   - (Event:Lunch) --[attended_by]--> (Person:Alice)
--   - (Event:Lunch) --[located_at]--> (Place:Cafe Blue)
--   - (Person:Alice) --[visited]--> (Place:Cafe Blue)
```

**3. Hybrid Retrieval Scoring:**

```python
def retrieve_relevant_context(query: str, user_id: str, top_k: int = 10):
    # 1. Parse query
    parsed = parse_query(query)  # Extract dates, entities, intent
    
    # 2. Vector search
    query_embedding = get_embedding(query)
    vector_hits = qdrant.search(
        collection=f"user_{user_id}",
        query_vector=query_embedding,
        limit=50,
        score_threshold=0.7
    )
    
    # 3. Temporal filter (if date mentioned)
    if parsed['date_range']:
        filtered_hits = [
            h for h in vector_hits 
            if h.payload['timestamp'] in parsed['date_range']
        ]
    else:
        filtered_hits = vector_hits
    
    # 4. Entity graph boost
    if parsed['entities']:
        # Find related events from graph
        graph_events = db.query(events).join(memory_edges).filter(
            memory_edges.target_node_id.in_(
                db.query(memory_nodes.id).filter(
                    memory_nodes.name.in_(parsed['entities'])
                )
            )
        ).all()
        
        # Boost scores for items in these events
        graph_item_ids = set()
        for event in graph_events:
            graph_item_ids.update(event.source_item_ids)
        
        for hit in filtered_hits:
            if hit.payload['item_id'] in graph_item_ids:
                hit.score *= 1.5  # Boost by 50%
    
    # 5. Re-rank by composite score
    def composite_score(hit):
        semantic = hit.score
        temporal = temporal_relevance(hit.payload['timestamp'], parsed['date_range'])
        entity_overlap = entity_similarity(hit.payload['entities'], parsed['entities'])
        
        return 0.5 * semantic + 0.3 * temporal + 0.2 * entity_overlap
    
    ranked_hits = sorted(filtered_hits, key=composite_score, reverse=True)
    
    return ranked_hits[:top_k]
```

#### Live Data Access (MCP) and Retrieval Router

**Principle:** embeddings + vector retrieval power long-term memory; MCP tools fetch live or missing data at query time. They are complementary, not interchangeable.

**When to use MCP (live tools):**
- Queries that are explicitly time-sensitive: "now", "today", "current", "latest"
- Action intents: "play", "navigate", "start workout", "set reminder"
- Data that is too large, locked behind API rate limits, or not yet ingested

**Router sketch:**

```
User Query
   -> Query Router (intent + time sensitivity)
       -> RAG Retrieval (vector + temporal + graph)
       -> Optional MCP Tools (live data)
   -> Context Fusion + Rerank
   -> LLM Response
```

```python
def route_query(query: str) -> dict:
    intent = classify_intent(query)
    needs_live = has_live_time(query) or intent in {
        "play_music",
        "navigate",
        "current_stats",
        "latest_activity"
    }
    tools = tools_for_intent(intent) if needs_live else []
    return {"use_rag": True, "mcp_tools": tools}
```

**Source-by-source strategy (future connectors):**

| Source | Long-term memory (embed + store) | Live MCP usage |
|--------|----------------------------------|----------------|
| Google Photos | Ingest user-selected Picker items; store metadata/captions/OCR/embeddings and Supabase copies for rendering | Fetch full-res on demand for already-selected items |
| Google Maps | Ingest timeline events + places into structured events and embeddings | "Where am I now", live navigation/traffic, current ETA |
| Spotify | Ingest listening history, playlists, artist metadata, embeddings | "What is playing now", control playback, latest queue |
| Oura Ring | Ingest daily aggregates (sleep, readiness, activity) into structured tables | "Current readiness", today's live metrics before nightly sync |

**Normalize live tool responses:**

```json
{
  "source": "spotify",
  "retrieved_at": "2025-11-02T10:15:00Z",
  "time_range": {"start": "2025-11-02", "end": "2025-11-02"},
  "records": [{"type": "track", "title": "...", "metadata": {...}}],
  "reliability": "live"
}
```

**Caching + memory backfill:**
- Cache live tool responses for 15-60 minutes to reduce API calls.
- Optionally ingest tool outputs into `source_items`/`processed_content` asynchronously so "live" becomes searchable memory.

**4. Modular Memory Component Design:**

```python
# Abstract memory retriever interface
class MemoryRetriever(ABC):
    @abstractmethod
    def retrieve(self, query: str, user_id: str, **kwargs) -> List[MemoryContext]:
        pass

# Implementations
class VectorMemoryRetriever(MemoryRetriever):
    def retrieve(self, query, user_id, **kwargs):
        # Pure vector search
        pass

class GraphMemoryRetriever(MemoryRetriever):
    def retrieve(self, query, user_id, **kwargs):
        # Graph-based traversal
        pass

class HybridMemoryRetriever(MemoryRetriever):
    def __init__(self, retrievers: List[MemoryRetriever]):
        self.retrievers = retrievers
    
    def retrieve(self, query, user_id, **kwargs):
        # Combine results from multiple retrievers
        all_results = []
        for retriever in self.retrievers:
            all_results.extend(retriever.retrieve(query, user_id, **kwargs))
        return self.fuse_and_rerank(all_results)

# Usage - easy to swap implementations
memory_system = HybridMemoryRetriever([
    VectorMemoryRetriever(qdrant_client),
    GraphMemoryRetriever(db),
    TemporalMemoryRetriever(db)
])

results = memory_system.retrieve("What did I do last Tuesday?", user_id)
```

**5. Daily Summary Generation**

```python
@celery.task
def generate_daily_summary(user_id: str, date: datetime.date):
    # Gather events, entities, and highlight media
    events = fetch_events(user_id, date)
    top_entities = fetch_top_entities(user_id, date)
    highlights = fetch_media_highlights(user_id, date)
    
    prompt = DAILY_SUMMARY_PROMPT.format(
        date=date.strftime("%B %d, %Y"),
        events=json.dumps(events, default=str),
        entities=json.dumps(top_entities),
        highlights=json.dumps(highlights)
    )
    
    summary = call_llm(prompt, response_format="markdown")
    
    db.insert(daily_summaries, {
        "user_id": user_id,
        "summary_date": date,
        "content_markdown": summary,
        "source_event_ids": [event["id"] for event in events],
        "created_at": datetime.utcnow()
    })
```

**Prompt Template**

```
You are the user's personal memory curator.
Summarize the following day in 3 sections:
1. Headline (1 sentence)
2. Key Moments (3-5 bullet points referencing event IDs)
3. People & Places (bullet list of notable entities)

Use friendly tone, reference timestamps, and avoid inventing details. Return Markdown.
```

#### Using mem0 for MVP

**Evaluation of mem0:**
- ✅ Good for: Session memory, conversation history, lightweight entity tracking
- ❌ Not ideal for: Large-scale multimodal data, complex temporal queries, custom retrieval logic

**Recommendation:** Use mem0 as a **conversation memory layer** on top of your core retrieval system

```python
from mem0 import Memory

# Initialize mem0 for conversation history
conversation_memory = Memory()

# In chat handler
def chat_handler(user_id: str, message: str, session_id: str):
    # 1. Add user message to conversation memory
    conversation_memory.add(
        messages=[{"role": "user", "content": message}],
        user_id=user_id,
        session_id=session_id
    )
    
    # 2. Retrieve relevant memories from YOUR system
    relevant_context = memory_system.retrieve(message, user_id)
    
    # 3. Get conversation context from mem0
    conversation_context = conversation_memory.get_all(
        user_id=user_id,
        session_id=session_id
    )
    
    # 4. Construct prompt
    prompt = f"""
    Conversation history:
    {conversation_context}
    
    Relevant memories:
    {format_context(relevant_context)}
    
    User: {message}
    """
    
    # 5. Get LLM response
    response = await call_llm(prompt)
    
    # 6. Add response to mem0
    conversation_memory.add(
        messages=[{"role": "assistant", "content": response}],
        user_id=user_id,
        session_id=session_id
    )
    
    return response
```

---

### 4.3 Model Layer

**LLM/VLM Selection:**

| Task | Model | Rationale |
|------|-------|-----------|
| Chat & Reasoning | GPT-4o / Claude 3.5 Sonnet | Best reasoning, long context |
| Image Understanding | GPT-4V / Gemini Vision Pro | Multimodal understanding |
| Embeddings | OpenAI text-embedding-3-small | Good quality/cost ratio |
| Summarization | Claude 3.5 Sonnet | Great at concise summaries |
| Entity Extraction | GPT-4o-mini | Cost-effective for structured extraction |

**Cost Optimization:**
- Cache embeddings (never recompute)
- Use cheaper models for batch processing (entity extraction, classification)
- Use expensive models only for user-facing chat

---

### 4.4 Application Layer (Web)

**Tech Stack:**
- **Framework:** React 19 + TypeScript SPA (Vite)
- **UI:** Tailwind (current repo uses CDN styling)
- **State/data:** TanStack Query (optional Zustand for UI state)
- **Auth:** Supabase Auth
- **Deployment:** Static hosting + CDN (or Vercel/Netlify) + API/worker on a container runtime

**Key Views (Tabs):**

1. **Dashboard** - Overview: storage usage, ingestion stats, and recent activity
2. **Ingest** - Manual upload + Google Photos connection + Picker ingest status
3. **Timeline** - Per-day media browsing + captions/summaries
4. **Chat** - RAG-powered memory Q&A with citations
5. **Settings** - Account + privacy + retention controls

**Chat Interface Design:**

```typescript
// Chat component structure
<ChatContainer>
  <ChatHeader>
    <UserAvatar />
    <SessionInfo />
  </ChatHeader>
  
  <MessageList>
    {messages.map(msg => (
      <Message key={msg.id}>
        <MessageContent>{msg.content}</MessageContent>
        {msg.sources && (
          <SourceCitations>
            {msg.sources.map(src => (
              <SourceCard 
                thumbnail={src.thumbnail}
                timestamp={src.timestamp}
                snippet={src.snippet}
              />
            ))}
          </SourceCitations>
        )}
      </Message>
    ))}
  </MessageList>
  
  <ChatInput 
    onSubmit={handleSendMessage}
    placeholder="Ask about your memories..."
  />
</ChatContainer>
```

---

### 4.5 Security & Privacy

**Data Security Measures:**

1. **Encryption:**
   - At rest: Supabase default encryption (AES-256)
   - In transit: TLS 1.3 for all connections
   - OAuth tokens: Encrypted in DB using application-level encryption

2. **Access Control:**
   - Row-Level Security (RLS) in Postgres for all user data tables
   - API authentication: JWT tokens from Supabase Auth
   - Rate limiting: 100 requests/min per user

3. **Privacy:**
   - User data isolation: All queries filtered by user_id
   - No cross-user data sharing in MVP
   - Data deletion: Cascade delete on user account deletion
   - Export: Provide full data export in JSON format

**Example RLS Policy:**

```sql
-- Only allow users to see their own data
ALTER TABLE source_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation_policy ON source_items
  FOR ALL
  USING (auth.uid() = user_id);
```

---

## 5. API Specification

### Core Endpoints

```
GET    /health                          # Service health
GET    /health/db                       # Database connectivity
GET    /health/celery                   # Celery connectivity
GET    /metrics                         # Prometheus metrics

POST   /storage/upload-url              # Get presigned upload URL
POST   /storage/download-url            # Get presigned download URL

POST   /upload/ingest                   # Create SourceItem + enqueue processing

GET    /timeline                        # Timeline feed (grouped by day)
GET    /dashboard/stats                 # Dashboard aggregates
GET    /search?q=...                    # Qdrant-backed search

GET    /integrations/google/photos/auth-url
GET    /integrations/google/photos/status
GET    /integrations/google/photos/callback
POST   /integrations/google/photos/picker-session
GET    /integrations/google/photos/picker-items?session_id=...
POST   /integrations/google/photos/sync # Enqueue ingest of picker selections

POST   /chat                            # (Week 7+) RAG chat endpoint
```

---

## 6. Data Flow Diagrams

### Google Photos Picker Ingest Flow

```
[User] → [Web UI: Connect Google Photos]
           ↓
[Frontend] → [API: GET /integrations/google/photos/auth-url]
           ↓
[API] → [Initiate OAuth Flow] → [Google OAuth]
           ↓
[API] ← [OAuth Token] ← [Google]
           ↓
[API] → [Store encrypted token in DB]
           ↓
[User] → [Web UI: Open Google Picker]
           ↓
[Frontend] → [API: POST /integrations/google/photos/picker-session]
           ↓
[Picker UI] → [User selects items] → [Picker returns session + media IDs]
           ↓
[API] → [Fetch selected media bytes]
           ↓
[API] → [Copy to Supabase Storage] → [Create source_items]
           ↓
[API] → [Queue: process_item task]
           ↓
[Celery Worker] → [process_item task]
                    ├→ [Normalize event_time_utc (EXIF/provider/server fallback)]
                    ├→ [Compute content_hash + pHash; dedup gate]
                    ├→ [Persist derived_artifacts (metadata/ocr/transcript/keyframes/vlm outputs)]
                    ├→ [Extract 1..N contexts per item (taxonomy)]
                    ├→ [Optional: semantic merge into episode contexts (time window)]
                    ├→ [Embed vector_text for contexts]
                    ├→ [Upsert contexts to Qdrant (context IDs + filterable payload)]
                    └→ [Update processing_status + processing_error]
           ↓
[Nightly Job] → [generate_daily_events]
                 ├→ [Cluster items by time]
                 ├→ [Extract entities]
                 ├→ [Generate event summaries]
                 └→ [Update memory graph]
```

### Chat Query Flow

```
[User] → [Web UI: "What did I do last Tuesday?"]
           ↓
[Frontend] → [API: POST /chat]
           ↓
[API] → [Query Understanding]
         ├→ [Parse temporal reference: "last Tuesday" → 2025-10-29]
         ├→ [Extract entities: none]
         └→ [Query type: "activity summary"]
           ↓
[API] → [Memory Retrieval]
         ├→ [Qdrant: Vector search with date filter]
         ├→ [Postgres: Get events on 2025-10-29]
         └→ [Fuse and rank results]
           ↓
[API] → [Context Construction]
         └→ Build prompt with:
            - Conversation history (mem0)
            - Retrieved memories (ranked)
            - Query
           ↓
[API] → [LLM: Generate response]
           ↓
[API] → [Format response with source citations]
           ↓
[API] → [Return to frontend]
           ↓
[Frontend] → [Display message + source cards]
```

---

## 7. MVP Deliverables Checklist

### Week 1-2: Foundation
- [ ] Set up monorepo structure
- [ ] Initialize Supabase project (Postgres + Auth + Storage)
- [ ] Set up Qdrant Cloud instance
- [ ] Implement FastAPI boilerplate with Supabase Auth integration
- [ ] Create initial database schema (users, data_connections, source_items, processed_content, daily_summaries)
- [ ] Set up Celery + Redis for task queue
- [ ] Create/maintain React + Vite SPA with Supabase Auth
- [ ] Implement login/logout flow

### Week 3-4: Data Ingestion
- [ ] Harden manual upload + Google Photos Picker ingest (keep current flows working)
- [ ] Add canonical timestamps (`event_time_utc`) + dedupe signals (`content_hash`, optional `pHash`)
- [ ] Add versioned artifacts storage (`derived_artifacts` or evolve `processed_content`)
- [ ] Add `processed_contexts` (1..N contexts per media item) and index by context ID in Qdrant
- [ ] Implement image pipeline v1: OCR + VLM batch analysis → contexts → embeddings → Qdrant upsert
- [ ] Implement video/audio v1 foundations: keyframes + transcript artifacts → initial contexts
- [ ] Defer additional connectors (Google Drive, Oura, Apple Photos, etc.) until post-MVP
- [ ] Implement Qdrant upsert for contexts + chunks with filterable payloads

### Week 5-6: Memory Layer
- [ ] Build daily event clustering job
- [ ] Implement entity extraction pipeline
- [ ] Create memory graph population logic
- [ ] Build hybrid retrieval function (vector + temporal + entity)
- [ ] Implement query understanding (parse dates, entities)
- [ ] Set up mem0 for conversation memory

### Week 7-8: Chat & UI
- [ ] Build chat API endpoint with RAG logic
- [ ] Integrate LLM API (GPT-4o/Claude)
- [ ] Implement source citation formatting
- [ ] Build chat UI with message history
- [ ] Create timeline view component
- [ ] Build dashboard with stats
- [ ] Implement connections management UI

### Week 9-10: Testing & Polish
- [ ] End-to-end testing of full pipeline
- [ ] Performance optimization (query latency, processing speed)
- [ ] Error handling and retry logic
- [ ] Add loading states and progress indicators
- [ ] Implement rate limiting and quotas
- [ ] Security audit (RLS policies, input validation)

### Week 11-12: Pilot Launch
- [ ] Deploy to production (Vercel + Railway/Render)
- [ ] Set up monitoring (Sentry, Prometheus)
- [ ] Create onboarding flow
- [ ] Invite pilot users (n=20)
- [ ] Collect feedback and iterate

---

## 8. Post-MVP Roadmap

### Phase 2: Enhanced Memory (Weeks 13-18)
- Advanced graph queries (shortest path, community detection)
- Face recognition and person clustering
- Location intelligence (frequent places, route patterns)
- Vlog generation (video montage + narration)
- Weekly/monthly summary reports

### Phase 3: Expansion (Weeks 19-26)
- Mobile apps (iOS, Android)
- Desktop capture app (macOS first) with screenshot sampling (e.g., every 30s while active)
- Cloud sync bridge: Google Drive connector (folder backfill + incremental sync)
- Wearables: Oura Ring connector (sleep/readiness/activity)
- Apple Photos ingestion via macOS agent/export bridge (can be integrated into the desktop app)
- ESP32 camera ingestion (SD-backed capture + upload-on-Wi-Fi using device tokens + presigned uploads)
- Additional integrations (Instagram, X/Twitter, TikTok)
- Shareable memory capsules
- Multi-user sharing and collaboration

### Phase 4: Scale (Weeks 27-40)
- Self-hosting option (Docker Compose deployment)
- Advanced privacy features (E2E encryption, local processing)
- Kubernetes deployment for enterprise
- Usage-based pricing tiers
- API for developers

---

## 9. Technical Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| 3rd-party API rate limits | Implement exponential backoff, queue-based sync with throttling |
| Large data volumes (storage costs) | Compress media, store thumbnails only, implement retention policies |
| Processing latency for large batches | Horizontal scaling of workers, batch processing optimization |
| Poor retrieval quality | Continuous evaluation with test queries, A/B test retrieval strategies |
| LLM costs | Cache responses, use cheaper models for batch tasks, implement usage quotas |
| Data privacy concerns | Clear privacy policy, offer local processing option, comply with GDPR |
| Apple iCloud auth fragility | Collect app-specific passwords with secure storage, proactive session refresh + manual export fallback |
| Video/audio compute expense | Batch inference on GPU workers, prioritize local/managed Whisper & BLIP variants, enforce per-user quotas |
| Daily summary hallucinations | Ground prompt with event IDs, include confidence thresholds, capture user feedback for corrections |

---

## 10. Success Criteria for MVP

**Must-Have:**
- ✅ User can connect Google Photos
- ✅ User can upload photos/videos manually (batch)
- ✅ User can upload 500+ photos in a single batch
- ✅ All uploaded data is processed within 1 hour
- ✅ User can ask temporal queries ("What did I do last week?") and get accurate answers
- ✅ User can ask entity queries ("When did I last see Alice?") and get relevant memories
- ✅ Chat response time < 3 seconds
- ✅ Timeline shows daily activity with thumbnails

**Nice-to-Have:**
- Export full data as ZIP
- Search by location
- Multi-language support
- Dark mode

---

*End of MVP PRD*
