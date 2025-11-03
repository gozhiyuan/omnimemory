COMPOSE := orchestration/docker-compose.dev.yml

.PHONY: dev-up dev-down dev-restart dev-logs dev-ps dev-pull dev-clean

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

