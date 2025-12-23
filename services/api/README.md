# Backend API (FastAPI)

This service handles all synchronous requests, including uploads, authentication, and queries.

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
up and the database has been migrated:

```bash
uv run pytest services/api/tests
```

If you are running offline, ensure the required wheels are already cached locally or install them
ahead of time.
