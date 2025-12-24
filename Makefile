COMPOSE := orchestration/docker-compose.dev.yml
NODE_PREFIX := $(shell brew --prefix node@20 2>/dev/null || brew --prefix node 2>/dev/null || true)
NODE_BIN := $(if $(NODE_PREFIX),$(NODE_PREFIX)/bin,)
NODE_PATH := $(if $(NODE_BIN),$(NODE_BIN):,)

.PHONY: dev-up dev-down dev-restart dev-logs dev-ps dev-pull dev-clean verify

dev-up:
	docker compose -f $(COMPOSE) up -d --remove-orphans

dev-down:
	docker compose -f $(COMPOSE) down

dev-restart: dev-down dev-up

dev-logs:
	docker compose -f $(COMPOSE) logs -f --tail=200

dev-ps:
	docker compose -f $(COMPOSE) ps

dev-pull:
	docker compose -f $(COMPOSE) pull

dev-clean:
	docker compose -f $(COMPOSE) down -v

verify:
	cd services/api && (UV_CACHE_DIR=.uv-cache uv run --extra dev pytest || .venv/bin/python -m pytest)
	cd apps/web && PATH="$(NODE_PATH)$$PATH" npm run test:e2e
