# Lifelog AI - MVP Product Requirements Document

> **Status:** MVP-focused version emphasizing core data and memory architecture
> **Last Updated:** November 2, 2025

---

## 1. Executive Summary

Build an AI-powered personal memory assistant that ingests multimodal data from 3rd-party sources and user uploads, processes and organizes it into a queryable knowledge base, and provides conversational access through a web-based chat interface.

**MVP Scope (8-12 weeks):** Web app only, focusing on data ingestion pipeline, memory organization, and RAG-powered chat.

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

---

## 3. MVP Feature Scope

### IN SCOPE ✅

**3.1 Data Layer**
- Manual upload interface for photos, videos, audio files (batch upload support)
- 3rd-party integrations:
  - Google Photos (OAuth + full historical sync)
  - Apple Photos / iCloud (via API or export import)
  - Notion (OAuth + page/database sync)
- One-time full historical data ingestion, then incremental daily syncs
- Metadata extraction: timestamp, location, file type, source
- Deduplication logic (hash-based)

**3.2 Processing Pipeline**
- Image: OCR (text extraction), captioning (BLIP/GPT-4V), CLIP embeddings
- Video: Keyframe extraction, scene detection, captioning per frame, audio transcription
- Audio: Speech-to-text (Whisper), speaker diarization
- Documents (Notion): Text extraction, embedding generation
- Entity extraction: People, places, objects, events (using LLM)
- Temporal clustering: Group items into "events" by time proximity

**3.3 Memory Layer**
- **Vector Store (Qdrant/Pinecone):** Semantic embeddings for all content
- **Structured DB (Postgres):** Timeline data, metadata, entity relationships
- **Memory Graph (Postgres graph tables):** Entity nodes (person/place/event) and relationships
- **Daily Summaries:** Automated job that synthesizes activities across all data sources per day
- **Hybrid Retrieval:** Combine vector similarity + temporal proximity + entity overlap

**3.4 Model Layer**
- LLM API integration: GPT-4o, Claude 3.5/4, or Gemini for chat and summarization
- Vision API: GPT-4V or Gemini Vision for image understanding
- Local embedding model: all-MiniLM-L6-v2 or OpenAI embeddings API

**3.5 Application Layer (Web Only)**
- User authentication (Supabase Auth: email/password + Google OAuth)
- **Data Connections Page:** UI to connect and authorize 3rd-party apps, view sync status
- **Upload Interface:** Drag-and-drop or folder selection for batch uploads, progress indicators
- **Chat Interface:** Conversational UI with memory-powered responses, source citations
- **Timeline View:** Calendar/timeline visualization of memories with thumbnails
- **Dashboard:** Weekly/monthly summaries, activity heatmap, storage usage

**3.6 Infrastructure**
- **Auth/DB/Storage:** Supabase (Postgres + Auth + Object Storage)
- **Vector DB:** Qdrant Cloud (start) → self-hosted Qdrant (scale)
- **API Backend:** FastAPI (Python)
- **Task Queue:** Celery + Redis for async processing
- **Web Frontend:** Next.js (App Router) deployed on Vercel
- **Cloud:** Start with managed services (Supabase, Vercel, Railway/Render for API)

### OUT OF SCOPE (Post-MVP) ❌
- Mobile apps (iOS/Android)
- Desktop capture agent
- Automated screenshot/video capture
- Vlog generation
- Social media integrations (Twitter, Instagram, TikTok)
- Shareable mini-chatbots
- Advanced graph visualizations
- Multi-user collaboration

---

## 4. Architecture Deep Dive

### 4.1 Data Layer Architecture

#### Problem: How to handle 3rd-party data ingestion?

**Recommendation:** Full historical fetch + incremental sync

**Rationale:**
1. **Full control:** Store all data in your DB for fast queries, no API rate limits during user queries
2. **Avoid reprocessing:** Process once, query many times
3. **Consistency:** Single source of truth for all memory operations
4. **Cost-effective:** One-time processing cost vs. repeated API calls

**Implementation Pattern:**

```
Initial Sync (One-time):
1. User connects Google Photos via OAuth
2. Backend job fetches ALL photos metadata (photo_id, timestamp, location, albums)
3. For each photo:
   - Download to temp storage
   - Extract metadata
   - Process (caption, OCR, embed)
   - Store processed data + embeddings
   - Store reference to original in 3rd party (for display)
   - Delete temp file
4. Mark sync complete, store last_sync_timestamp

Incremental Sync (Daily):
1. Cron job triggers daily sync for each connected source
2. Query 3rd party API for items modified_since last_sync_timestamp
3. Process only new/updated items
4. Update last_sync_timestamp
```

**Storage Strategy:**
- **Metadata only:** Store photo URLs, IDs, timestamps in your DB
- **Processed artifacts:** Store captions, embeddings, OCR text in your DB
- **Original files:** Keep in 3rd party (Google Photos) for display, or selectively cache thumbnails

**Data Schema:**

```sql
-- Connections table
CREATE TABLE data_connections (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  provider TEXT, -- 'google_photos', 'notion', 'apple_photos'
  status TEXT, -- 'connected', 'syncing', 'error'
  oauth_token_encrypted TEXT,
  last_sync_at TIMESTAMP,
  total_items INTEGER,
  created_at TIMESTAMP DEFAULT now()
);

-- Source items table (generic for all sources)
CREATE TABLE source_items (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  connection_id UUID REFERENCES data_connections(id),
  provider TEXT,
  external_id TEXT, -- ID in 3rd party system
  item_type TEXT, -- 'photo', 'video', 'note', 'audio'
  original_url TEXT,
  thumbnail_url TEXT,
  captured_at TIMESTAMP,
  location JSONB, -- {lat, lng, place_name}
  metadata JSONB, -- provider-specific data
  processing_status TEXT, -- 'pending', 'processing', 'completed', 'failed'
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(connection_id, external_id)
);

-- Processed content table
CREATE TABLE processed_content (
  id UUID PRIMARY KEY,
  source_item_id UUID REFERENCES source_items(id),
  user_id UUID REFERENCES users(id),
  content_type TEXT, -- 'caption', 'ocr', 'transcript', 'summary'
  content_text TEXT,
  language TEXT,
  confidence FLOAT,
  created_at TIMESTAMP DEFAULT now()
);

-- Embeddings table (or use Qdrant exclusively)
CREATE TABLE embeddings (
  id UUID PRIMARY KEY,
  source_item_id UUID REFERENCES source_items(id),
  processed_content_id UUID REFERENCES processed_content(id),
  user_id UUID REFERENCES users(id),
  embedding vector(384), -- or 1536 for OpenAI
  created_at TIMESTAMP DEFAULT now()
);
```

#### Batch Upload Implementation

**User uploads a folder of 1000 photos:**

```python
# FastAPI endpoint
@app.post("/api/v1/upload/batch")
async def batch_upload(
    files: List[UploadFile],
    user: User = Depends(get_current_user)
):
    upload_batch_id = str(uuid.uuid4())
    
    for file in files:
        # Generate unique ID
        item_id = str(uuid.uuid4())
        
        # Extract EXIF metadata for timestamp, location
        metadata = extract_exif(file)
        
        # Upload to object storage
        storage_path = f"{user.id}/uploads/{upload_batch_id}/{item_id}"
        await supabase.storage.upload(storage_path, file)
        
        # Create source_item record
        db.insert(source_items, {
            'id': item_id,
            'user_id': user.id,
            'item_type': 'photo',
            'original_url': storage_path,
            'captured_at': metadata.get('timestamp'),
            'location': metadata.get('location'),
            'metadata': metadata,
            'processing_status': 'pending'
        })
        
        # Queue for processing
        celery.send_task('process_item', args=[item_id])
    
    return {"batch_id": upload_batch_id, "queued": len(files)}
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
- **Framework:** Next.js 14 (App Router)
- **UI:** TailwindCSS + shadcn/ui
- **State:** TanStack Query + Zustand
- **Auth:** Supabase Auth
- **Deployment:** Vercel

**Key Pages:**

1. **/auth** - Login/Signup with email + Google OAuth
2. **/dashboard** - Overview: storage usage, connected sources, activity timeline
3. **/connections** - Manage 3rd-party connections, trigger syncs
4. **/upload** - Batch upload interface with progress tracking
5. **/chat** - Conversational memory interface with source citations
6. **/timeline** - Calendar view of memories
7. **/settings** - Account settings, privacy controls, export data

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
POST   /api/v1/auth/signup              # Create account
POST   /api/v1/auth/login               # Login
POST   /api/v1/auth/logout              # Logout

GET    /api/v1/connections              # List connected sources
POST   /api/v1/connections              # Add new connection (OAuth)
DELETE /api/v1/connections/:id          # Remove connection
POST   /api/v1/connections/:id/sync     # Trigger manual sync

POST   /api/v1/upload/batch             # Batch upload files
GET    /api/v1/upload/batch/:id/status  # Check upload progress

GET    /api/v1/items                    # List all user items (paginated)
GET    /api/v1/items/:id                # Get item details
DELETE /api/v1/items/:id                # Delete item

POST   /api/v1/chat                     # Send chat message
GET    /api/v1/chat/sessions            # List chat sessions
GET    /api/v1/chat/sessions/:id        # Get session history

GET    /api/v1/timeline                 # Get timeline data
GET    /api/v1/dashboard/stats          # Get dashboard statistics
GET    /api/v1/events                   # List events (daily summaries)
GET    /api/v1/events/:id               # Get event details
```

---

## 6. Data Flow Diagrams

### Initial Data Sync Flow

```
[User] → [Web UI: Connect Google Photos]
           ↓
[Frontend] → [API: POST /connections]
           ↓
[API] → [Initiate OAuth Flow] → [Google OAuth]
           ↓
[API] ← [OAuth Token] ← [Google]
           ↓
[API] → [Store encrypted token in DB]
           ↓
[API] → [Queue: sync_google_photos task]
           ↓
[Celery Worker] → [Google Photos API: List all photos]
           ↓
[Celery Worker] → [For each photo:]
                    ├→ [Create source_items record]
                    ├→ [Queue: process_item task]
                    └→ [Update sync progress]
           ↓
[Celery Worker] → [process_item task]
                    ├→ [Download photo]
                    ├→ [Extract EXIF metadata]
                    ├→ [Run captioning (GPT-4V)]
                    ├→ [Run OCR if text detected]
                    ├→ [Generate embedding]
                    ├→ [Store in processed_content]
                    ├→ [Upsert to Qdrant]
                    └→ [Update processing_status]
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
- [ ] Create database schema (users, source_items, processed_content, embeddings, events, memory_nodes, memory_edges)
- [ ] Set up Celery + Redis for task queue
- [ ] Create Next.js app with Supabase Auth
- [ ] Implement login/logout flow

### Week 3-4: Data Ingestion
- [ ] Implement batch upload endpoint + UI
- [ ] Build Google Photos OAuth integration
- [ ] Create full sync job for Google Photos
- [ ] Implement incremental sync logic
- [ ] Build Notion integration (OAuth + page sync)
- [ ] Create processing pipeline (Celery tasks):
  - [ ] Image captioning
  - [ ] OCR
  - [ ] Video keyframe extraction
  - [ ] Audio transcription
  - [ ] Embedding generation
- [ ] Implement Qdrant upsert logic

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
- Desktop capture agent
- Additional integrations (Apple Photos, Twitter, Instagram)
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

---

## 10. Success Criteria for MVP

**Must-Have:**
- ✅ User can connect Google Photos and Notion
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