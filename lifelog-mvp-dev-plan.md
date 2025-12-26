# Lifelog AI - MVP Development Plan (12 Weeks)

> **Goal:** Ship a working web app where users can connect data sources, upload files, and chat about their memories
> **Timeline:** 12 weeks to pilot launch
> **Team Size:** 1-2 developers

---

## Week 1-2: Foundation & Infrastructure Setup

### Objectives
- Set up development environment
- Establish core infrastructure
- Implement authentication
- Create basic database schema

### Tasks

**Repository & Project Structure**
- [ ] Initialize Git repository with monorepo structure:
  ```
  lifelog-ai/
  ├── apps/
  │   └── web/          # React + Vite SPA frontend
  ├── services/
  │   ├── api/          # FastAPI backend
  │   └── workers/      # Celery workers
  ├── packages/
  │   ├── db/           # Shared database schemas
  │   └── types/        # Shared TypeScript types
  ├── docker-compose.yml
  └── README.md
  ```
- [ ] Create `.env.example` files for all services

**Cloud Infrastructure**
- [ ] Create Supabase project:
  - [ ] Enable Email auth provider (if using Supabase Auth for login)
  - [ ] Enable Google OAuth provider later with Google Photos integration
- [ ] Set up GitHub repository and enable GitHub Actions

**Database Setup**
- [ ] Create database schema in Supabase:
  ```sql
  -- migrations/001_initial_schema.sql
  CREATE TABLE users (...);
  CREATE TABLE data_connections (...);
  CREATE TABLE source_items (...);
  CREATE TABLE processed_content (...);
  CREATE TABLE embeddings (...);
  CREATE TABLE events (...);
  CREATE TABLE memory_nodes (...);
  CREATE TABLE memory_edges (...);
  ```
- [ ] Create database migration scripts and apply them to local Postgres and Supabase (keep schemas in sync)
- [ ] Skip `pgvector` for now (using Qdrant); enable later only if hybrid Postgres vector search is needed
- [ ] Defer Row-Level Security (RLS) until the frontend talks directly to Supabase
- [ ] Defer encryption key storage (`vault.secrets`/KMS) until OAuth or other app-specific tokens are stored

**Backend API (FastAPI)**
- [ ] Initialize FastAPI project with poetry/pipenv:
  ```python
  # services/api/pyproject.toml
  dependencies = [
      "fastapi",
      "uvicorn",
      "supabase",
      "celery",
      "redis",
      "python-multipart",
      "pillow",
      "opencv-python",
      "qdrant-client"
  ]
  ```
- [ ] Implement health check endpoint (`/health`) — done
- [ ] Add CORS middleware for frontend — done
- [ ] Set up Supabase storage integration with environment variables (for presigned uploads) — done
- [ ] Defer JWT authentication middleware until auth is wired
- [ ] Defer structlog + OpenTelemetry tracing until observability setup
- [ ] Defer token encryption helper (AES-256-GCM with key rotation schedule) until OAuth tokens are stored
- [ ] Implement core HTTP endpoints — done:
  - `GET /health`, `GET /health/db`, `GET /health/celery`
  - `GET /metrics` (Prometheus)
  - `POST /storage/upload-url`, `POST /storage/download-url` (presigned URLs)
  - `POST /upload/ingest` (enqueue processing)
  - `GET /timeline` (timeline feed)
  - `GET /dashboard/stats` (dashboard aggregates)
  - `GET /search` (Qdrant-backed search)

**Storage Abstraction & Presigned URLs**
- [ ] Implement storage provider interface (memory + Supabase) — done
- [ ] Configure Supabase storage env vars for presigned uploads (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `STORAGE_PROVIDER=supabase`) — done
- [ ] Add API endpoints for presigned flows (`POST /storage/upload-url`, `POST /storage/download-url`) — done
- [ ] Use presigned URL TTL from config (`presigned_url_ttl_seconds`) — done
- [ ] Use storage fetch helper in the processing pipeline — done
- [ ] Defer `thumbnails` bucket setup until thumbnail generation lands

**Task Queue (Celery)**
- [ ] Set up Redis locally (Docker) — done
- [ ] Initialize Celery app with Redis broker + result backend — done
- [ ] Implement background tasks (`process_item`, `health.ping`) — done
- [ ] Wire `/upload/ingest` to enqueue processing tasks — done
- [ ] Add Celery beat schedule skeleton (health + lifecycle cleanup) — done
- [ ] Defer Flower monitoring until debugging/ops needs it
- [ ] Defer production queue tuning (retries, rate limits, per-queue routing) until later

**Frontend (React + Vite SPA)**
- [ ] Maintain the existing `apps/web` stack of React 19 + TypeScript bundled with Vite 6 (`npm create vite@latest lifelog-ai -- --template react-ts` as reference)
- [ ] Keep Tailwind via CDN config in `index.html` (custom `primary` palette + Inter font) so component classes in `App.tsx`, `Layout.tsx`, etc. render correctly without a Tailwind build step
- [ ] Install/maintain runtime dependencies already present in `package.json`: `react`, `react-dom`, `lucide-react` for icons, `recharts` for dashboard charts, and `@google/genai` for assistant calls
- [ ] Ensure `App.tsx` view switcher (dashboard/chat/timeline/upload/settings) remains the single source of truth after login so every tab can reuse shared layout + auth state
- [ ] Flesh out the Ingest tab so manual uploads call the FastAPI `/upload/batch` endpoint and the Google Photos connector UI triggers OAuth, shows sync status, and surfaces retry/manage actions
- [ ] Build chat + ingestion workflows against the Gemini API via `@google/genai` services, reusing hooks/utilities under `apps/web/services`
- [ ] Drive the Timeline view from the `/timeline` API (daily activity heatmap + detail drawer per day showing photos, videos, summaries) and hydrate the Dashboard components with stats returned by `/stats`
- [ ] Add lightweight routing or state persistence as needed (URL params or Zustand) instead of server-side routing, and gate authenticated data fetches through supabase/api clients once backend endpoints exist

**Docker Compose for Local Development**
- [ ] Create `docker-compose.yml`:
  ```yaml
  version: '3.8'
  services:
    postgres:
      image: postgres:15
      environment:
        POSTGRES_PASSWORD: postgres
      volumes:
        - postgres_data:/var/lib/postgresql/data
    
    redis:
      image: redis:7-alpine
    
    qdrant:
      image: qdrant/qdrant:latest
      ports:
        - "6333:6333"
    
    api:
      build: ./services/api
      depends_on:
        - postgres
        - redis
      environment:
        - DATABASE_URL=...
        - REDIS_URL=redis://redis:6379
    
    worker:
      build: ./services/api
      command: celery -A tasks worker --loglevel=info
      depends_on:
        - redis
        - postgres
  ```
- [ ] Test full stack with `docker-compose up`

**CI/CD Pipeline**
- [ ] Create GitHub Actions workflow:
  ```yaml
  # .github/workflows/test.yml
  name: Test
  on: [push, pull_request]
  jobs:
    test-api:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Run API tests
          run: |
            cd services/api
            pytest
    test-frontend:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Run frontend tests
          run: |
            cd apps/web
            pnpm test
  ```

### Deliverables
- ✅ Working local development environment
- ✅ User can sign up and log in
- ✅ Database schema created with RLS
- ✅ API health check endpoint working
- ✅ Celery task queue functional

---

## Week 3-4: Data Ingestion Pipeline

### Objectives
- Implement file upload with progress tracking
- Build Google Photos integration
- Create processing pipeline for images
- Store processed data in vector database

### Tasks

**Manual Upload (Backend)**
- [ ] Create Supabase storage bucket `user-uploads`; add `thumbnails` when thumbnail generation lands
- [ ] Configure Supabase Storage CORS for local development if the browser uses presigned uploads
- [ ] Create upload endpoint (presigned preferred; keep batch POST for small files):
  ```python
  @app.post("/api/v1/upload/batch")
  async def batch_upload(
      files: List[UploadFile],
      user: User = Depends(get_current_user)
  ):
      batch_id = str(uuid.uuid4())
      for file in files:
          # Extract EXIF metadata
          # Upload to Supabase Storage
          # Create source_item record
          # Queue processing task
      return {"batch_id": batch_id}
  ```
- [ ] Implement EXIF metadata extraction (timestamp, location, camera info)
- [ ] Create Supabase Storage upload helper with presigned URLs
- [ ] Add file type validation (images, videos, audio only)
- [ ] Implement deduplication using SHA256 + perceptual hash + EXIF heuristics

**Manual Upload (Frontend)**
- [ ] Create `/upload` page with drag-and-drop UI
- [ ] Implement file selection with folder support
- [ ] Add upload progress tracking:
  ```typescript
  const { mutate: uploadFiles } = useMutation({
    mutationFn: async (files: File[]) => {
      const formData = new FormData();
      files.forEach(f => formData.append('files', f));
      return api.post('/upload/batch', formData, {
        onUploadProgress: (e) => {
          setProgress(Math.round((e.loaded * 100) / e.total));
        }
      });
    }
  });
  ```
- [ ] Show thumbnail previews before upload
- [ ] Display upload status (pending/processing/completed/failed)

**Google Photos Integration**
- [ ] Set up Google Cloud Project & enable Photos API
- [ ] Implement OAuth flow:
  ```python
  @app.get("/api/v1/connections/google-photos/oauth")
  async def google_photos_oauth(user: User = Depends(get_current_user)):
      # Generate OAuth URL
      return {"auth_url": oauth_url}
  
  @app.get("/api/v1/connections/google-photos/callback")
  async def google_photos_callback(code: str, user: User):
      # Exchange code for token
      # Encrypt and store token
      # Queue initial sync task
      return {"status": "connected"}
  ```
- [ ] Create OAuth frontend flow with popup window
- [ ] Build sync task:
  ```python
  @celery.task
  def sync_google_photos(connection_id: str):
      # Get OAuth token
      # Paginate through all photos
      # For each photo:
      #   - Create source_item record
      #   - Queue process_item task
      # Update last_sync_at
  ```
- [ ] Implement pagination handling (Google Photos API returns 50 items/page)
- [ ] Add sync status tracking in UI
- [ ] Wire Celery Beat to enqueue `sync_google_photos` nightly (or more frequently) per connected account so new media land in the ingest queue without manual action
- [ ] Persist Google media IDs + album references so timeline/day-summary queries can deep-link to original Google Photos items when the user opens details

**Image Processing Pipeline**
- [ ] Create `process_item` Celery task:
```python
@celery.task
def process_item(item_id: str):
    item = db.get(source_items, item_id)
    file_path = storage.download_to_tmp(item.original_url)
    
    caption = ""
    ocr_text = ""
    transcript = ""
    
    if item.item_type == "photo":
        ocr_text = run_ocr(file_path)
        caption = generate_caption(load_bytes(file_path))
    
    elif item.item_type == "video":
        keyframes = extract_keyframes(file_path, interval_sec=3)
        scenes = detect_scenes(keyframes)
        caption = summarize_scenes(scenes)
        transcript = transcribe_audio(file_path)
        generate_video_thumbnails(keyframes)
    
    elif item.item_type == "audio":
        transcript, speakers = transcribe_and_diarize(file_path)
    
    content_records = []
    if caption:
        content_records.append({"content_type": "caption", "content_text": caption})
    if ocr_text:
        content_records.append({"content_type": "ocr", "content_text": ocr_text})
    if transcript:
        content_records.append({"content_type": "transcript", "content_text": transcript})
    
    for record in content_records:
        db.insert(processed_content, {
            "source_item_id": item_id,
            "user_id": item.user_id,
            **record
        })
    
    embedding_payload = " ".join([r["content_text"] for r in content_records])
    embedding = get_embedding(embedding_payload)
    
    qdrant.upsert(
        collection_name=f"user_{item.user_id}",
        points=[{
            "id": item_id,
            "vector": embedding,
            "payload": {
                "item_id": item_id,
                "user_id": item.user_id,
                "caption": caption,
                "ocr_text": ocr_text,
                "transcript": transcript,
                "timestamp": item.captured_at,
                "location": item.location,
                "item_type": item.item_type
            }
        }]
    )
    
    db.update(source_items, item_id, {"processing_status": "completed"})
    emit_processing_metrics(item_id, item.item_type, duration_ms)
```

- [ ] Implement ffmpeg-based `extract_keyframes` + PySceneDetect integration
- [ ] Use Whisper large-v3 or faster alternative on GPU-backed worker (RunPod or Modal)
- [ ] Cache embeddings + captions to avoid recomputation on reprocess
- [ ] Enforce processing SLA dashboards (queue depth, items/minute)

**Lifecycle & Retention Jobs**
- [ ] Create `enforce_storage_lifecycle` Celery task:
  ```python
  @celery.task
  def enforce_storage_lifecycle():
      for user in get_active_users():
          settings = get_user_storage_settings(user.id)  # keep_originals, retention_days
          if settings.keep_originals:
              continue
          cutoff = datetime.utcnow() - timedelta(days=settings.retention_days or 30)
          originals = list_originals_older_than(user.id, cutoff)
          for key in originals:
              storage.delete(key)
              log_storage_deletion(user.id, key)
  ```
- [ ] Schedule nightly run via Celery beat and add metrics (deleted count, reclaimed bytes)
- [ ] Add “Optimize storage” UI action to trigger a one-off lifecycle run per user

**AI Model Integration**
- [ ] Document model decision matrix (cost per 1k tokens/min, throughput, latency) for GPT-4V vs BLIP, Whisper vs Faster-Whisper
- [ ] Implement caching layer (Redis) for identical caption/transcript requests and chunk batching
- [ ] Set monthly budget guardrails + alerts for model spend; failover to cheaper models when threshold hit
- [ ] Integrate OCR service (Tesseract or cloud OCR):
  ```python
  import pytesseract
  from PIL import Image
  
  def run_ocr(image_data: bytes) -> str:
      image = Image.open(io.BytesIO(image_data))
      text = pytesseract.image_to_string(image)
      return text.strip()
  ```
- [ ] Integrate image captioning:
  - Option A: Use GPT-4V API
  - Option B: Use Hugging Face BLIP model
  ```python
  async def generate_caption(image_data: bytes) -> str:
      # Using GPT-4V
      response = await openai.ChatCompletion.create(
          model="gpt-4-vision-preview",
          messages=[{
              "role": "user",
              "content": [
                  {"type": "text", "text": "Describe this image in detail"},
                  {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
              ]
          }]
      )
      return response.choices[0].message.content
  ```
- [ ] Integrate embedding model:
  ```python
  from openai import OpenAI
  
  client = OpenAI()
  
  def get_embedding(text: str) -> List[float]:
      response = client.embeddings.create(
          model="text-embedding-3-small",
          input=text
      )
      return response.data[0].embedding
  ```

**Qdrant Integration**
- [ ] Create user collection on first upload:
  ```python
  def ensure_user_collection(user_id: str):
      collection_name = f"user_{user_id}"
      if not qdrant.collection_exists(collection_name):
          qdrant.create_collection(
              collection_name=collection_name,
              vectors_config={
                  "size": 1536,  # OpenAI embedding size
                  "distance": "Cosine"
              }
          )
  ```
- [ ] Implement batch upsert for efficiency
- [ ] Add error handling and retry logic

**Connections Management UI**
- [ ] Create `/connections` page
- [ ] Show list of connected sources with status
- [ ] Add "Connect" buttons for each provider
- [ ] Display sync statistics (total items, last sync, status)
- [ ] Add manual "Sync Now" button
- [ ] Show progress during sync

**Storage Monitoring**
- [ ] Track per-user storage usage (bytes/originals/previews) and show in dashboard
- [ ] Add alerts on high growth (>5 GB/day) and quota breaches

### Deliverables
- ✅ User can upload 100+ mixed media items (photos/videos/audio) at once
- ✅ Users can connect Google Photos and Apple Photos accounts with automated backfill + delta sync
- ✅ Video keyframes/transcripts and audio diarization are generated and searchable
- ✅ Processed data (captions, transcripts, embeddings) is stored in Qdrant + Supabase
- ✅ Sync + processing status, throughput, and cost telemetry are visible in monitoring dashboard

---

## Week 5-6: Memory Layer & Event Clustering

### Objectives
- Implement entity extraction from content
- Build memory graph
- Create daily event clustering
- Develop hybrid retrieval system

### Tasks

**Entity Extraction**
- [ ] Create entity extraction task:
  ```python
  @celery.task
  def extract_entities(item_id: str):
      item = db.get(source_items, item_id)
      content = db.query(processed_content).filter(
          source_item_id=item_id
      ).all()
      
      # Combine all content
      full_text = ' '.join([c.content_text for c in content])
      
      # Use LLM for entity extraction
      entities = await extract_entities_llm(full_text, item.location)
      
      # Store in memory graph
      for entity in entities:
          node_id = upsert_memory_node(
              user_id=item.user_id,
              node_type=entity['type'],
              name=entity['name'],
              attributes=entity['attributes']
          )
          
          # Create edge between item and entity
          create_memory_edge(
              source_node_id=item_id,
              target_node_id=node_id,
              relation_type=entity['relation']
          )
  ```
- [ ] Implement LLM-based entity extraction:
  ```python
  async def extract_entities_llm(text: str, location: dict) -> List[dict]:
      prompt = f"""
      Extract entities from the following content:
      {text}
      
      Location: {location}
      
      Return JSON with entities:
      {{
        "people": ["Alice", "Bob"],
        "places": ["Central Park"],
        "objects": ["camera", "bicycle"],
        "activities": ["hiking", "photography"]
      }}
      """
      
      response = await openai.ChatCompletion.create(
          model="gpt-4o-mini",
          messages=[{"role": "user", "content": prompt}],
          response_format={"type": "json_object"}
      )
      
      return json.loads(response.choices[0].message.content)
  ```
- [ ] Add entity extraction to processing pipeline
- [ ] Batch LLM requests with caching to cap cost (<$0.15 per 100 items)
- [ ] Create evaluation set (50 items) to score precision/recall of extracted entities monthly
- [ ] Emit entity extraction metrics (latency, token usage, error rate)

**Memory Graph Implementation**
- [ ] Create helper functions for graph operations:
  ```python
  def upsert_memory_node(user_id, node_type, name, attributes):
      # Check if node exists
      existing = db.query(memory_nodes).filter(
          user_id=user_id,
          node_type=node_type,
          name=name
      ).first()
      
      if existing:
          # Update attributes and timestamps
          db.update(memory_nodes, existing.id, {
              'attributes': {**existing.attributes, **attributes},
              'last_seen': datetime.now(),
              'mention_count': existing.mention_count + 1
          })
          return existing.id
      else:
          # Create new node
          return db.insert(memory_nodes, {
              'user_id': user_id,
              'node_type': node_type,
              'name': name,
              'attributes': attributes,
              'first_seen': datetime.now(),
              'last_seen': datetime.now(),
              'mention_count': 1
          })
  
  def create_memory_edge(source_node_id, target_node_id, relation_type, strength=1.0):
      # Check if edge exists
      existing = db.query(memory_edges).filter(
          source_node_id=source_node_id,
          target_node_id=target_node_id,
          relation_type=relation_type
      ).first()
      
      if existing:
          db.update(memory_edges, existing.id, {
              'strength': existing.strength + strength,
              'last_connected': datetime.now()
          })
      else:
          db.insert(memory_edges, {...})
  ```
- [ ] Create graph query helpers:
  ```python
  def get_related_entities(user_id, entity_name, relation_type=None, depth=1):
      # Find all entities connected to given entity
      query = """
      WITH RECURSIVE entity_traverse AS (
        SELECT id, name, node_type, 0 as depth
        FROM memory_nodes
        WHERE user_id = %s AND name = %s
        
        UNION ALL
        
        SELECT n.id, n.name, n.node_type, et.depth + 1
        FROM memory_nodes n
        JOIN memory_edges e ON e.target_node_id = n.id
        JOIN entity_traverse et ON et.id = e.source_node_id
        WHERE et.depth < %s
      )
      SELECT * FROM entity_traverse;
      """
      return db.execute(query, (user_id, entity_name, depth))
  ```

**Daily Event Clustering**
- [ ] Create nightly event clustering job:
  ```python
  @celery.task
  def generate_daily_events(user_id: str, date: datetime.date):
      # Get all items for this date
      items = db.query(source_items).filter(
          user_id=user_id,
          func.date(captured_at) == date,
          processing_status='completed'
      ).order_by(captured_at).all()
      
      if not items:
          return
      
      # Cluster by time proximity (2-hour window)
      events = cluster_by_time(items, gap_minutes=120)
      
      for event_items in events:
          # Extract common attributes
          location = most_common_location(event_items)
          
          # Get entities from graph
          entity_ids = set()
          for item in event_items:
              entities = db.query(memory_edges).filter(
                  source_node_id=item.id
              ).all()
              entity_ids.update([e.target_node_id for e in entities])
          
          entities = db.query(memory_nodes).filter(
              id.in_(entity_ids)
          ).all()
          
          # Generate event summary with LLM
          summary = await generate_event_summary(event_items, entities)
          
          # Create event record
          event_id = db.insert(events, {
              'user_id': user_id,
              'title': summary['title'],
              'start_time': min(i.captured_at for i in event_items),
              'end_time': max(i.captured_at for i in event_items),
              'location': location,
              'summary': summary['text'],
              'source_item_ids': [i.id for i in event_items]
          })
          
          # Create event node in graph
          event_node_id = upsert_memory_node(
              user_id=user_id,
              node_type='event',
              name=summary['title'],
              attributes={'event_id': event_id}
          )
          
          # Connect event to entities
          for entity in entities:
              create_memory_edge(
                  source_node_id=event_node_id,
                  target_node_id=entity.id,
                  relation_type='involves'
              )
  ```
- [ ] Implement time-based clustering:
  ```python
  def cluster_by_time(items, gap_minutes=120):
      clusters = []
      current_cluster = [items[0]]
      
      for i in range(1, len(items)):
          time_gap = (items[i].captured_at - items[i-1].captured_at).total_seconds() / 60
          
          if time_gap <= gap_minutes:
              current_cluster.append(items[i])
          else:
              clusters.append(current_cluster)
              current_cluster = [items[i]]
      
      clusters.append(current_cluster)
      return clusters
  ```

**Daily Summaries**
- [ ] Implement Celery beat job to trigger `generate_daily_summary` for each active user at 02:00 local time
- [ ] Define prompt templates + guardrails (Markdown format, event ID references, hallucination checks)
- [ ] Persist summaries to `daily_summaries` table with traceable source event IDs
- [ ] Surface summaries in dashboard + inject into chat context (preprompt hook)
- [ ] Collect user feedback (thumbs up/down + comment) and log for quality tuning
- [ ] Monitor summary generation latency + error rate with Prometheus counters
- [ ] Set up Celery Beat for daily scheduling:
  ```python
  from celery.schedules import crontab
  
  app.conf.beat_schedule = {
      'generate-daily-events': {
          'task': 'tasks.generate_daily_events',
          'schedule': crontab(hour=2, minute=0),  # Run at 2 AM
      },
  }
  ```

**Hybrid Retrieval System**
- [ ] Implement query understanding:
  ```python
  from dateparser import parse as parse_date
  
  def parse_query(query: str, user_id: str):
      # Extract dates
      parsed_date = parse_date(query, settings={'RELATIVE_BASE': datetime.now()})
      
      # Extract entities using LLM
      entities_response = await openai.ChatCompletion.create(
          model="gpt-4o-mini",
          messages=[{
              "role": "user",
              "content": f"Extract entity names from: '{query}'. Return JSON: {{\"people\": [], \"places\": [], \"objects\": []}}"
          }],
          response_format={"type": "json_object"}
      )
      entities = json.loads(entities_response.choices[0].message.content)
      
      # Determine query type
      query_type = classify_query(query)
      
      return {
          'date_range': get_date_range(parsed_date) if parsed_date else None,
          'entities': entities,
          'query_type': query_type,
          'original': query
      }
  ```
- [ ] Build composite retrieval function:
  ```python
  async def retrieve_relevant_context(query: str, user_id: str, top_k=10):
      parsed = parse_query(query, user_id)
      
      # 1. Vector search
      query_embedding = get_embedding(query)
      vector_hits = qdrant.search(
          collection_name=f"user_{user_id}",
          query_vector=query_embedding,
          limit=50,
          score_threshold=0.6
      )
      
      # 2. Apply temporal filter
      if parsed['date_range']:
          start, end = parsed['date_range']
          vector_hits = [
              h for h in vector_hits
              if start <= h.payload['timestamp'] <= end
          ]
      
      # 3. Graph-based entity boost
      if parsed['entities'].get('people') or parsed['entities'].get('places'):
          entity_names = parsed['entities']['people'] + parsed['entities']['places']
          related_items = get_items_with_entities(user_id, entity_names)
          related_item_ids = set(related_items)
          
          for hit in vector_hits:
              if hit.payload['item_id'] in related_item_ids:
                  hit.score *= 1.5
      
      # 4. Time decay (prefer recent items, but not too aggressive for MVP)
      now = datetime.now()
      for hit in vector_hits:
          days_old = (now - hit.payload['timestamp']).days
          time_decay = 1.0 / (1.0 + days_old * 0.01)
          hit.score *= time_decay
      
      # 5. Re-rank and return top-k
      ranked = sorted(vector_hits, key=lambda h: h.score, reverse=True)
      return ranked[:top_k]
  ```

**Conversation Memory (mem0 integration)**
- [ ] Install mem0: `pip install mem0ai`
- [ ] Initialize mem0 client:
  ```python
  from mem0 import Memory
  
  memory = Memory()
  ```
- [ ] Store conversations:
  ```python
  def store_conversation_turn(user_id, session_id, role, content):
      memory.add(
          messages=[{"role": role, "content": content}],
          user_id=user_id,
          session_id=session_id
      )
  ```
- [ ] Retrieve conversation history:
  ```python
  def get_conversation_context(user_id, session_id, limit=10):
      return memory.get_all(
          user_id=user_id,
          session_id=session_id,
          limit=limit
      )
  ```

### Deliverables
- ✅ Entities are extracted from all processed items
- ✅ Memory graph contains people, places, objects, events
- ✅ Daily events are automatically generated
- ✅ Hybrid retrieval combines vector + temporal + entity signals
- ✅ End-to-end ingest → process → retrieve smoke test passes with seed dataset

---

## Week 7-8: Chat Interface & RAG

### Objectives
- Build chat API with RAG logic
- Create conversational web interface
- Implement source citations
- Add conversation history

### Tasks

**Chat API Endpoint**
- [ ] Create chat endpoint:
  ```python
  @app.post("/api/v1/chat")
  async def chat(
      request: ChatRequest,
      user: User = Depends(get_current_user)
  ):
      # Parse query
      parsed = parse_query(request.message, user.id)
      
      # Retrieve relevant context
      context_hits = await retrieve_relevant_context(
          query=request.message,
          user_id=user.id,
          top_k=10
      )
      
      # Get conversation history from mem0
      conversation_history = get_conversation_context(
          user_id=user.id,
          session_id=request.session_id,
          limit=5
      )
      
      # Fetch recent daily summaries to ground responses
      daily_summaries = get_recent_daily_summaries(
          user_id=user.id,
          days=7
      )
      
      # Build prompt
      context_text = format_context(context_hits)
      prompt = build_chat_prompt(
          query=request.message,
          context=context_text,
          conversation_history=conversation_history,
          daily_summaries=daily_summaries
      )
      
      # Call LLM
      response = await openai.ChatCompletion.create(
          model="gpt-4o",
          messages=prompt,
          temperature=0.7,
          max_tokens=500
      )
      
      assistant_message = response.choices[0].message.content
      
      # Store in mem0
      store_conversation_turn(user.id, request.session_id, "user", request.message)
      store_conversation_turn(user.id, request.session_id, "assistant", assistant_message)
      
      # Format sources
      sources = [
          {
              'item_id': hit.payload['item_id'],
              'thumbnail': get_thumbnail_url(hit.payload['item_id']),
              'timestamp': hit.payload['timestamp'],
              'snippet': hit.payload['caption'][:150],
              'score': hit.score
          }
          for hit in context_hits[:5]
      ]
      
      return {
          'message': assistant_message,
          'sources': sources,
          'session_id': request.session_id
      }
  ```
- [ ] Enforce response time budget (<3s p95) and log latency to Prometheus histogram
- [ ] Record per-turn feedback + thumbs UI to feed accuracy metric
- [ ] Add guardrails for token usage (truncate context beyond 8k tokens, fallback to cheaper model on overflow)

**Prompt Engineering**
- [ ] Design system prompt:
  ```python
  SYSTEM_PROMPT = """You are a personal memory assistant. You help users recall and understand their past experiences based on their photos, videos, and notes.

  Guidelines:
  - Be warm, conversational, and helpful
  - Use only the provided context - don't make up information
  - If you're not sure, say "I don't have enough information"
  - When referencing specific memories, mention the date/time
  - Keep responses concise (2-3 sentences unless more detail is requested)
  - Use first-person perspective when talking about the user's memories
  """
  ```
- [ ] Create context formatting function:
  ```python
  def format_context(hits):
      context_items = []
      for i, hit in enumerate(hits):
          timestamp = hit.payload['timestamp'].strftime('%Y-%m-%d %H:%M')
          location = hit.payload.get('location', {})
          location_str = location.get('place_name', 'Unknown location')
          
          context_items.append(f"""
  [{i+1}] {timestamp} at {location_str}
  Caption: {hit.payload['caption']}
  OCR Text: {hit.payload.get('ocr_text', 'None')}
  """)
      
      return '\n'.join(context_items)
  ```
- [ ] Build full prompt:
```python
def build_chat_prompt(query, context, conversation_history, daily_summaries):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    if daily_summaries:
        summary_block = "\n".join(
            f"{s['summary_date']}: {s['content_markdown']}"
            for s in daily_summaries
        )
        messages.append({
            "role": "system",
            "content": f"Recent daily summaries:\n{summary_block}"
        })
    
    for turn in conversation_history or []:
        messages.append({"role": turn['role'], "content": turn['content']})
    
    user_message = f"""Here are relevant memories:

{context}

User question: {query}"""
    
    messages.append({"role": "user", "content": user_message})
    return messages
```

**Chat Frontend**
- [ ] Create `/chat` page with chat interface
- [ ] Implement message list with auto-scroll:
  ```typescript
  const ChatMessages = ({ messages }) => {
    const messagesEndRef = useRef<HTMLDivElement>(null);
    
    useEffect(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);
    
    return (
      <div className="flex-1 overflow-y-auto p-4">
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>
    );
  };
  ```
- [ ] Create message input component:
  ```typescript
  const ChatInput = ({ onSend, disabled }) => {
    const [input, setInput] = useState('');
    
    const handleSubmit = (e) => {
      e.preventDefault();
      if (input.trim()) {
        onSend(input);
        setInput('');
      }
    };
    
    return (
      <form onSubmit={handleSubmit} className="border-t p-4">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your memories..."
            disabled={disabled}
            className="flex-1 px-4 py-2 border rounded-lg"
          />
          <button type="submit" disabled={disabled || !input.trim()}>
            Send
          </button>
        </div>
      </form>
    );
  };
  ```
- [ ] Implement chat hook with React Query:
  ```typescript
  const useChat = (sessionId: string) => {
    const queryClient = useQueryClient();
    
    const { data: messages = [] } = useQuery({
      queryKey: ['chat', sessionId],
      queryFn: () => api.get(`/chat/sessions/${sessionId}`),
    });
    
    const { mutate: sendMessage, isPending } = useMutation({
      mutationFn: (message: string) => 
        api.post('/chat', { message, session_id: sessionId }),
      onSuccess: (response) => {
        queryClient.setQueryData(['chat', sessionId], (old) => [
          ...old,
          { role: 'user', content: message },
          { role: 'assistant', content: response.message, sources: response.sources }
        ]);
      },
    });
    
    return { messages, sendMessage, isPending };
  };
  ```

**Source Citations UI**
- [ ] Create source card component:
  ```typescript
  const SourceCard = ({ source }) => {
    return (
      <div className="border rounded-lg p-2 hover:shadow-md transition">
        <img
          src={source.thumbnail}
          alt="Memory"
          className="w-full h-32 object-cover rounded"
        />
        <p className="text-xs text-gray-500 mt-1">
          {new Date(source.timestamp).toLocaleString()}
        </p>
        <p className="text-sm mt-1 line-clamp-2">
          {source.snippet}
        </p>
      </div>
    );
  };
  ```
- [ ] Show recent daily summaries sidebar with toggle + ability to pin summary into prompt
- [ ] Add feedback controls (👍/👎 + optional comment) on assistant messages and send to `/feedback` API
- [ ] Add sources section to assistant messages:
  ```typescript
  const AssistantMessage = ({ message }) => {
    return (
      <div className="bg-gray-100 rounded-lg p-4 max-w-2xl">
        <p>{message.content}</p>
        {message.sources && message.sources.length > 0 && (
          <div className="mt-4">
            <p className="text-xs text-gray-500 mb-2">Sources:</p>
            <div className="grid grid-cols-3 gap-2">
              {message.sources.map((src) => (
                <SourceCard key={src.item_id} source={src} />
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };
  ```

**Session Management**
- [ ] Create session list endpoint:
  ```python
  @app.get("/api/v1/chat/sessions")
  async def list_sessions(user: User = Depends(get_current_user)):
      # Get unique session IDs from mem0
      sessions = memory.get_sessions(user_id=user.id)
      return [
          {
              'session_id': s.id,
              'title': s.title or f"Chat {s.created_at.strftime('%b %d')}",
              'created_at': s.created_at,
              'message_count': s.message_count
          }
          for s in sessions
      ]
  ```
- [ ] Add session sidebar in UI
- [ ] Implement "New Chat" button
- [ ] Add session title generation:
  ```python
  async def generate_session_title(first_message: str):
      response = await openai.ChatCompletion.create(
          model="gpt-4o-mini",
          messages=[{
              "role": "user",
              "content": f"Create a short 3-4 word title for a conversation that starts with: '{first_message}'"
          }],
          max_tokens=10
      )
      return response.choices[0].message.content
  ```

### Deliverables
- ✅ User can chat with <3s p95 response time backed by hybrid retrieval
- ✅ Responses include source citations + recent daily summary context
- ✅ Conversation history persists across sessions with mem0 integration
- ✅ Users can provide per-turn feedback captured in analytics

---

## Week 9-10: Timeline, Dashboard & Polish

### Objectives
- Build timeline visualization
- Create dashboard with statistics
- Add loading states and error handling
- Implement Notion integration
- Polish UI/UX

### Tasks

**Timeline View**
- [ ] Create timeline API endpoint:
  ```python
  @app.get("/api/v1/timeline")
  async def get_timeline(
      start_date: date,
      end_date: date,
      user: User = Depends(get_current_user)
  ):
      # Get events in date range
      events = db.query(events).filter(
          user_id=user.id,
          start_time >= start_date,
          end_time <= end_date
      ).all()
      
      # Get items per day
      items_by_day = db.query(
          func.date(source_items.captured_at).label('date'),
          func.count(source_items.id).label('count')
      ).filter(
          user_id=user.id,
          captured_at >= start_date,
          captured_at <= end_date
      ).group_by('date').all()
      
      return {
          'events': [format_event(e) for e in events],
          'activity': {day: count for day, count in items_by_day}
      }
  ```
- [ ] Build timeline UI with calendar view:
  ```typescript
  const Timeline = () => {
    const [selectedDate, setSelectedDate] = useState(new Date());
    
    const { data: timeline } = useQuery({
      queryKey: ['timeline', selectedDate.getMonth(), selectedDate.getFullYear()],
      queryFn: () => api.get('/timeline', {
        params: {
          start_date: startOfMonth(selectedDate),
          end_date: endOfMonth(selectedDate)
        }
      })
    });
    
    return (
      <div>
        <Calendar
          value={selectedDate}
          onChange={setSelectedDate}
          tileContent={({ date }) => (
            <ActivityDot count={timeline?.activity[date] || 0} />
          )}
        />
        <EventList
          events={timeline?.events.filter(e =>
            isSameDay(e.start_time, selectedDate)
          )}
        />
      </div>
    );
  };
  ```
- [ ] Add a day-details panel that combines the selected day's timeline events, thumbnails, embedded videos, and Gemini-generated text summaries (include links back to Google Photos originals when available)
- [ ] Implement infinite scroll for timeline

**Dashboard**
- [ ] Create dashboard stats endpoint:
  ```python
  @app.get("/api/v1/dashboard/stats")
  async def get_dashboard_stats(user: User = Depends(get_current_user)):
      total_items = db.query(func.count(source_items.id)).filter(
          user_id=user.id
      ).scalar()
      
      total_storage = db.query(func.sum(source_items.size)).filter(
          user_id=user.id
      ).scalar() or 0
      
      # Items uploaded in last 7 days
      recent_items = db.query(func.count(source_items.id)).filter(
          user_id=user.id,
          created_at >= datetime.now() - timedelta(days=7)
      ).scalar()
      
      # Connected sources
      connections = db.query(data_connections).filter(
          user_id=user.id,
          status='connected'
      ).count()
      
      # Activity heatmap (last 30 days)
      heatmap = db.query(
          func.date(source_items.captured_at).label('date'),
          func.count(source_items.id).label('count')
      ).filter(
          user_id=user.id,
          captured_at >= datetime.now() - timedelta(days=30)
      ).group_by('date').all()
      
      return {
          'total_items': total_items,
          'storage_used_gb': total_storage / (1024**3),
          'recent_uploads': recent_items,
          'connected_sources': connections,
          'activity_heatmap': heatmap
      }
  ```
- [ ] Build dashboard UI with stat cards:
  ```typescript
  const Dashboard = () => {
    const { data: stats } = useQuery({
      queryKey: ['dashboard-stats'],
      queryFn: () => api.get('/dashboard/stats')
    });
    
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Memories"
          value={stats?.total_items.toLocaleString()}
          icon={<PhotoIcon />}
        />
        <StatCard
          title="Storage Used"
          value={`${stats?.storage_used_gb.toFixed(2)} GB`}
          icon={<DatabaseIcon />}
        />
        <StatCard
          title="This Week"
          value={stats?.recent_uploads}
          icon={<CalendarIcon />}
        />
        <StatCard
          title="Connected Sources"
          value={stats?.connected_sources}
          icon={<LinkIcon />}
        />
      </div>
    );
  };
  ```
- [ ] Add activity heatmap component
- [ ] Show recent events and highlights

**Notion Integration**
- [ ] Set up Notion OAuth integration
- [ ] Create Notion sync task:
  ```python
  @celery.task
  def sync_notion(connection_id: str):
      connection = db.get(data_connections, connection_id)
      token = decrypt_token(connection.oauth_token_encrypted)
      
      notion = Client(auth=token)
      
      # Get all pages
      results = notion.search(filter={"property": "object", "value": "page"})
      
      for page in results.get('results', []):
          # Get page content
          blocks = notion.blocks.children.list(page['id'])
          
          # Extract text content
          text_content = extract_notion_text(blocks)
          
          # Create source_item
          item_id = db.insert(source_items, {
              'user_id': connection.user_id,
              'connection_id': connection_id,
              'provider': 'notion',
              'external_id': page['id'],
              'item_type': 'note',
              'captured_at': page['created_time'],
              'metadata': {
                  'title': page['properties']['title']['title'][0]['text']['content'],
                  'url': page['url']
              },
              'processing_status': 'pending'
          })
          
          # Store content
          db.insert(processed_content, {
              'source_item_id': item_id,
              'content_type': 'note',
              'content_text': text_content
          })
          
          # Queue for embedding
          celery.send_task('generate_embedding', args=[item_id])
  ```

**Error Handling & Loading States**
- [ ] Add error boundaries in React:
  ```typescript
  class ErrorBoundary extends React.Component {
    state = { hasError: false };
    
    static getDerivedStateFromError(error) {
      return { hasError: true };
    }
    
    render() {
      if (this.state.hasError) {
        return <ErrorFallback />;
      }
      return this.props.children;
    }
  }
  ```
- [ ] Add loading skeletons for all data fetching
- [ ] Implement toast notifications for errors:
  ```typescript
  import { toast } from 'sonner';
  
  const handleError = (error) => {
    toast.error(error.message || 'Something went wrong');
  };
  ```
- [ ] Add retry logic for failed tasks:
  ```python
  @celery.task(bind=True, max_retries=3)
  def process_item(self, item_id):
      try:
          # Processing logic
          pass
      except Exception as exc:
          raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
  ```

**UI Polish**
- [ ] Add animations with Framer Motion
- [ ] Implement dark mode toggle
- [ ] Create empty states for all pages
- [ ] Add keyboard shortcuts for chat (Cmd+K to focus)
- [ ] Optimize image loading with lazy loading
- [ ] Add tooltips and help text

**Performance Optimization**
- [ ] Implement pagination for timeline and item lists
- [ ] Add database indexes:
  ```sql
  CREATE INDEX idx_source_items_user_captured ON source_items(user_id, captured_at DESC);
  CREATE INDEX idx_events_user_date ON events(user_id, start_time DESC);
  CREATE INDEX idx_memory_nodes_user_type ON memory_nodes(user_id, node_type);
  ```
- [ ] Enable caching for dashboard stats (Redis)
- [ ] Optimize Qdrant queries with filters
- [ ] Add CDN for static assets

**Instrumentation & QA**
- [ ] Wire Prometheus/Grafana dashboards for ingestion throughput, processing SLA, chat latency, and model spend
- [ ] Schedule nightly synthetic end-to-end test (seed user) covering upload → processing → retrieval → chat
- [ ] Automate weekly metric export to Metabase for success metric review
- [ ] Run load test (Locust/k6) to validate chat p95 < 3s at target concurrency
- [ ] Validate security + privacy requirements (encryption, token rotation, data deletion) and document checklist

### Deliverables
- ✅ Timeline view shows user's activity by day with high-performance pagination
- ✅ Dashboard surfaces ingestion, storage, and activity metrics with caching
- ✅ Notion integration syncs pages incrementally into memory system
- ✅ Monitoring dashboards + synthetic tests cover ingest → chat pipeline
- ✅ UI is polished, accessible, and resilient with loading/error states

---

## Week 11-12: Testing, Deployment & Launch

### Objectives
- Comprehensive testing
- Production deployment
- Monitoring and observability
- Onboarding flow
- Pilot user launch

### Tasks

**Testing**
- [ ] Write API integration tests:
  ```python
  # tests/test_upload.py
  def test_batch_upload(client, auth_token):
      files = [
          ('files', open('test_image.jpg', 'rb')),
          ('files', open('test_image2.jpg', 'rb'))
      ]
      response = client.post(
          '/api/v1/upload/batch',
          files=files,
          headers={'Authorization': f'Bearer {auth_token}'}
      )
      assert response.status_code == 200
      assert 'batch_id' in response.json()
  ```
- [ ] Test processing pipeline end-to-end
- [ ] Write frontend E2E tests with Playwright:
  ```typescript
  test('user can upload and chat about photos', async ({ page }) => {
    await page.goto('/upload');
    await page.setInputFiles('input[type="file"]', ['test.jpg']);
    await page.click('button:has-text("Upload")');
    await expect(page.locator('text=Upload complete')).toBeVisible();
    
    await page.goto('/chat');
    await page.fill('input[placeholder="Ask about your memories..."]', 'What did I upload?');
    await page.click('button:has-text("Send")');
    await expect(page.locator('text=test.jpg')).toBeVisible();
  });
  ```
- [ ] Test retrieval quality with sample queries
- [ ] Load test with Locust:
  ```python
  from locust import HttpUser, task
  
  class LifelogUser(HttpUser):
      @task
      def chat(self):
          self.client.post("/api/v1/chat", json={
              "message": "What did I do yesterday?",
              "session_id": "test-session"
          })
  ```

**Production Deployment**
- [ ] Set up production Supabase project
- [ ] Provision Qdrant Cloud production cluster
- [ ] Deploy API to Cloud Run (or Railway):
  ```yaml
  # cloudbuild.yaml
  steps:
    - name: 'gcr.io/cloud-builders/docker'
      args: ['build', '-t', 'gcr.io/$PROJECT_ID/lifelog-api', './services/api']
    - name: 'gcr.io/cloud-builders/docker'
      args: ['push', 'gcr.io/$PROJECT_ID/lifelog-api']
    - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
      args: ['gcloud', 'run', 'deploy', 'lifelog-api', 
             '--image', 'gcr.io/$PROJECT_ID/lifelog-api',
             '--region', 'us-central1']
  ```
- [ ] Deploy Celery workers as separate service
- [ ] Provision GPU-backed media processing workers (RunPod/Modal) for Whisper + captioning
- [ ] Set up Redis on Cloud Memorystore (or Upstash)
- [ ] Deploy Next.js to Vercel:
  ```bash
  vercel --prod
  ```
- [ ] Configure environment variables in production
- [ ] Set up custom domain and SSL

**Staging & Dev Experience (Deferred from Week 1-2)**
- [ ] Set up pnpm workspaces or Turborepo (only if/when shared JS packages are introduced)
- [ ] Initialize Git hooks with Husky (pre-commit linting)
- [ ] Create Qdrant Cloud instance (1GB dev) for shared dev/staging usage if local Qdrant isn't sufficient
- [ ] Provision staging environment (Supabase + Qdrant) with seeded demo data for onboarding tests

**Monitoring & Observability**
- [ ] Set up Sentry for error tracking:
  ```python
  import sentry_sdk
  
  sentry_sdk.init(
      dsn="your-dsn",
      traces_sample_rate=0.1,
  )
  ```
- [ ] Add Prometheus metrics:
  ```python
  from prometheus_client import Counter, Histogram
  
  upload_counter = Counter('uploads_total', 'Total uploads')
  processing_duration = Histogram('processing_duration_seconds', 'Processing time')
  ```
- [ ] Set up Grafana dashboard for key metrics
- [ ] Stream model usage + spend to monitoring (per provider, per task)
- [ ] Create alerts for:
  - High error rate (>5%)
  - Long processing queue (>100 items)
  - High API latency (>3s p95)
  - Low disk space

**Onboarding Flow**
- [ ] Create welcome screen after signup
- [ ] Build step-by-step onboarding:
  1. Connect first data source
  2. Upload first batch
  3. Wait for processing
  4. Try first chat query
- [ ] Add interactive tutorial tooltips
- [ ] Create demo account with sample data
- [ ] Publish Apple Photos setup guide (app-specific password + manual export fallback) in-app

**Documentation**
- [ ] Write README with setup instructions
- [ ] Create API documentation with Swagger
- [ ] Write user guide for key features
- [ ] Document deployment process

**Pilot Launch**
- [ ] Invite 20 pilot users
- [ ] Set up feedback form (Typeform or Google Forms)
- [ ] Create feedback collection in-app:
  ```typescript
  const FeedbackWidget = () => {
    return (
      <button onClick={() => openFeedbackModal()}>
        💬 Give Feedback
      </button>
    );
  };
  ```
- [ ] Monitor usage and errors closely
- [ ] Schedule weekly check-ins with pilot users
- [ ] Iterate based on feedback

### Deliverables
- ✅ Production stack (API, GPU workers, Celery, web) is live and stable
- ✅ Monitoring, alerts, and cost dashboards are configured
- ✅ 20 pilot users onboarded with at least one Google + Apple source connected
- ✅ Feedback collection + support loops are active
- ✅ Success metrics instrumentation confirms MVP readiness to scale

---

## Post-MVP: Next Steps

### Immediate Priorities (Week 13+)
1. Analyze pilot user feedback
2. Fix critical bugs and UX issues
3. Optimize processing speed and cost
4. Add most requested features

### Ingestion Expansion (Post-MVP)

**Desktop Capture App (macOS first)**
- [ ] Build a lightweight menubar app that captures screenshots every 30s while the screen is active (idle detection + pause toggle)
- [ ] Buffer locally (ring buffer) and upload to a user-selected Google Drive folder (either via Drive sync folder or Drive API)
- [ ] Add privacy controls: pause/resume, exclude apps/windows (best-effort), and “delete local after upload”
- [ ] Treat uploaded screenshots as normal assets (same processing: OCR/caption/embeddings) with `provider=desktop_capture`

**Google Drive Connector (Cloud Sync Bridge)**
- [ ] Add `google_drive` as a `data_connections.provider` and implement OAuth + folder picker
- [ ] Backfill + incremental sync for a specific folder using the Drive Changes API (or folder query + modifiedTime cursor)
- [ ] Map Drive files into the existing pipeline by writing to Supabase Storage (or using signed URLs) and then calling `/upload/ingest`
- [ ] De-dupe by `(connection_id, external_id)` plus optional SHA256 if available

**Oura Ring Connector**
- [ ] Implement OAuth connection and nightly/daily ingestion of sleep/readiness/activity summaries into structured tables
- [ ] Generate “daily health events” for the Timeline and embed short textual summaries for semantic retrieval
- [ ] Optional: same-day refresh job (e.g., hourly) with caching to reduce API calls

**Apple Photos (via Mac Agent / Export Bridge)**
- [ ] Start with a Mac-only approach: the desktop app exports new Photos items into a local folder (or directly to the Drive bridge folder)
- [ ] Ingest exported media through the same pipeline (mark `provider=apple_photos_export`)
- [ ] Defer any iCloud-native integration until a reliable API/approach is confirmed

**ESP32 Camera Ingestion (XIAO ESP32S3 Sense)**
- [ ] Add a `devices` table: `id`, `user_id`, `name`, `device_token_hash`, `created_at`, `last_seen_at`, `revoked_at`
- [ ] Add API endpoints for device pairing and ingestion:
  - [ ] `POST /devices/pair` (user authed) → returns `device_id` + one-time `pairing_code`
  - [ ] `POST /devices/activate` (pairing_code) → returns long-lived `device_token`
  - [ ] `POST /devices/upload-url` (header `X-Device-Token`) → proxy to `/storage/upload-url` with a safe prefix like `devices/{device_id}`
  - [ ] `POST /devices/ingest` (header `X-Device-Token`) → creates a `SourceItem` without requiring `user_id` in the payload
- [ ] Firmware (Arduino) behavior: capture JPEG every 30s to SD, and when Wi-Fi is available (e.g., phone hotspot) upload backlog via `/devices/upload-url` + `/devices/ingest`
- [ ] Spec: `docs/esp32-ingestion/README.md`

### Live Data Access (MCP) Roadmap
- [ ] Define a query router that detects time-sensitive or action intents (now/today/current, play/navigate) and decides when to call live tools vs. RAG
- [ ] Design a normalized `live_context` schema for tool responses (source, retrieved_at, time_range, records, reliability)
- [ ] Implement MCP connectors (read-only first) for Google Maps and Spotify with rate limits + 15-60 min caching
- [ ] Optional: add an Oura “live refresh” MCP tool for same-day data when it’s not yet ingested
- [ ] Merge live tool results into chat context with explicit "live" badges and fallback messaging if tools fail
- [ ] Add async backfill to store live results into `source_items`/`processed_content` so they become searchable memory

### Future Phases
- **Phase 2:** Advanced graph analytics, face clustering, richer retrieval evaluation tooling
- **Phase 3:** Mobile apps, desktop capture agent, automated story generation
- **Phase 4:** Sharing/collaboration features, developer API, enterprise deployment options
- **Phase 5:** Integration with Google Cloud—centralized billing, IAM, managed HA services, regional compliance, etc. Gradually shift pieces: Cloud Run for containers, Cloud SQL + Memorystore, Cloud Storage, Identity Platform, Vertex AI, etc.

---

## Team & Resources

### Roles
- **Full-stack Developer:** API, workers, frontend (Week 1-12)
- **Optional: ML Engineer:** Optimize embedding and retrieval (Week 6+)

### Infrastructure Costs (Estimated Monthly)
- Supabase Pro: $25
- Qdrant Cloud: $0-50 (depending on data volume)
- Railway/Cloud Run: $20-50
- Vercel: $0 (hobby) or $20 (pro)
- OpenAI API: $50-200 (depending on usage)
- **Total: $120-345/month for MVP**

---

## Success Metrics Tracking

| Metric | Target | Measurement |
|--------|--------|-------------|
| User signups | 20 pilot users | Supabase Auth count |
| Data sources connected | 80% of pilots connect both Google + Apple | `data_connections` table filtered by provider |
| Media assets processed | 10,000+ mixed items | `source_items` count + processing metrics |
| Chat queries | 100+ total | OpenTelemetry traces + API logs |
| Query accuracy | 80%+ relevant | In-app thumbs feedback tagged per conversation turn |
| Response time | <3s p95 | Prometheus histogram (`chat_latency_seconds`) |
| Daily summary usefulness | 70% thumbs-up | Daily summary feedback table |
| User retention | 40% D14 | Analytics |

---

*End of Development Plan*
