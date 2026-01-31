# Orchestration

This folder contains supporting config files for the Docker Compose stack.

## Files

- `prometheus.yml` - Prometheus scrape configuration

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
