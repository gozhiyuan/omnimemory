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

1. Copy `.env.example` → `.env` at the repo root for Docker Compose (Postgres/RustFS/Qdrant/Authentik).
   - Set `AUTHENTIK_SECRET_KEY` and a valid `AUTHENTIK_IMAGE_TAG` if you plan to use local OIDC.
2. Copy `.env.dev.example` → `.env.dev` at the repo root for the FastAPI/Celery runtime.
   - If you want uploads to work from the web UI or seed script, keep `STORAGE_PROVIDER=s3` with the RustFS defaults or switch to Supabase.
   - If the frontend cannot reach the API due to CORS, set `CORS_ALLOW_ORIGINS=http://localhost:3000` (comma-separated for multiple origins).
3. Start supporting services:
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
4. (Optional) Start Authentik for local OIDC:
   ```bash
   make authentik-up
   ```
   Authentik UI: `http://localhost:9002/`
5. Install API dependencies and run migrations:
   ```bash
   cd services/api
   uv sync
   uv run python -m app.db.migrator
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   In another terminal, start the Celery worker (and optionally beat) so uploads and sync jobs process:
   ```bash
   uv run celery -A app.celery_app.celery_app worker --loglevel=info
   uv run celery -A app.celery_app.celery_app beat --loglevel=info
   ```
6. (Frontend) Once the API + Celery worker are running:
   ```bash
   cd apps/web
   npm install
   npm run dev
   ```
   Ensure `apps/web/.env.local` contains `VITE_API_URL=http://localhost:8000`. The UI runs on `http://localhost:3000` by default.

7. (Optional) Exercise the ingest pipeline end-to-end with a local file:
   ```bash
   cd services/api
   uv run python scripts/seed_ingest_flow.py ./fixtures/sample.jpg \
     --api-url http://localhost:8000 \
     --postgres-dsn postgresql://lifelog:lifelog@localhost:5432/lifelog
   ```
   Use `--direct-upload` if you want the script to upload directly to Supabase without presigned URLs.

### Authentication (Authentik OIDC)

The API validates bearer tokens against OIDC JWKS when auth is enabled. For local dev, Authentik runs inside Docker.

1. Start Authentik:
   ```bash
   make authentik-up
   ```
2. In Authentik:
   - Create an **OAuth2/OpenID Provider**.
   - Use a client ID like `omnimemory`.
   - Add redirect URI: `http://localhost:3000/`.
   - Save the provider.
   - Create an **Application** that uses the provider and set its slug to `omnimemory`.
3. Configure API auth in `.env.dev`:
   ```
   AUTH_ENABLED=true
   OIDC_ISSUER_URL=http://localhost:9002/application/o/omnimemory/
   OIDC_JWKS_URL=http://localhost:9002/application/o/omnimemory/jwks/
   OIDC_AUDIENCE=omnimemory
   ```
4. Configure the web app in `apps/web/.env.local`:
   ```
   VITE_API_URL=http://localhost:8000
   VITE_OIDC_ISSUER_URL=http://localhost:9002/application/o/omnimemory/
   VITE_OIDC_CLIENT_ID=omnimemory
   VITE_OIDC_REDIRECT_URI=http://localhost:3000/
   VITE_OIDC_SCOPES=openid profile email offline_access
   VITE_OIDC_AUTH_URL=http://localhost:9002/application/o/authorize/
   VITE_OIDC_TOKEN_URL=http://localhost:9002/application/o/token/
   VITE_OIDC_LOGOUT_URL=http://localhost:9002/application/o/omnimemory/end-session/
   VITE_OIDC_POST_LOGOUT_REDIRECT_URI=http://localhost:3000/
   ```

### Settings + Weekly Recap

- Settings are stored per-user via `GET/PUT /settings`.
- Weekly recap generation can be triggered via `POST /settings/weekly-recap` and is scheduled weekly via Celery beat.

Manual trigger example:
```bash
curl -X POST http://localhost:8000/settings/weekly-recap \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"force": true}'
```

### Migrating Local Data After Enabling Auth

Before auth, data is stored under the default dev user. After enabling OIDC, a new user is created.
Use the migration script to move old data to your OIDC user:
```bash
cd services/api
uv run python -m app.scripts.migrate_user_id --new-email you@example.com --dry-run
uv run python -m app.scripts.migrate_user_id --new-email you@example.com --delete-old-user
```

### Docker Compose Notes

- `orchestration/docker-compose.dev.yml` is source of truth for local infra; `make dev-up` uses it.
- API/worker/beat services run locally for now and are not wired into Compose.
- `make authentik-up` starts the local OIDC provider. `make observability` starts Prometheus/Grafana/celery-exporter.
- Prometheus scrape config lives in `orchestration/prometheus.yml` (uncomment API job when `/metrics` is active in Docker).

### Next Steps

Follow `lifelog-mvp-dev-plan.md` for implementation milestones: connect OAuth data sources, add the backend chat endpoint, and continue hardening the processing pipeline and UI.
