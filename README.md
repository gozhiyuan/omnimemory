## OmniMemory / Lifelog MVP

This repository houses the Lifelog AI MVP: a FastAPI + Celery backend (Postgres, Redis, Qdrant), optional Supabase storage for uploads, and a React + Vite frontend with Dashboard, Timeline, Chat, and Ingest views.

### Project Structure (current focus)

- `services/api/` – FastAPI service + Celery processing pipeline (upload/ingest, timeline, dashboard, search, seed script).
- `apps/web/` – React 19 + Vite SPA (manual upload, timeline, dashboard; chat UI uses mock memory context).
- `orchestration/` – Docker Compose stack (Postgres, Redis, Qdrant, Prometheus, Grafana, Flower).
- `lifelog-mvp-prd.md` – Product requirements.
- `lifelog-mvp-dev-plan.md` – Development roadmap.
- `docs/minecontext/lifelog_ingestion_rag_design.md` – Detailed ingestion + RAG design (draft).

### Tooling Prerequisites

- Docker Desktop (or Docker Engine) with Compose plugin.
- Python 3.11+
  - [uv](https://github.com/astral-sh/uv) for dependency management (`pip install uv` or via homebrew: `brew install uv`).
- Node.js 20+
- Optional: `just` or `make` (Makefile provided).

### Local Environment Setup

1. Copy `.env.dev.example` → `.env.dev` at the repo root and adjust values. The API reads this root file automatically; no need to duplicate per service.
   - If you want uploads to work from the web UI or seed script, set `STORAGE_PROVIDER=supabase` and provide `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`.
   - If the frontend cannot reach the API due to CORS, add `CORS_ALLOW_ORIGINS=http://localhost:5173` (comma-separated for multiple origins).
2. Start supporting services:
   ```bash
   make dev-up
   ```
   Services exposed:
   - Postgres: `localhost:5432`
   - Redis: `localhost:6379`
   - Qdrant: `http://localhost:6333`
   - Flower: `http://localhost:5555`
   - Prometheus: `http://localhost:9090`
   - Grafana: `http://localhost:3001`
3. Install API dependencies and run migrations:
   ```bash
   cd services/api
   uv sync
   uv run python -m app.db.migrator
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   In another terminal, start the Celery worker (and optionally beat) so uploads and sync jobs process:
   ```bash
   uv run celery -A app.celery_app.celery_app worker --loglevel=info
   # uv run celery -A app.celery_app.celery_app beat --loglevel=info
   ```
4. (Frontend) Once the API + Celery worker are running:
   ```bash
   cd apps/web
   npm install
   npm run dev
   ```
   Ensure `.env.local` contains `VITE_API_URL=http://localhost:8000` and your `GEMINI_API_KEY`. The UI runs on `http://localhost:5173` by default.

5. (Optional) Exercise the ingest pipeline end-to-end with a local file:
   ```bash
   cd services/api
   uv run python scripts/seed_ingest_flow.py ./fixtures/sample.jpg \
     --api-url http://localhost:8000 \
     --postgres-dsn postgresql://lifelog:lifelog@localhost:5432/lifelog
   ```
   Use `--direct-upload` if you want the script to upload directly to Supabase without presigned URLs.

### Docker Compose Notes

- `orchestration/docker-compose.dev.yml` is source of truth for local infra; `make dev-up` uses it.
- API/worker/beat services run locally for now and are not wired into Compose.
- Prometheus scrape config lives in `orchestration/prometheus.yml` (uncomment API job when `/metrics` is active in Docker).

### Next Steps

Follow `lifelog-mvp-dev-plan.md` for implementation milestones: connect OAuth data sources, add the backend chat endpoint, and continue hardening the processing pipeline and UI.
