# Backend API (FastAPI)

This service handles all synchronous requests, including uploads, authentication, and queries.

## Prerequisites

All commands below assume you are running from `services/api/` with the environment variables in
`.env` populated for Postgres, Redis, Qdrant, and Supabase. Bootstrap the local virtual environment
and install dependencies with:

```bash
uv venv           # creates .venv using the settings from pyproject.toml
uv sync           # installs runtime + dev dependencies into .venv
```

Subsequent commands can be executed through `uv run <command>` which automatically reuses the
virtual environment.

## Key scripts & modules

### FastAPI application (`app/main.py`)

Run the HTTP API locally with auto-reload:

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

Migrations are expressed as SQL files in `migrations/`. Apply them against your local Postgres
instance with:

```bash
uv run python -m app.db.migrator
```

The runner stores applied versions in the `schema_migrations` table so it can be rerun safely.

### Vector store helper (`app/vectorstore.py`)

The Qdrant wrapper exposes `ensure_collection()` for bootstrapping the collection schema and
`upsert_embeddings()`/`search_embeddings()` for CRUD operations. Call `ensure_collection()` during
initialisation (e.g. worker startup) to guarantee the collection exists:

```bash
uv run python -c "from app.vectorstore import ensure_collection; ensure_collection()"
```

### Storage abstraction (`app/storage.py`)

The `get_storage_client()` factory will return the Supabase storage implementation when
`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are configured. For local development without Supabase,
set `STORAGE_PROVIDER=local` to use the filesystem-backed stub.

### Processing pipeline (`app/tasks/process_item.py`)

`process_item` downloads an uploaded asset, extracts metadata, stores the derived record in
Postgres, and seeds Qdrant. Enqueue a processing job by calling the `/ingest` endpoint or directly
queuing the Celery task:

```bash
uv run python -c "from app.tasks.process_item import process_item; process_item.delay({'item_id': '...', 'storage_key': '...'})"
```

## Tests

Run the FastAPI integration tests after the services in `orchestration/docker-compose.dev.yml` are
up (Postgres, Redis, Qdrant). The first invocation installs the dev dependencies thanks to `uv sync`
above:

```bash
uv run pytest services/api/tests
```

If you are running offline, ensure the required wheels are already cached locally or install them
ahead of time.
