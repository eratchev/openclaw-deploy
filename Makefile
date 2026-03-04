PROJECT := $(notdir $(CURDIR))
DATA_VOLUME := $(PROJECT)_openclaw_data

.PHONY: up down logs logs-all restart status backup backup-remote update test kill-switch setup-approvals deploy-workspace

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

# Backup /data volume to Hetzner Object Storage (requires BACKUP_S3_* in .env)
backup-remote:
	sudo bash scripts/backup-cron.sh

# Pull latest image and restart
update:
	@echo "WARNING: Back up your data first: make backup"
	docker compose pull openclaw
	docker compose up -d --no-deps openclaw
	@echo "OpenClaw updated. Check logs: make logs"

# Run guardrail unit tests
test:
	pytest tests/ -v

# Configure exec approvals allowlist for calendar (run once after first deploy)
setup-approvals:
	@echo "Fixing exec-approvals socket path (macOS path breaks on Linux)..."
	docker compose exec openclaw python3 -c "\
import json; p='/home/node/.openclaw/exec-approvals.json'; \
d=json.load(open(p)); d['socket']['path']='/home/node/.openclaw/exec-approvals.sock'; \
open(p,'w').write(json.dumps(d,indent=2)); print('socket path fixed')"
	docker compose exec openclaw openclaw approvals allowlist add '/home/node/.openclaw/bin/gcal' --agent main --gateway
	docker compose exec openclaw openclaw approvals allowlist add 'gcal *' --agent main --gateway
	docker compose exec openclaw openclaw approvals allowlist add '*gcal *' --agent main --gateway
	docker compose exec openclaw openclaw approvals allowlist add 'date' --agent main --gateway
	docker compose exec openclaw openclaw approvals allowlist add 'date *' --agent main --gateway
	docker compose exec openclaw openclaw approvals allowlist add 'ai' --agent main --gateway
	docker compose exec openclaw openclaw approvals allowlist add 'ai *' --agent main --gateway
	docker compose exec openclaw openclaw config set tools.exec.safeBins '["gcal","date","ai"]'
	docker compose restart openclaw
	@echo "Exec approvals configured. Run 'make logs' to verify."

# Deploy workspace files (AGENTS.md, MEMORY.md, COMMANDS.md) to container
deploy-workspace:
	@for f in workspace/*.md; do \
		docker compose cp $$f openclaw:/home/node/.openclaw/workspace/$$(basename $$f); \
		echo "Deployed $$f"; \
	done

# Activate manual kill switch
kill-switch:
	@echo "Activating kill switch..."
	docker compose exec -T openclaw touch /home/node/.openclaw/GUARDRAIL_DISABLE
	@echo "Kill switch activated. OpenClaw will terminate within 5s."
	@echo "To resume: remove the file from the volume, then restart:"
	@echo "  docker run --rm -v $(DATA_VOLUME):/data busybox rm -f /data/GUARDRAIL_DISABLE"
	@echo "  make restart"
