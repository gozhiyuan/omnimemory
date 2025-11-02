# Product Requirements Document (PRD): AI-Powered LifeLog Platform

> **Status:** This is the master PRD. It has been updated to include a recommended MVP stack and links to the formal development plan and project structure.

> **Quick Links:**
> - [Development Plan](./DEVELOPMENT_PLAN.md)
> - [Local Docker Environment](./docker-compose.yml)

---

## 1. Executive Summary

### Recommended MVP Stack

For the fastest path to a functional MVP, the following stack is recommended. This minimizes operational overhead and focuses on delivering the core user experience.

- **Authentication, Database & Storage:** **Supabase** (Postgres with `pgvector`, Object Storage, Auth).
- **Web Frontend:** **Next.js** (Deployed on Vercel).
- **Backend API & Workers:** **FastAPI** and **Celery** (Deployed as containers on Railway, Render, or Cloud Run).
- **Vector Database:** **Qdrant Cloud** (or `pgvector` for initial prototyping).
- **Local Development:** **Docker Compose** to orchestrate all backend services.


---

## 1. Executive Summary

Build an AI-driven personal lifelog platform that continuously collects multimodal data (screenshots, photos, short videos, documents, location traces, social media), preprocesses and indexes it into a personal knowledge graph + vector store, and exposes a conversational interface (chatbot + timeline + vlog generator) powered by RAG and multimodal LLMs. The product emphasizes privacy-by-default, fine-grained sharing, and the ability for users to export or self-host components.

Key differentiators:

- Multimodal daily recording (e.g., 15s clips / screenshots) + automated daily summarization
- Context engineering & memory graph (entity/event graph) for episodic memory retrieval
- Shareable mini-chatbots with scoped access controls
- Extensible connectors for Google Photos, Apple Photos, social platforms

---

## 2. Goals & Success Metrics

### Goals

- MVP in 8–12 weeks demonstrating upload → preprocessing → RAG chat flow
- Accurate day summaries (ROUGE/F1 proxies using synthetic evaluation) and user satisfaction ≥ 4/5 on pilot
- Privacy-first: user data isolated and optional local processing

### Success Metrics

- Time-to-first-answer (median) < 1.5s for cached queries, < 3s for fresh queries
- Embedding retrieval precision\@5 > 0.75 (internal eval)
- Daily active users (pilot) retention 40% after 14 days
- System uptime 99.5%

---

## 3. Detailed Feature List

### 3.1 Data Ingestion & Connectors

- Manual upload (web/mobile) for images, videos, PDFs, ePubs
- Automatic periodic capture:
  - Desktop agent / extension for screenshots (every 15s configurable)
  - Mobile background capture for screenshots & short clips (opt-in), or manual recording via app
- Connectors (OAuth + selective sync): Google Photos, Apple iCloud Photos, Google Maps Timeline, Twitter/X, Instagram, TikTok (via API or screen-record upload)
- Social activity ingestion: webhook / scheduled scraping for authorized accounts

### 3.2 Preprocessing

- File type detection and metadata extraction
- Image/video frame extraction (ffmpeg) → select key frames (every N seconds or scene-change)
- OCR (Tesseract / PaddleOCR or cloud OCR) for detected text content
- ASR (OpenAI Whisper or cloud ASR) for videos/voice notes
- Image captioning (BLIP, Gemini Vision, Qwen-VL) and tag classification via CLIP-like models
- Face clustering & person identity mapping (optional, on-device face encodings stored encrypted)
- Geolocation reverse-geocoding (Google Maps API / Nominatim)
- Deduplication & compression

### 3.3 Context Engineering & Memory Graph

- Entity extraction (people, places, objects, events) using LLMs + NER models
- Event segmentation (temporal clustering of items into events)
- Memory graph (nodes: person/place/event/object; edges: "attended", "at", "ate", "worked\_on")
- Graph DB: lightweight solution using PostgreSQL + graph tables or Neo4j for complex traversals
- Integration options:
  - **Graphiti (Zep)** for conversation memory + entity-aware retrieval
  - **Mem0** for lightweight session memory & local caching
  - **Dayflow / MineContext** ideas for periodic desktop context-capture and prompt stitching

### 3.4 RAG & Retrieval

- Embed textual/caption/OCR results to vector DB (Qdrant / Pinecone / Weaviate)
- Hybrid ranking: combine semantic similarity + time decay + entity overlap + location match
- Context builder: collect top-K vectors, then build condensed context snippets with instructions for the LLM
- Use LlamaIndex or LangChain adapter for composing retrieval-to-LLM flows

### 3.5 AI Services & Outputs

- Daily text summary (short/long), timeline JSON, and optionally narrated vlog (video montage + TTS)
- Chatbot with RAG-powered answers and follow-up question support
- Shareable mini-chatbots (scoped API keys / share links) with RBAC and expiration
- Export options: PDF daily journals, CSV of events, downloadable video montages

### 3.6 Privacy Controls

- Data default: private
- Granular sharing by collection, date-range, tag, person, or event
- Optional client-side-only preprocessing modes (face recognition etc.)
- Data retention policies and user-initiated full deletion

---

## 4. System Architecture (Detailed)

### 4.1 Components

1. **Client Layer**
   - Next.js Web App (UI/Chat/Dashboard)
   - React Native (Expo) Mobile App (capture/upload, local settings)
   - Desktop Capture Agent (Electron / native) for screenshots and uploads
2. **API Gateway**
   - FastAPI (Python) handling auth, uploads, task enqueueing, admin endpoints
   - Optional Next.js API routes for lightweight proxy operations (auth, SSR)
3. **Storage & DB**
   - Object Storage: Supabase Storage or S3-compatible (MinIO for self-host)
   - Primary DB: PostgreSQL (hosted by Supabase or RDS) with `pgvector` extension
   - Vector DB: Qdrant (self-host or cloud)
   - Optional Graph DB: Neo4j (or implement graph tables in Postgres)
4. **Processing & Task Queue**
   - Celery + Redis or RabbitMQ for task orchestration
   - Worker pool for OCR, ASR, captioning, embeddings, graph updates
5. **AI Model Layer**
   - Hosted API: Gemini, GPT-4o, Claude 3.5 / 4.1 with multimodal support
   - Local models (optional): LLaVA, InternVL, Qwen-VL for private deployments
6. **RAG & Context Layer**
   - LlamaIndex / LangChain adapters
   - Graphiti (Zep) for memory / entity-aware retrieval
7. **Monitoring & Observability**
   - Prometheus + Grafana for system metrics
   - Sentry for errors
   - Honeycomb for tracing (optional)

### 4.2 Data Flow (Step-by-step)

1. Client uploads file → FastAPI receives multipart and stores raw file to object storage
2. FastAPI creates `files` record in Postgres, returns acknowledgment
3. FastAPI enqueues preprocess task to Celery (file id)
4. Worker downloads file:
   - Extract basic metadata (duration, resolution)
   - For video: extract keyframes (ffmpeg)
   - Run OCR / ASR
   - Run image captioning & CLIP tagger
   - Create text chunks and call embedding API
   - Upsert embeddings to Qdrant with payload linking to `file.id` and metadata
   - Entity extraction & graph insertion/update
5. Daily scheduler triggers `daily_summary` job per user which:
   - Retrieves day's embeddings/events
   - Uses context-engineer to create prompt + context
   - Calls multimodal LLM for summary + possible montage plan
   - Stores summary in `summaries` table and creates events
6. Chat flow:
   - On user query, retrieve top-K embeddings relevant to query + graph-based context
   - Build RAG prompt and call LLM for final answer
   - Log conversation for session memory (Mem0/Zep)

---

## 5. Detailed Data Schema (Postgres)

> Using Supabase-managed Postgres with `pgvector` extension.

### Table: users (supabase.auth users in practice)

```
CREATE TABLE users (
  id UUID PRIMARY KEY,
  display_name TEXT,
  email TEXT,
  created_at TIMESTAMP DEFAULT now(),
  settings JSONB
);
```

### Table: files

```
CREATE TABLE files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  file_type TEXT, -- image/video/pdf/text
  storage_path TEXT,
  mime_type TEXT,
  size BIGINT,
  captured_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now(),
  metadata JSONB
);
```

### Table: chunks (text chunks / captions / ocr snippets)

```
CREATE TABLE chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id UUID REFERENCES files(id),
  user_id UUID,
  chunk_text TEXT,
  chunk_source TEXT, -- caption, ocr, asr
  created_at TIMESTAMP DEFAULT now()
);
```

### Table: embeddings (pgvector)

```
CREATE TABLE embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id UUID REFERENCES chunks(id),
  user_id UUID,
  embedding vector(1536),
  meta JSONB,
  created_at TIMESTAMP DEFAULT now()
);
```

### Table: events (aggregated user events)

```
CREATE TABLE events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,
  title TEXT,
  start_time TIMESTAMP,
  end_time TIMESTAMP,
  location JSONB,
  involved JSONB, -- persons/objects
  source_ids UUID[], -- related chunk ids
  summary TEXT,
  created_at TIMESTAMP DEFAULT now()
);
```

### Table: graph\_nodes & graph\_edges (optional)

```
CREATE TABLE graph_nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,
  node_type TEXT, -- person/place/event/object
  properties JSONB,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE graph_edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  src UUID REFERENCES graph_nodes(id),
  dst UUID REFERENCES graph_nodes(id),
  relation TEXT,
  weight FLOAT DEFAULT 1.0,
  created_at TIMESTAMP DEFAULT now()
);
```

---

## 6. API Contract Examples

### Authentication

- Use Supabase Auth (JWT). Every API call requires Authorization: Bearer&#x20;

### Upload (FastAPI)

```
POST /api/v1/upload
Headers: Authorization
Body: multipart/form-data (file, captured_at)
Response: { file_id }
```

### Query Chat

```
POST /api/v1/chat
Body: { user_id, session_id, prompt }
Response: { reply, sources: [{chunk_id, score, text_excerpt}] }
```

### Get Daily Summary

```
GET /api/v1/summaries?date=2025-11-01
```

---

## 7. Implementation Details & Example Code

### 7.1 FastAPI Upload Endpoint (detailed)

```python
from fastapi import FastAPI, UploadFile, Depends, HTTPException
from fastapi.responses import JSONResponse
import uuid

app = FastAPI()

@app.post('/api/v1/upload')
async def upload_file(file: UploadFile, user=Depends(get_current_user), captured_at: str = None):
    file_id = str(uuid.uuid4())
    path = f"{user.id}/raw/{file_id}-{file.filename}"
    # Save to Supabase storage (pseudo)
    supabase.storage.from_('user_data').upload(path, await file.read())

    # Insert file record
    db.execute('''INSERT INTO files (id, user_id, file_type, storage_path, mime_type, captured_at) VALUES (...)''')

    # Enqueue preprocess
    celery.send_task('tasks.preprocess_file', args=[file_id])

    return JSONResponse({'file_id': file_id})
```

### 7.2 Celery Preprocess Task

```python
from celery import Celery
from some_ai import run_ocr, image_caption, get_embedding

celery = Celery('tasks', broker='redis://localhost:6379/0')

@celery.task
def preprocess_file(file_id):
    file = db.fetch_file(file_id)
    data = storage.download(file.storage_path)

    if file.file_type.startswith('video'):
        frames = extract_key_frames(data)
        for frame in frames:
            caption = image_caption(frame)
            chunk_id = db.insert_chunk(file.id, caption, 'caption')
            emb = get_embedding(caption)
            qdrant.upsert(chunk_id, emb, meta={'file_id': file.id})
    elif file.file_type.startswith('image'):
        ocr_text = run_ocr(data)
        caption = image_caption(data)
        # store both
    # update graph entities
    update_memory_graph(file.user_id, file.id)
```

### 7.3 Chat Handler (RAG using LlamaIndex)

```python
from llama_index import GPTVectorStoreIndex, SimpleDirectoryReader

def answer_query(user_id, prompt):
    # 1. fetch top embeddings from Qdrant
    hits = qdrant.query(prompt, top_k=10, user_id=user_id)

    # 2. build context
    context_text = '
'.join([h['text_excerpt'] for h in hits])

    # 3. call LLM
    final_prompt = f"You are a personal assistant. Use the following context:
{context_text}
User: {prompt}"
    resp = call_llm(final_prompt)
    return resp
```

---

## 8. Context Engineering Details

### 8.1 Prompting Patterns

- Use instruction templates that emphasize privacy, factuality and brevity
- Add system messages like:
  - "You are an assistant for a single user's personal data. Use only provided context. If unsure, say you don't know."
- Chunk selection: prefer recent events and entity matches; use composite scoring

### 8.2 Hybrid Scoring Function (Pseudo)

```
score = alpha * semantic_sim + beta * time_score + gamma * entity_overlap + delta * location_score
```

- time\_score can decay older items (or boost items near query date)
- entity\_overlap counts shared named entities

### 8.3 Memory Graph & Traversal

- To answer questions like "When did I last meet Alice?":
  - Find graph node for "Alice" → traverse edges labeled "attended" or "met" → return connected events sorted by date

---

## 9. Integrating Open Source Projects

### 9.1 Graphiti (Zep)

- Use Graphiti to maintain conversation-centric memory and entity tags
- Integrate its SDK to store & query conversation memories and perform entity-aware retrieval
- Replace or augment chunk selection in RAG with Graphiti query results

### 9.2 Mem0

- Use Mem0 for in-session memory caching and fast recall of recent interactions
- Great for storing conversation state and short ephemeral memories

### 9.3 Dayflow & MineContext Inspiration

- Implement a Desktop Agent that captures screenshots at configured intervals, extracts context and sends to server (like MineContext)
- Dayflow’s pipeline for daily aggregation & prompt design is directly reusable; adapt its event segmentation logic

---

## 10. Frontend Stack (Detailed)

### 10.1 Web (Next.js / App Router)

- **UI Framework:** React + TailwindCSS + shadcn/ui + Radix primitives
- **State Management:** React Query (TanStack Query) for server state; Zustand for local UI state
- **Chat UI:** React-chat-elements / custom chat using MUI components
- **Auth:** Supabase Auth client + Server Actions for token refresh
- **Media Uploads:** Resumable uploads via tus or presigned URL + background upload with Service Workers
- **Maps / Timeline:** Mapbox GL JS or Google Maps SDK for timeline visualization
- **Realtime:** Supabase Realtime or WebSocket for live upload progress and job updates

### 10.2 Mobile (React Native + Expo)

- Capture: Camera & Screen Capture (Expo, react-native-vision-camera)
- Background Tasks: expo-task-manager + BackgroundFetch (iOS restrictions apply)
- Upload: use native resumable uploads, chunking for large videos
- Local Storage: SQLite or AsyncStorage for caching before upload
- Push Notifications: Expo push notifications or Firebase messaging

### 10.3 Desktop Capture Agent

- Electron (cross-platform) or native agents for macOS (Swift) and Windows (C#)
- Capture: screenshot API, optional window-level capture
- Local queue for uploads & throttling

---

## 11. Backend & Infra Stack (Detailed)

### 11.1 Service Components

- **FastAPI** for REST endpoints and admin APIs
- **Workers:** Celery + Redis for background jobs
- **Vector DB:** Qdrant (Docker + managed) or Pinecone for simpler ops
- **Postgres:** Supabase-managed Postgres with `pgvector` extension
- **Storage:** Supabase Storage / S3
- **Reverse Proxy / Ingress:** Traefik or Nginx
- **Authentication:** Supabase Auth (supports OAuth providers)

### 11.2 Deployment

- Dev: Docker Compose (Postgres, Redis, Qdrant, FastAPI, Celery, Next.js)
- Staging/Prod: Docker images to Cloud Run / ECS / Fly.io; Next.js deployed to Vercel for SSR
- CI/CD: GitHub Actions: run tests, build images, push to registry, deploy

### 11.3 Scaling Considerations

- Scale workers horizontally, autoscale Celery worker pods
- Qdrant scaling: sharding/replicas depending on dataset size
- Use S3 lifecycle + compression to manage storage costs

---

## 12. Security, Privacy & Compliance

- Encryption at rest (object storage with SSE), in transit (TLS everywhere)
- Per-user row-level security (RLS) in Postgres
- Optional end-to-end encryption for sensitive artifacts (keys held by user)
- Audit logs for sharing actions and deletions
- GDPR / CCPA compliance guidance for data deletion & export

---

## 13. Dev Plan (Detailed Timeline & Tasks)

### Phase 0 — Prep (1 week)

- Create repo templates (mono-repo: apps/{web, mobile, agent}, services/{api, workers})
- Provision Supabase, S3, Qdrant dev instances
- Setup CI/CD skeleton

### Phase 1 — Core MVP (4 weeks)

- Week 1: FastAPI upload endpoint, Supabase storage integration, DB schema
- Week 2: Celery + worker to run basic image OCR & caption (use local BLIP or API), Qdrant embedding upsert
- Week 3: Next.js chat UI, basic RAG retrieval (query Qdrant + pass to LLM API), Auth integration
- Week 4: End-to-end test: upload -> preprocess -> retrieve -> chat; user acceptance testing

### Phase 2 — Automation & Connectors (6 weeks)

- Desktop agent (Electron) prototype for screenshot capture
- Mobile capture flow (Expo) for video & screenshots
- Connectors: Google Photos & Maps OAuth flows
- Implement daily summary scheduler & store summaries

### Phase 3 — Context Engine & Memory Graph (6 weeks)

- Integrate Graphiti / Zep for memory graph & entity-aware retrieval
- Implement event segmentation & graph-based queries
- Add Mem0 for session memory caching

### Phase 4 — Multimodal LLMs & Vlog Generation (6 weeks)

- Replace captioning with Gemini Vision / GPT-4V for better multimodal reasoning
- Build video montage service: ffmpeg-based composition + TTS
- Add sharing & RBAC UI + audit logs

### Phase 5 — Hardening & Scaling (4 weeks)

- Monitoring & alerting, cost optimization
- Security audit & privacy features
- Performance tuning and deployment to production

---

## 14. Testing Strategy

- Unit tests for API endpoints (pytest + requests)
- Integration tests for upload → preprocess → embed pipeline (local Qdrant)
- E2E tests using Playwright for frontend flows (upload, chat)
- Load testing for worker throughput (Locust)
- Model output evaluation via synthetic QA datasets (measure retrieval quality)

---

## 15. Observability & Cost Management

- Track usage: storage GB per user, API calls to LLMs, embedding calls
- Implement quotas & rate limits per-user / per-plan
- Alerts for Qdrant storage growth, worker queue backlogs
- Cost saving: batch embedding calls, caching frequent queries, compress media

---

## 16. Example Roadmap & Deliverables

- Week 2: Running local dev environment with Next.js, FastAPI, Supabase, Qdrant
- Week 6: Public pilot with 10–20 users ingesting daily screenshots and chatting
- Week 12–16: Multimodal LLM integrated, daily summaries & vlog generation

---

## 17. Appendix: Resources & References

- Graphiti (Zep): [https://github.com/getzep/graphiti](https://github.com/getzep/graphiti)
- Mem0: [https://github.com/mem0ai/mem0](https://github.com/mem0ai/mem0)
- MineContext: [https://github.com/volcengine/MineContext](https://github.com/volcengine/MineContext)
- Dayflow: [https://github.com/JerryZLiu/Dayflow](https://github.com/JerryZLiu/Dayflow)
- LlamaIndex: [https://github.com/jerryjliu/llama\_index](https://github.com/jerryjliu/llama_index)
- Qdrant: [https://qdrant.tech/](https://qdrant.tech/)
- Supabase: [https://supabase.com/](https://supabase.com/)

---

*End of Document.*

