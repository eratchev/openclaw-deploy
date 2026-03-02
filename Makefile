PROJECT := $(notdir $(CURDIR))
DATA_VOLUME := $(PROJECT)_openclaw_data

.PHONY: up down logs logs-all restart status backup update test kill-switch

# Start all services
up:
	docker compose up -d

# Stop all services
down:
	docker compose down

# Follow OpenClaw logs
logs:
	docker compose logs -f openclaw

# Follow all logs
logs-all:
	docker compose logs -f

# Restart OpenClaw only
restart:
	docker compose restart openclaw

# Show container resource usage
status:
	docker stats --no-stream

# Backup /data volume to ./backups/
backup:
	mkdir -p backups
	docker run --rm \
		-v $(DATA_VOLUME):/source:ro \
		-v $(PWD)/backups:/backup \
		busybox tar czf /backup/openclaw-data-$(shell date +%Y%m%d-%H%M%S).tar.gz -C /source .
	@echo "Backup saved to ./backups/"

# Pull latest image and restart
update:
	@echo "WARNING: Back up your data first: make backup"
	docker compose pull openclaw
	docker compose up -d --no-deps openclaw
	@echo "OpenClaw updated. Check logs: make logs"

# Run guardrail unit tests
test:
	pytest tests/ -v

# Activate manual kill switch
kill-switch:
	@echo "Activating kill switch..."
	docker compose exec -T openclaw touch /home/node/.openclaw/GUARDRAIL_DISABLE
	@echo "Kill switch activated. OpenClaw will terminate within 5s."
	@echo "To resume: remove the file from the volume, then restart:"
	@echo "  docker run --rm -v $(DATA_VOLUME):/data busybox rm -f /data/GUARDRAIL_DISABLE"
	@echo "  make restart"
