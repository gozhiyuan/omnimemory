<div align="center">
  <img src="docs/images/omnilogo.png" alt="OmniMemory" width="1000">
</div>

<div align="center">
  <h1>OmniMemory</h1>
  <p>Open-source personal memory system that turns daily capture into searchable, summarized memories.</p>
  <p>Every day we take many photos. OmniMemory converts them into memories automatically. With future hardware capture,
  it will save even more of your life, and AI will help organize, summarize, and retrieve everything. The more you save,
  the more it understands you.</p>
</div>

<p align="center">
  <a href="#what-is-omnimemory">What is OmniMemory</a> |
  <a href="#project-updates">Project Updates</a> |
  <a href="#key-features">Key Features</a> |
  <a href="#use-cases">Use Cases</a> |
  <a href="#hardware-capture-and-integrations">Hardware Capture</a> |
  <a href="#quick-start">Quick Start</a> |
  <a href="#manual-setup-advanced">Manual Setup</a> |
  <a href="#repository-structure">Repository Structure</a> |
  <a href="#roadmap">Roadmap</a> |
  <a href="#license">License</a>
</p>

---

## What is OmniMemory

OmniMemory is a personal memory AI system (lifelog) with a FastAPI + Celery backend and a React + Vite frontend.
It ingests photos, audio, and video; builds a timeline and search index; and lets you chat with your memories.
It is designed to be local-first, production-ready, and extensible for hardware capture.

At its core, OmniMemory is simple: capture life, convert it into structured memories, and make it easy to find what
matters later. As you save more moments, the system learns your preferences and context, making recall and summaries
better over time. 
I believe there will be more embodied intelligence hardware like AI glasses, pendants, and robotics with cameras to
capture life. OmniMemory is built to organize those daily captures.

## Project Updates

- 2025-02: OpenClaw integration released for agent workflows and memory sync. OmniMemory extends OpenClaw by syncing daily image, video, and audio memories.

## Key Features

- Local-first and self-hosted by default.
- Full-stack with containers and a production-ready backend with authentication (OIDC), background workers, and observability.
- Google Photos Picker API integration.
- Multimodal understanding for images, audio, and video with Gemini models.
- Vector search with Qdrant plus RAG retrieval.
- Daily summaries, timelines, and chat over your memory graph.
- OpenClaw agent integration for memory sync.
- Easy setup with a guided CLI.

## Tech Stack

- Backend: FastAPI, Celery, Python 3.11+
- Storage: Postgres, Redis, Qdrant, S3 (RustFS) or Supabase
- Frontend: React 19, Vite
- Hardware: ESP32 firmware (PlatformIO)
- Auth: Authentik OIDC (optional)

## Use Cases

- Manual ingest from the app. (GIF placeholder)
- Search and context recall: "When did I last visit that cafe?" and get photos and context instantly. (GIF placeholder)
- Daily summaries: auto-generate a concise recap of your day, with optional voice edits.
- Weekly recaps: see patterns and themes across a week.
- Generate summary images. (GIF placeholder)
- Agent workflows: let OpenClaw query and summarize your memory stream. (GIF placeholder)
- Advanced settings. (GIF placeholder)

## Hardware Capture and Integrations (Ongoing)

We are building hardware capture for always-on memories: an ESP32 camera + audio device (apps/esp32) that
continuously takes photos and audio (for example every 30s) for ingestion.

You can also manually upload photos, videos, and audio from any device. There are devices that can automatically take
photos/videos on a schedule. Many sports cameras have better hardware. I am building the ESP32 cameras because I
already have several sports cams and do not want to buy more hardware, but if you already own one, you can upload
those photos manually. Below is a comparison based on research; please verify specs and prices.

| Device / Project | Category | 30s Interval Photo | Video Interval / Burst | Data Access / Ingest Path | Key Specs (from this report) | Estimated Price (USD) |
|---|---|---|---|---|---|---|
| Looki L1 | AI lifelogger (agent) | Low (defaults to video clips) | Story Mode clips (15-30s) | App-tethered, encrypted storage; export via phone app | 12MP Sony sensor; 375mAh battery; 32GB eMMC | ~199 |
| Meta Ray-Ban Smart Glasses | Consumer smart glasses | No native interval photo | 30s/60s/3min manual video bursts | Phone-bridged SDK; 720p stream via Bluetooth | Camera access via SDK; battery/thermal limits for continuous use (not stated) | ~299-379 |
| Insta360 GO 3S | Wearable action cam | Yes (interval photo) | Timelapse / interval modes | USB Mass Storage via Action Pod; DCIM files | Core 310mAh + Pod 1270mAh; outputs INSP/JPG | ~399 |
| DJI Osmo Action 4 | Prosumer action cam | Yes (timelapse photo, 0.5-40s) | Timelapse video or photo | MicroSD or USB Mass Storage | 1/1.3-inch sensor; 1770mAh battery; JPG/DNG | ~299 |
| Omi OpenGlass Dev Kit | Open-source wearable | Yes (programmable) | Yes (programmable) | Full firmware control; direct POST to endpoint | Seeed XIAO ESP32-S3 Sense; battery pack claim 6x150mAh + 1x250mAh | ~299 |
| XIAO ESP32-S3 Sense (DIY) | MCU build | Yes (custom firmware) | Yes (custom firmware) | Full firmware control; direct POST to endpoint | Dual-core 240MHz; 8MB PSRAM; OV2640; deep sleep ~10uA; Wi-Fi ~260mA | ~20-30 |




---

## Quick Start

The easiest way to get started is using the OmniMemory CLI.

### Prerequisites

- Docker Desktop (or Docker Engine with Compose plugin)
- Node.js 20+

### Setup

```bash
# Install the CLI
cd apps/cli && npm install && npm run build && npm link
cd ../..

# Run interactive setup (configures Gemini API key, storage, auth options)
omni setup

# Start all services
omni start
```

The setup wizard will prompt you for:
- Gemini API Key (required) for AI features
- Storage Provider: Local (RustFS) or Cloud (Supabase)
- Google Photos sync (optional)
- Google Cloud APIs (optional) for Vision OCR and Maps
- Authentication (optional) via Authentik OIDC
- OpenClaw integration (optional)

### Integrate with OpenClaw
OmniMemory can integrate with OpenClaw. TODO: update integration guidance on setting the JSON env for authentication.
`omni start` also copies the skill files to OpenClaw.

### What Gets Started

| Auth Disabled | Auth Enabled |
|---|---|
| Postgres, Redis, Qdrant | All core services |
| RustFS (S3 storage) | + Authentik (OIDC provider) |
| API, Celery worker | + User creation prompt |
| Monitoring (Prometheus, Grafana, Flower) | |
| Web app (localhost:3000) | |

### Other Commands

```bash
omni status              # Check service health
omni stop                # Stop all services
omni stop --volumes      # Stop and remove all data (fresh start)
```

### Clean Reinstall

To start completely fresh:

```bash
omni stop --volumes
rm -f .env apps/web/.env.local
docker volume prune -f
omni setup
omni start
```

---

## Manual Setup (Advanced)

If you prefer manual control or need to customize the setup, follow the instructions below.

### Tooling Prerequisites

- Docker Desktop (or Docker Engine) with Compose plugin
- Python 3.11+ with uv (pip install uv or brew install uv)
- Node.js 20+
- Optional: make (Makefile provided)

### Local Environment Setup (Manual)

1. Copy `.env.example` to `.env` at the repo root. This single file configures both Docker Compose and API/Celery.
   - Set `AUTHENTIK_SECRET_KEY` and a valid `AUTHENTIK_IMAGE_TAG` if you plan to use local OIDC.
   - If you want uploads to work from the web UI or seed script, keep `STORAGE_PROVIDER=s3` with the RustFS defaults or switch to Supabase.
   - If the frontend cannot reach the API due to CORS, set `CORS_ALLOW_ORIGINS=http://localhost:3000` (comma-separated for multiple origins).
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
3. (Optional) Start Authentik for local OIDC:
   ```bash
   make authentik-up
   ```
   Authentik UI: `http://localhost:9002/`
4. Install API dependencies and run migrations:
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
5. (Frontend) Once the API + Celery worker are running:
   ```bash
   cd apps/web
   npm install
   npm run dev
   ```
   Ensure `apps/web/.env.local` contains `VITE_API_URL=http://localhost:8000`. The UI runs on `http://localhost:3000` by default.
6. (Optional) Exercise the ingest pipeline end-to-end with a local file:
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
   - Create an OAuth2/OpenID Provider.
   - Use a client ID like `omnimemory`.
   - Add redirect URI: `http://localhost:3000/`.
   - Save the provider.
   - Create an Application that uses the provider and set its slug to `omnimemory`.
3. Configure API auth in `.env`:
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

### Settings and Weekly Recap

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

- `docker-compose.yml` at repo root is the source of truth for local infra; `make dev-up` uses it.
- API/worker/beat services run locally for now and are not wired into Compose.
- `make authentik-up` starts the local OIDC provider. `make observability` starts Prometheus/Grafana/celery-exporter.
- Prometheus scrape config lives in `orchestration/prometheus.yml` (uncomment API job when `/metrics` is active in Docker).

---

## Repository Structure

- `services/api/` - FastAPI service + Celery processing pipeline
- `apps/web/` - React 19 + Vite SPA
- `apps/cli/` - OmniMemory CLI for guided setup
- `apps/esp32/` - ESP32 firmware for hardware capture
- `orchestration/` - Docker Compose stack for infra services
- `docs/` - Design docs, hardware notes, and integrations like OpenClaw skills
- `legacy/` - Archived planning docs and research notes

## Roadmap

- ESP32 camera integrations
- Remember people feature
- Improved long-range memory linking and clustering
- Smarter daily and weekly summaries
- More OpenClaw skills and agent workflows

## License

This project is under the MIT License.

## Thanks

Inspired by and grateful for the open-source community, including
- [Dayflow](https://github.com/JerryZLiu/Dayflow)
- [MineContext](https://github.com/volcengine/MineContext)
- [OmiGlass](https://github.com/BasedHardware/omi/tree/main/omiGlass)
- [OpenAIglasses_for_Navigation](https://github.com/AI-FanGe/OpenAIglasses_for_Navigation)
- [OpenClaw](https://github.com/openclaw/openclaw)

Also thanks to Codex 5.2 and Claude Code Opus 4.5 for building the initial app, and Gemini 3 for frontend design.
