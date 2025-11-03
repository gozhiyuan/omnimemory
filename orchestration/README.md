Local Orchestration (Docker Compose)

Services included:
- Redis (broker for Celery)
- Qdrant (vector DB)
- Flower (Celery monitoring)
- Celery Exporter (Prometheus metrics for Celery)
- Prometheus (metrics)
- Grafana (dashboards)

Quick start
- Prereq: Docker + Docker Compose
- Copy `.env.dev.example` to `.env.dev` at repo root and fill values as needed (Grafana creds optional)
- Start stack: `make dev-up`
- Stop stack: `make dev-down`
- Tail logs: `make dev-logs`
- Open Grafana: http://localhost:3001 (default admin/admin or values from env)
- Open Prometheus: http://localhost:9090
- Open Flower: http://localhost:5555
- Qdrant UI/API: http://localhost:6333

Notes
- API/worker/beat services are stubbed (commented) until the backend exists at `services/api`.
- Prometheus scrapes Celery exporter and Qdrant. When your API exposes `/metrics`, uncomment the `api` job in `prometheus.yml`.
- Volumes: Qdrant data persisted in the `qdrant_data` volume.

Production orchestration
- Use managed services per the MVP plan (Supabase, Qdrant Cloud, Redis/Upstash, Vercel/Cloud Run). Terraform/IaC can be added later; start by configuring via provider dashboards.

