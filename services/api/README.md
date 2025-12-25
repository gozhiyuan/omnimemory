# Backend API (FastAPI)

This service handles synchronous requests (uploads, storage signing, timeline/dashboard summaries, search) and enqueues Celery tasks to process items. Authentication is not wired yet; endpoints default to the test user ID unless you provide one explicitly.

## Prerequisites

1. From the repo root, start the infrastructure containers and confirm they are healthy:

   ```bash
   make dev-up    # launches postgres/redis/qdrant/flower/prom/grafana
   make dev-ps    # optional: shows container status
   ```

   You can inspect the DB with
   `docker compose -f orchestration/docker-compose.dev.yml exec postgres psql -U lifelog -d lifelog`
   or watch Flower at http://localhost:5555 to ensure Celery can reach Redis.

2. In `services/api/`, create the virtual environment and install dependencies:

   ```bash
   uv venv
   uv sync
   ```

   _Tip:_ if your shell blocks uv’s default cache (permission errors under `~/.cache/uv`), set
   `UV_CACHE_DIR=../.uv-cache` before running `uv sync`.

3. Apply the SQL migrations once per database to create the schema:

   ```bash
   uv run python -m app.db.migrator
   ```

   The runner records applied versions in `schema_migrations`, so re-running it after future SQL
   changes is safe. Verify the tables via `\dt` inside the Postgres container if needed.

   To apply the same schema to Supabase, point the migrator at the Supabase Postgres host
   using environment variables and run the same command. You do not need to create tables
   manually; the migrations create them.

   ```bash
   POSTGRES_HOST=db.<project-ref>.supabase.co \
   POSTGRES_PORT=5432 \
   POSTGRES_DB=postgres \
   POSTGRES_USER=postgres \
   POSTGRES_PASSWORD=<db-password> \
   uv run python -m app.db.migrator
   ```

   Environment variables set in your shell override values from `.env.dev`/`.env`. The Supabase
   database password is available in the Supabase dashboard under Project Settings → Database.
   If your network only resolves the pooler hostname (common on some IPv4-only Wi‑Fi/DNS setups),
   use the connection pooling host/port and the pooler username from Supabase:

   ```bash
   POSTGRES_HOST=aws-0-<region>.pooler.supabase.com \
   POSTGRES_PORT=6543 \
   POSTGRES_DB=postgres \
   POSTGRES_USER=postgres.<project-ref> \
   POSTGRES_PASSWORD=<db-password> \
   uv run python -m app.db.migrator
   ```

4. Configure storage for uploads:
   - `STORAGE_PROVIDER=supabase` plus `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are required for `/storage/upload-url` and the web upload flow.
   - If you do not set these, you can still call `/upload/ingest` directly (no presigned uploads), but the UI upload flow will fail with a 501.

Subsequent commands can be executed through `uv run <command>` which automatically reuses the virtual
environment.

## Key scripts & modules

### FastAPI application (`app/main.py`)

Run the HTTP API locally with auto-reload once `make dev-up`, `uv sync`, and the migrations have
completed:

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Celery worker & beat (`app/celery_app.py`, `app/tasks/`)

Two processes are required: a worker that executes tasks such as `process_item`, and an optional
beat scheduler that triggers recurring jobs defined in `configure_celery()`.

```bash
# Terminal 1 – execute background jobs (process_item, maintenance.cleanup, health.ping, ...)
uv run celery -A app.celery_app.celery_app worker --loglevel=info

# Terminal 2 – emit scheduled tasks like the hourly lifecycle cleanup
uv run celery -A app.celery_app.celery_app beat --loglevel=info
```

### Database migration runner (`app/db/migrator.py`)

Already covered in the prerequisites section, but worth highlighting: migrations live under
`services/api/migrations/` as raw SQL files. Whenever those change, rerun:

```bash
uv run python -m app.db.migrator
```

You can confirm the schema exists by opening `psql` via Docker and running `\dt` or checking
`SELECT * FROM schema_migrations;`.

### Vector store helper (`app/vectorstore.py`)

The Qdrant wrapper exposes `ensure_collection()` for bootstrapping the collection schema and
`upsert_embeddings()`/`search_embeddings()` for CRUD operations. Call `ensure_collection()` during
initialisation (e.g. worker startup) to guarantee the collection exists:

```bash
uv run python -c "from app.vectorstore import ensure_collection; ensure_collection()"
```

### Storage abstraction (`app/storage.py`)

The `get_storage_provider()` factory returns the Supabase storage implementation when
`STORAGE_PROVIDER=supabase` and `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are configured. The
default in-memory provider does not issue presigned URLs, so `/storage/upload-url` will respond
with 501 unless Supabase is enabled.

### Processing pipeline (`app/tasks/process_item.py`)

`process_item` downloads an uploaded asset, extracts placeholder metadata/caption/ocr/transcription,
stores derived records in Postgres, and seeds Qdrant. Enqueue a processing job by calling the
`/upload/ingest` endpoint or directly queuing the Celery task:

```bash
uv run python -c "from app.tasks.process_item import process_item; process_item.delay({'item_id': '...', 'storage_key': '...'})"
```

### Seed ingest flow (`scripts/seed_ingest_flow.py`)

This script exercises presigned uploads + ingest + processing and then verifies counts in Postgres:

```bash
uv run python scripts/seed_ingest_flow.py ./fixtures/sample.jpg \
  --api-url http://localhost:8000 \
  --postgres-dsn postgresql://lifelog:lifelog@localhost:5432/lifelog
```

Use `--direct-upload` to upload directly to Supabase without relying on `/storage/upload-url`.

## HTTP endpoints (current)

- `GET /health`, `GET /health/db`, `GET /health/celery`
- `POST /storage/upload-url`, `POST /storage/download-url`
- `POST /upload/ingest`
- `GET /timeline`
- `GET /dashboard/stats`
- `GET /search?q=...`

## Data flow (upload → processing → search)

```text
Client
  │
  ├─ POST /storage/upload-url  ───────────────▶ API (presigned URL)
  │
  ├─ PUT <signed URL> ───────────────────────▶ Supabase Storage (object upload)
  │
  └─ POST /upload/ingest (storage_key) ──────▶ API
                               │
                               ├─ writes users/source_items in Postgres
                               └─ enqueues Celery task (Redis broker)
                                          │
                                          ▼
                               Celery worker (process_item)
                               │
                               ├─ fetch object from storage
                               ├─ write processed_content + update source_items
                               └─ upsert embedding to Qdrant
```

## Tests

Run the FastAPI integration tests after the services in `orchestration/docker-compose.dev.yml` are
up and the database has been migrated:

```bash
uv run pytest services/api/tests
```

If you are running offline, ensure the required wheels are already cached locally or install them
ahead of time.
