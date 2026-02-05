# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OmniMemory/Lifelog MVP is a personal memory AI system with:
- **FastAPI + Celery backend** (Python 3.11+) handling uploads, processing, timeline, dashboard, search, and chat
- **React + Vite frontend** (Node.js 20+) with Dashboard, Timeline, Chat, and Ingest views
- **ESP32 firmware** (PlatformIO) for hardware device capture (photos + audio)
- **Infrastructure**: Postgres, Redis, Qdrant (vector DB), optional Supabase storage

## Common Commands

### OmniMemory CLI (recommended for local dev)
The CLI handles Docker Compose, API, Celery, and web app startup in one command.
```bash
# First time setup
cd apps/cli && npm install && npm run build && npm link
cd ../..

omni setup    # Interactive wizard (configures .env, storage, auth, integrations)
omni start    # Start all services (Docker + API + Celery + Web)
omni stop     # Stop all services
omni status   # Check service health
```

### Manual Infrastructure (alternative to CLI)
```bash
make dev-up          # Start Postgres, Redis, Qdrant, Flower, Prometheus, Grafana
make dev-down        # Stop all containers
make dev-logs        # Tail container logs
make dev-ps          # Show container status
make authentik-up    # Start local OIDC provider
make verify          # Run all tests (API + web e2e)
```

### Backend API (services/api/)
```bash
uv sync --extra dev                        # Install dependencies (including pytest)
uv run python -m app.db.migrator           # Run database migrations
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000  # Start API
uv run celery -A app.celery_app.celery_app worker --loglevel=info  # Start worker
uv run celery -A app.celery_app.celery_app beat --loglevel=info    # Start scheduler
uv run pytest tests                        # Run all tests
uv run pytest tests/test_dashboard.py      # Run single test file
uv run pytest tests/test_foo.py::test_bar  # Run single test function
uv run pytest --cov=app --cov-report=term-missing tests  # Run with coverage
```

### Frontend (apps/web/)
```bash
npm install          # Install dependencies
npm run dev          # Start dev server (localhost:5173)
npm run build        # Production build
npm run test:e2e     # Run Playwright tests
```

### ESP32 Firmware (apps/esp32/)
```bash
pio run -t upload    # Build and flash
pio device monitor   # Serial monitor
```

## Architecture

### Backend Structure (services/api/app/)
- `main.py` - FastAPI application entry point
- `celery_app.py` - Celery configuration and task registration
- `config.py` - Pydantic settings (env vars)
- `routes/` - API endpoints (upload, timeline, dashboard, search, chat, devices, settings, integrations)
- `tasks/` - Celery tasks (process_item, google_photos, recaps, episodes, maintenance, backfill)
- `pipeline/` - Media processing pipeline (steps, runner, media_utils)
- `ai/` - AI integrations (vlm, ocr, transcription, geocoding, image_gen, prompts)
- `db/` - Database models and migrations
- `storage.py` - Storage abstraction (S3/Supabase)
- `vectorstore.py` - Qdrant vector operations
- `rag.py` - RAG retrieval logic
- `auth.py` - OIDC JWT validation

### Data Flow
```
Upload → /storage/upload-url (presigned) → Object Storage → /upload/ingest
→ Postgres (source_items) → Celery (process_item) → AI processing
→ processed_content + embeddings → Qdrant
```

### Frontend Structure (apps/web/)
- `App.tsx` - Main app with routing
- `components/` - UI components (Dashboard, Timeline, Chat, Ingest, Settings)
- `services/` - API client and auth
- `contexts/` - React contexts (auth, settings)

### ESP32 Structure (apps/esp32/)
- `src/` - Main firmware code
- `include/board_pins.h` - Hardware pin definitions
- `include/config.h` - Firmware configuration
- Device writes to SD card, uploads via `/devices/*` endpoints

## Environment Configuration

- `.env` - All backend config (copy from `.env.example`). Used by Docker Compose and API/Celery.
- `apps/web/.env.local` - Frontend only (copy from `.env.local.example`). Required for Vite `VITE_` prefix.

Key settings:
- `STORAGE_PROVIDER=s3|supabase` - Upload storage backend
- `AUTH_ENABLED=true|false` - OIDC authentication toggle
- `POSTGRES_*`, `REDIS_URL`, `QDRANT_*` - Service connections

## Key Integrations

- **Google Photos**: OAuth sync via `/integrations/google-photos/*`
- **Gemini AI**: VLM for image understanding, chat responses
- **Qdrant**: Vector similarity search for RAG
- **Authentik**: Local OIDC provider for auth testing

## Debugging (Docker Commands)

See `docs/debug-best-practices.md` for detailed steps. Quick reference:

### Health & Status
```bash
curl -i "http://localhost:8000/health"        # API health
docker compose ps                              # Container status
```

### Database Queries (via Docker)
```bash
# Query source_items
docker exec -i lifelog-postgres psql -U lifelog -d lifelog \
  -c "SELECT id, captured_at, event_time_utc FROM source_items LIMIT 10;"

# Check user settings
docker exec -i lifelog-postgres psql -U lifelog -d lifelog \
  -c "SELECT user_id, settings FROM user_settings WHERE user_id='USER_ID';"
```

### Run Python Scripts in API Container
```bash
docker exec -i lifelog-api /app/.venv/bin/python -m app.scripts.fix_demo_event_times --provider demo --all
```

### Common Gotchas
- Always pass `tz_offset_minutes` to timeline/search/dashboard calls
- Demo uploads have no `data_connection`, so `provider=demo` filter may return zero items
- Check `event_time_utc` vs `captured_at` when items appear on wrong day
- Use read-only SQL first; only UPDATE/DELETE after identifying root cause
