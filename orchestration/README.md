# Orchestration

This folder contains supporting config files for the Docker Compose stack.

## Files

- `prometheus.yml` - Prometheus scrape configuration mounted by `docker-compose.yml`
- `setup-authentik-oauth.sh` - Helper script to configure Authentik OAuth (invoked by `omni start`)

## How these files are used

- `prometheus.yml` is mounted into the Prometheus container via `docker-compose.yml` at `/etc/prometheus/prometheus.yml`.
- `setup-authentik-oauth.sh` is called by the CLI (`apps/cli/src/commands/start.ts`) after bringing up Authentik, or can be run manually.

## Usage

The main `docker-compose.yml` is at repo root. Run with:

```bash
make dev-up      # Start all infrastructure
make dev-down    # Stop all containers
make dev-logs    # Tail logs
```

## Services

- Postgres 15 (app database)
- Redis (Celery broker/cache)
- Qdrant (vector DB)
- RustFS (S3-compatible storage)
- Authentik (OIDC provider)
- Flower (Celery monitoring)
- Prometheus + Grafana (observability)

## URLs

| Service | URL |
|---------|-----|
| Postgres | localhost:5432 |
| Redis | localhost:6379 |
| Qdrant | http://localhost:6333 |
| RustFS (S3) | http://localhost:9000 |
| Authentik | http://localhost:9002 |
| Flower | http://localhost:5555 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 |
