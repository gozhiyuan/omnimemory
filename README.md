## OmniMemory / Lifelog MVP

This repository houses the Lifelog AI MVP: a FastAPI + Celery backend, Qdrant for vector search, Supabase for auth/storage, and a Next.js frontend.

### Project Structure (current focus)

- `services/api/` – FastAPI service (draft skeleton) + Celery tasks.
- `apps/web/` – Next.js App Router client (to be implemented).
- `orchestration/` – Docker Compose stack (Postgres, Redis, Qdrant, Prometheus, Grafana, Flower).
- `lifelog-mvp-prd.md` – Product requirements.
- `lifelog-mvp-dev-plan.md` – Development roadmap.

### Tooling Prerequisites

- Docker Desktop (or Docker Engine) with Compose plugin.
- Python 3.11+
  - [uv](https://github.com/astral-sh/uv) for dependency management (`pip install uv` or via homebrew: `brew install uv`).
- Node.js 20+
  - [pnpm](https://pnpm.io/installation).
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
3. Install API dependencies:
   ```bash
   cd services/api
   uv sync
   uv run uvicorn app.main:app --reload
   ```
4. (Frontend) Once ready:
   ```bash
   cd apps/web
   pnpm install
   pnpm dev
   ```

### Docker Compose Notes

- `orchestration/docker-compose.dev.yml` is source of truth for local infra; `make dev-up` uses it.
- API/worker/beat services are scaffolded in code but not yet wired into Compose; add them once the FastAPI service is containerised.
- Prometheus scrape config lives in `orchestration/prometheus.yml` (uncomment API job when `/metrics` is active in Docker).

### Next Steps

Follow `lifelog-mvp-dev-plan.md` for implementation milestones: wire the API to Supabase/Postgres, flesh out processing pipelines, then connect the Next.js app.
