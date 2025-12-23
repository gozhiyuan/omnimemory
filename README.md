## OmniMemory / Lifelog MVP

This repository houses the Lifelog AI MVP: a FastAPI + Celery backend, Qdrant for vector search, Supabase for auth/storage, and a React + Vite frontend that exposes the Dashboard, Timeline, Chat, and Ingest tabs.

### Project Structure (current focus)

- `services/api/` – FastAPI service (draft skeleton) + Celery tasks.
- `apps/web/` – React 19 + Vite SPA client (current MVP UI).
- `orchestration/` – Docker Compose stack (Postgres, Redis, Qdrant, Prometheus, Grafana, Flower).
- `lifelog-mvp-prd.md` – Product requirements.
- `lifelog-mvp-dev-plan.md` – Development roadmap.

### Tooling Prerequisites

- Docker Desktop (or Docker Engine) with Compose plugin.
- Python 3.11+
  - [uv](https://github.com/astral-sh/uv) for dependency management (`pip install uv` or via homebrew: `brew install uv`).
- Node.js 20+
- Optional: `just` or `make` (Makefile provided).

### Local Environment Setup

1. Copy `.env.dev.example` → `.env.dev` at the repo root and adjust values (Supabase keys optional initially). The API reads this root file automatically; no need to duplicate per service.
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

### Docker Compose Notes

- `orchestration/docker-compose.dev.yml` is source of truth for local infra; `make dev-up` uses it.
- API/worker/beat services are scaffolded in code but not yet wired into Compose; add them once the FastAPI service is containerised.
- Prometheus scrape config lives in `orchestration/prometheus.yml` (uncomment API job when `/metrics` is active in Docker).

### Next Steps

Follow `lifelog-mvp-dev-plan.md` for implementation milestones: wire the API to Supabase/Postgres, flesh out processing pipelines, then integrate the React + Vite web app.
