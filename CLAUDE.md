# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OmniMemory/Lifelog MVP is a personal memory AI system with:
- **FastAPI + Celery backend** (Python 3.11+) handling uploads, processing, timeline, dashboard, search, and chat
- **React + Vite frontend** (Node.js 20+) with Dashboard, Timeline, Chat, and Ingest views
- **ESP32 firmware** (PlatformIO) for hardware device capture (photos + audio)
- **Infrastructure**: Postgres, Redis, Qdrant (vector DB), optional Supabase storage

## Common Commands

### OmniMemory CLI (recommended)
```bash
cd apps/cli && npm install && npm run build  # First time only
node apps/cli/dist/index.js setup            # Interactive setup wizard
node apps/cli/dist/index.js start            # Start all services
node apps/cli/dist/index.js stop             # Stop all services
node apps/cli/dist/index.js status           # Check service health

# Or install globally: cd apps/cli && npm link
omni setup                                   # Interactive setup wizard
omni start                                   # Start all services (foreground)
omni status                                  # Check service health
```

### Infrastructure (from repo root)
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
