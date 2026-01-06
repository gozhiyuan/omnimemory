# OmniMemory - MVP Development Plan (12 Weeks)

> **Goal:** Ship a working web app where users can connect data sources, upload files, and chat about their memories
> **Timeline:** 12 weeks to pilot launch
> **Team Size:** 1-2 developers

## Current Status Snapshot (Actual Implementation)

Implemented:
- Ingestion pipeline (uploads + Google Photos Picker) with dedupe, derived artifacts, contexts, embeddings, episodes, daily summaries, and Qdrant indexing.
- Timeline day view (episodes + daily summaries + item detail), timeline/all pagination, and ingestion recent list pagination.
- RAG chat API with query parsing, rerank, citations, and chat session persistence in Postgres.
- Chat image upload (VLM description) + chat attachments.
- Downstream agents: Cartoon Day Summary and Day Insights Infographic.
- Dashboard aggregates + usage metrics.
- Authentik-backed OIDC authentication (bearer token validation + AuthGate).
- Settings API + Settings UI (profile, language, timezone, timeline defaults, weekly recap toggle).

Deferred or not implemented yet:
- Advanced auth (RBAC, social logins, account management).
- Memory graph population and graph-based retrieval.
- Scheduled lifecycle retention jobs.
- Notion integration and other connectors.
- Mobile/desktop apps and auto-capture.

---

## Week 1-2: Foundation & Infrastructure Setup

### Objectives
- Set up development environment
- Establish core infrastructure
- Implement OIDC authentication
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
- [ ] Provision self-hosted core services (Postgres + RustFS + Valkey) for OSS-first deployment
- [ ] Optional: create Supabase project if using managed Postgres/Auth later
  - [ ] Enable Email auth provider (if using Supabase Auth for login)
  - [ ] Enable Google OAuth provider later with Google Photos integration
- [ ] Set up GitHub repository and enable GitHub Actions

**Database Setup**
- [ ] Create database schema in Postgres (local; optional Supabase if managed later):
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
- [ ] Create database migration scripts and apply them to local Postgres (and Supabase if used)
- [ ] Skip `pgvector` for now (using Qdrant); enable later only if hybrid Postgres vector search is needed
- [ ] Defer Row-Level Security (RLS) until auth provider + direct DB access is finalized
- [ ] Defer encryption key storage (`vault.secrets`/KMS) until OAuth or other app-specific tokens are stored

**Backend API (FastAPI)**
- [ ] Initialize FastAPI project with poetry/pipenv:
  ```python
  # services/api/pyproject.toml
  dependencies = [
      "fastapi",
      "uvicorn",
      "boto3",
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
- [ ] Set up S3-compatible storage integration (RustFS/MinIO/AWS) for presigned uploads — done
- [x] OIDC bearer auth middleware (JWKS validation via Authentik) — done
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

**Authentication (OIDC)**
- [x] Authentik local stack in `orchestration/docker-compose.dev.yml` with `make authentik-up`
- [x] API env config: `AUTH_ENABLED=true`, `OIDC_ISSUER_URL`, `OIDC_JWKS_URL`, `OIDC_AUDIENCE`
- [x] Web env config: `VITE_OIDC_ISSUER_URL`, `VITE_OIDC_CLIENT_ID`, `VITE_OIDC_AUTH_URL`, `VITE_OIDC_TOKEN_URL`

**Storage Abstraction & Presigned URLs**
- [ ] Implement storage provider interface (memory + S3/RustFS, optional Supabase) — done
- [ ] Configure S3 storage env vars for presigned uploads (`S3_*`, `STORAGE_PROVIDER=s3`) — done
- [ ] Add API endpoints for presigned flows (`POST /storage/upload-url`, `POST /storage/download-url`) — done
- [ ] Use presigned URL TTL from config (`presigned_url_ttl_seconds`) — done
- [ ] Use storage fetch helper in the processing pipeline — done
- [ ] Defer `thumbnails` bucket setup until thumbnail generation lands

**Task Queue (Celery)**
- [ ] Set up Valkey locally (Docker) — done
- [ ] Initialize Celery app with Valkey/Redis broker + result backend — done
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
- [ ] Flesh out the Ingest tab so manual uploads use `POST /storage/upload-url` + `PUT <signed url>` + `POST /upload/ingest`, and the Google Photos connector UI triggers OAuth, shows sync status, and surfaces retry/manage actions
- [ ] Build chat + ingestion workflows against the Gemini API via `@google/genai` services, reusing hooks/utilities under `apps/web/services`
- [ ] Drive the Timeline view from the `/timeline` API (daily activity heatmap + detail drawer per day showing photos, videos, summaries) and hydrate the Dashboard components with stats returned by `/dashboard/stats`
- [ ] Add lightweight routing or state persistence as needed (URL params or Zustand) instead of server-side routing; auth gating now handled via AuthGate + bearer tokens

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
- Harden existing ingest flows (manual upload + Google Photos Picker)
- Implement versioned artifacts, dedupe gates, and canonical timestamps
- Implement image multi-context extraction + context-level Qdrant indexing
- Ship video/audio pipelines with chunked Gemini understanding, keyframes, and transcripts
- Ship episode merge + daily summaries (embedded + searchable)

### Tasks

**Week 3 (implemented): Ingestion core (artifacts → contexts → embeddings)**

Reference: `docs/minecontext/lifelog_ingestion_rag_design.md`

- [x] Manual uploads flow: `POST /storage/upload-url` → `PUT <signed url>` → `POST /upload/ingest` with optional `captured_at` + `client_tz_offset_minutes`.
- [x] Google Photos Picker sync: OAuth + session ingest (`integrations.google_photos.sync`) writes originals into object storage (RustFS/S3) and enqueues pipeline.
- [x] Schema migrations shipped:
  - `002_ingestion_core.sql`: `event_time_utc`, `content_hash`, `phash`, `derived_artifacts`, `processed_contexts`.
  - `003_dedup_canonical.sql`: `canonical_item_id` for dedupe linkage.
  - `004_ai_usage_events.sql`: token usage capture for dashboard spend stats.
- [x] Modular pipeline in `services/api/app/pipeline/` with versioned steps and `input_fingerprint` caching:
  - `fetch_blob`, `content_hash`, `metadata`, `media_metadata`, `exif`, `preview`, `phash`,
    `event_time`, `dedupe`, `geocode`, `caption`, `keyframes`, `media_chunk_understanding`,
    `media_summary`, `transcript_context`, `generic_context`, `contexts`, `embeddings`.
- [x] Canonical timestamp priority: EXIF/container → provider metadata → request `captured_at`
  → server time, with `event_time_source` + confidence stored. Naive EXIF uses the
  `client_tz_offset_minutes` from the upload request.
- [x] Dedupe gates: SHA256 `content_hash` + near-dup `pHash` with window/hamming settings;
  `PIPELINE_REPROCESS_DUPLICATES` controls whether expensive steps rerun.
- [x] Image pipeline v2:
  - HEIC/HEIF preview fallback for browser-friendly thumbnails.
  - Gemini VLM prompt `lifelog_image_analysis_v2` with full taxonomy + user-perspective phrasing.
  - Contexts stored in `processed_contexts` with `vector_text` for embeddings.
- [x] Qdrant indexing by **context_id** (payload includes `user_id`, `context_type`, `event_time_utc`,
  `source_item_ids`, `is_episode`). `/search` returns context hits for items + episodes + daily summaries.
- [x] Token usage tracking: Gemini calls log `usage_metadata` into `ai_usage_events`; `/dashboard/stats`
  returns `usage_this_week`, `usage_all_time`, and `usage_daily` series.
- [x] Backfill + cleanup:
  - Pipeline backfill task (`maintenance.backfill_pipeline`) for missing artifacts/embeddings.
  - Episodes-only backfill (`episodes.backfill`) for post-hoc episode creation.
  - Timeline delete endpoint removes storage + Qdrant + episode/daily summary updates.

**Week 4 (implemented): Video/audio foundations + episode merge**

- [x] Media guards: size/duration bounds (`media_max_bytes=1GB`, `video_max_duration_sec=300`,
  `audio_max_duration_sec=3600`, `video_understanding_max_duration_sec=1200`).
- [x] Keyframes + poster:
  - Scene detection with interval fallback (`video_keyframe_mode=scene`, `video_keyframe_interval_sec=5`).
  - Always capture a `t=0` poster and prefer it for `poster_url`.
  - Keyframes stored under `derived_artifacts` with `poster` + `frames` storage keys.
- [x] Chunked Gemini understanding for video/audio:
  - Chunks target 10MB (`media_chunk_target_bytes=10_000_000`), 60s for video, 300s for audio.
  - Audio chunks normalized to 16kHz mono; Gemini returns **both transcript + contexts per chunk**.
  - Transcript segments stored in `processed_content` + derived artifacts (size-capped with storage fallback).
- [x] Episode merge + semantic cleanup:
  - Item-level contexts cleaned via semantic merge (Jaccard threshold).
  - Episodes built via time gap + similarity; stored as `processed_contexts.is_episode=true`.
  - Episode summaries can be re-generated with Gemini and re-embedded.
- [x] Daily summaries:
  - Generated from episode contexts, stored as `processed_contexts` (`context_type=daily_summary`).
  - Embedded and searchable; shown in Timeline day view.
- [x] UI wiring:
  - Timeline shows episodes + drill-down items, posters for video, daily summary card, search, and upload-for-date.
  - Dashboard shows AI usage totals + usage daily chart.

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
- [ ] Document model decision matrix (cost/latency/throughput) for VLM vs OCR vs ASR choices
- [ ] Implement caching by `input_fingerprint` so identical artifacts are never recomputed
- [ ] Set monthly budget guardrails + alerts for model spend; failover to cheaper models when threshold hit
- [ ] Integrate OCR service (Tesseract or cloud OCR) when ready:
  ```python
  import pytesseract
  from PIL import Image
  
  def run_ocr(image_data: bytes) -> str:
      image = Image.open(io.BytesIO(image_data))
      text = pytesseract.image_to_string(image)
      return text.strip()
  ```
- [ ] Integrate embedding model (text):
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
- [ ] Ensure the collection exists and supports the chosen embedding dimension.
- [ ] Implement batch upsert for efficiency (contexts + chunks).
- [ ] Add error handling and retry logic; make Qdrant failures non-fatal (item can still be marked processed with a warning).

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
- ✅ Upload/Google Photos ingestion stays functional while the pipeline is upgraded
- ✅ Every ingested item has canonical `event_time_utc` + dedupe signals (`content_hash`, optional `pHash`)
- ✅ Images, videos, and audio produce 1..N `processed_contexts` (min `activity_context`) indexed in Qdrant
- ✅ Chunked Gemini understanding + transcripts for video/audio, with keyframes + poster artifacts
- ✅ Episodes + daily summaries are generated, embedded, searchable, and shown in Timeline

---

## Week 5-6: Memory Layer & Retrieval (Updated)

### Objectives
- Use `processed_contexts` + Qdrant for retrieval.
- Parse dates/entities and rerank results.
- Keep memory graph and scheduled clustering deferred.

### Implemented
- [x] Query parsing in `services/api/app/rag.py` (explicit dates, relative ranges, month/day parsing).
- [x] Optional query entity extraction via Gemini (`chat_entity_extraction_enabled`).
- [x] Context retrieval from Qdrant with boosts (episode, entity overlap) and time decay.
- [x] Daily summaries stored in `processed_contexts` and injected into chat context; `daily_summaries` table used for recent summary preprompting.
- [x] Chat history persistence in Postgres (`chat_sessions`, `chat_messages`) instead of mem0.

### Deferred (Post-MVP)
- [ ] Memory graph population + graph-based retrieval (tables exist).
- [ ] Scheduled daily event clustering job.
- [ ] mem0 integration (not used in current implementation).
- [ ] Notion integration (deferred until post-MVP).

---

## Week 7-8: Chat Interface & RAG (Implemented)

### Implemented
- [x] RAG chat endpoint (`POST /chat`) with query parsing, Qdrant retrieval, reranking, and citations.
- [x] Image chat endpoint (`POST /chat/image`) using VLM description + RAG.
- [x] Chat sessions + messages persisted in Postgres (`chat_sessions`, `chat_messages`).
- [x] Sources include thumbnails, timestamps, and snippets; UI surfaces relevant memories.
- [x] Chat attachments stored in `chat_attachments` with signed download URLs.
- [x] Downstream agents: Cartoon Day Summary and Day Insights Infographic.

### Remaining (if needed)
- [ ] Feedback UI and `/chat/feedback` wiring (backend table exists).
- [ ] Optional guardrails/timeout instrumentation in Prometheus.

---

## Week 9-10: Timeline, Dashboard & Polish

### Objectives
- Stabilize and polish the implemented Timeline + Dashboard
- Improve error handling, loading states, and UX resilience
- Stand up local observability for Celery + API + Qdrant
- Expand QA coverage for ingest -> retrieval -> chat

### Implemented (Current)
- [x] Timeline API: `/timeline`, `/timeline/items`, `/timeline/items/{id}`, `/timeline/episodes/{id}`, `PATCH /timeline/episodes/{id}`, `DELETE /timeline/items/{id}`
- [x] Timeline UI: day + all views, daily summary + episodes, item detail drawer, search, upload-for-date, manual "load more"
- [x] Dashboard API: `/dashboard/stats` with activity, recent items, AI usage, storage totals
- [x] Dashboard UI: stat cards, ingestion activity chart, AI usage chart, recent memories linking to timeline
- [x] Basic tests: `services/api/tests/test_timeline.py`, `services/api/tests/test_dashboard.py`, `apps/web/tests/ingest-flow.spec.ts`
- [x] Authentik OIDC auth (API JWKS validation + web AuthGate)
- [x] Global Error Boundary + event-driven toast notifications for API failures
- [x] Settings API + Settings UI (profile, language, timezone, timeline defaults, weekly recap toggle)
- [x] Weekly recap task + Celery beat schedule + manual trigger endpoint (`POST /settings/weekly-recap`)

### Remaining (Week 9-10 Focus)

**Monitoring & Instrumentation**
- [x] Enable Prometheus stack in `orchestration/docker-compose.dev.yml` (celery-exporter, Prometheus, Grafana) and `make observability`
- [ ] Update `orchestration/prometheus.yml` to scrape API `/metrics` (container or `host.docker.internal`)
- [ ] Add Grafana datasource provisioning + starter dashboards (ingest throughput, task latency, chat latency, model spend)
- [ ] Optional: run Flower for Celery task debugging

**Error Handling & Loading States**
- [x] Add a global Error Boundary + fallback UI
- [x] Add toast notifications for API failures
- [ ] Add consistent skeletons/spinners for Timeline/Dashboard/Chat fetches

**UI/UX Polish**
- [ ] Empty states + helper copy across tabs
- [ ] Tooltips and small help affordances
- [ ] Keyboard shortcuts for chat (Cmd+K focus, Cmd+Enter send)
- [ ] Optional: subtle motion and dark mode toggle

**Performance**
- [ ] Consider infinite scroll for timeline (replace manual "Load more")
- [ ] Add caching for dashboard stats (Valkey)
- [ ] Review indexes for timeline queries and add any missing ones
- [ ] Verify Qdrant filters for timeline/search use

**QA / Testing**
- [ ] Expand Playwright flows (timeline detail, episode edits, item delete)
- [ ] Add synthetic end-to-end job (upload -> processing -> retrieval -> chat)
- [ ] Run load test (k6/Locust) for chat latency budgets

### Deliverables
- [x] Timeline and Dashboard features shipped
- [ ] Monitoring dashboards and synthetic tests cover ingest -> chat pipeline
- [ ] UI is polished, accessible, and resilient with loading/error states
- [ ] Performance and caching verified for timeline + dashboard

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
          '/upload/ingest',
          json={
              "storage_key": "fixtures/test_image.jpg",
              "item_type": "photo",
              "content_type": "image/jpeg",
              "original_filename": "test_image.jpg",
          },
          headers={'Authorization': f'Bearer {auth_token}'}
      )
      assert response.status_code == 200
      assert 'item_id' in response.json()
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
          self.client.post("/chat", json={
              "message": "What did I do yesterday?",
              "session_id": "test-session"
          })
  ```

**Production Deployment**
- [ ] Provision production Postgres + object storage (RustFS/S3); Supabase optional
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
- [ ] Set up Valkey/Redis on Cloud Memorystore (or Upstash)
- [ ] Deploy the React + Vite SPA (Vercel/Netlify or Cloud Storage + CDN):
  ```bash
  vercel --prod
  ```
- [ ] Configure environment variables in production
- [ ] Set up custom domain and SSL

**Staging & Dev Experience (Deferred from Week 1-2)**
- [ ] Set up pnpm workspaces or Turborepo (only if/when shared JS packages are introduced)
- [ ] Initialize Git hooks with Husky (pre-commit linting)
- [ ] Create Qdrant Cloud instance (1GB dev) for shared dev/staging usage if local Qdrant isn't sufficient
- [ ] Provision staging environment (Postgres + RustFS/S3 + Qdrant) with seeded demo data for onboarding tests

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

### Summary (Post-MVP scope at a glance)
- Identity & personalization: people/voice enrollment, matching, review flow
- Ingestion expansion: desktop capture, Drive/Oura/Apple Photos, ESP32 device ingest
- Lifecycle + cost controls: storage retention, budget guardrails, monitoring
- Live data access (MCP): tool routing + cached live context
- Memory graph + analytics: graph search, clustering jobs, mem0-assisted memory
- Surprise and customization: “surprise me” insights + configurable ingestion pipeline
- Platform expansion: mobile apps, sharing, developer API

### Immediate Priorities (Week 13+)
1. Analyze pilot user feedback
2. Fix critical bugs and UX issues
3. Optimize processing speed and cost
4. Add most requested features

### Post-MVP Additions (Requested)
- Improve RAG quality with mem0-assisted memory, graph search, and scheduled clustering jobs.
- Daily vlog generation (storyboard + montage plan), with optional rendering later.
- Settings page for user controls (retention, AI preferences, agent toggles).
- “Surprise me” mode and customizable ingestion pipeline steps (per-user toggles/weights).

### Ingestion Expansion (Post-MVP)

**Photo Pipeline Enhancements (Post-MVP)**
- [ ] EXIF timezone fallback: infer timezone when OffsetTimeOriginal is missing (e.g., GPS/timezone database or user profile)
- [ ] EXIF/XMP sidecar support for GPS/time when metadata is stored outside the image file
- [ ] Preview/thumbnail generation for all photo formats (not just HEIF)
- [ ] Geocode VLM-derived location names when GPS is missing (normalize to lat/lng + address)

**People + Voice Identity (Post-MVP, opt-in)**

**Faces in photos/videos: enroll → embed → match → confirm**
- [ ] People setup flow: “Add Me” + optional family members; upload 3–10 photos or pick from library.
- [ ] Compute face embeddings per detected face; store per-person profile vectors (no model training).
- [ ] Ingestion: detect faces in photos + video keyframes, embed, match against profiles with a similarity threshold.
- [ ] Unknown/low-confidence faces grouped for review.
- [ ] Review UI: user assigns names; confirmed embeddings are added to the person profile (or centroid updated).

**Voices in audio/videos: diarize → embed → match → confirm**
- [ ] Voice setup flow: record 20–60s prompts for user + optional family voices.
- [ ] Store speaker embeddings per person.
- [ ] Ingestion: ASR → diarization → speaker embeddings; match against enrolled voices.
- [ ] Unknown speakers surfaced with short clips + transcript snippets for confirmation.
- [ ] Confirmed segments update the speaker profile vectors.

**Desktop Capture App (macOS first)**
- [ ] Build a lightweight menubar app that captures screenshots every 30s while the screen is active (idle detection + pause toggle)
- [ ] Buffer locally (ring buffer) and upload to a user-selected Google Drive folder (either via Drive sync folder or Drive API)
- [ ] Add privacy controls: pause/resume, exclude apps/windows (best-effort), and “delete local after upload”
- [ ] Treat uploaded screenshots as normal assets (same processing: OCR/caption/embeddings) with `provider=desktop_capture`

**Google Drive Connector (Cloud Sync Bridge)**
- [ ] Add `google_drive` as a `data_connections.provider` and implement OAuth + folder picker
- [ ] Backfill + incremental sync for a specific folder using the Drive Changes API (or folder query + modifiedTime cursor)
- [ ] Map Drive files into the existing pipeline by writing to object storage (RustFS/S3 or signed URLs) and then calling `/upload/ingest`
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
- Managed Supabase (optional): $25
- Qdrant Cloud: $0-50 (depending on data volume)
- Railway/Cloud Run: $20-50
- Vercel: $0 (hobby) or $20 (pro)
- OpenAI API: $50-200 (depending on usage)
- **Total: $120-345/month for MVP**

---

## Success Metrics Tracking

| Metric | Target | Measurement |
|--------|--------|-------------|
| User signups | 20 pilot users | Auth provider user count |
| Data sources connected | 80% of pilots connect both Google + Apple | `data_connections` table filtered by provider |
| Media assets processed | 10,000+ mixed items | `source_items` count + processing metrics |
| Chat queries | 100+ total | OpenTelemetry traces + API logs |
| Query accuracy | 80%+ relevant | In-app thumbs feedback tagged per conversation turn |
| Response time | <3s p95 | Prometheus histogram (`chat_latency_seconds`) |
| Daily summary usefulness | 70% thumbs-up | Daily summary feedback table |
| User retention | 40% D14 | Analytics |

---

*End of Development Plan*
