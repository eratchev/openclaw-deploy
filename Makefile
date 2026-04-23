PROJECT := $(notdir $(CURDIR))
DATA_VOLUME := $(PROJECT)_openclaw_data

# Load HOST from .deploy file written by 'make deploy'
-include .deploy

.PHONY: up up-calendar up-voice up-mail down logs logs-all restart status backup backup-remote update test kill-switch setup-approvals setup-heartbeat setup-model setup-egress setup-inbound setup-gcal setup-gmail setup-skills deploy-workspace deploy deploy-clis push doctor pair-whatsapp

# Start all services (caddy, openclaw, redis, voice-proxy).
up:
	docker compose up -d

# Start all services + Google Calendar proxy (rebuilds calendar-proxy image)
up-calendar:
	docker compose --profile calendar up -d --build calendar-proxy
	@echo "Calendar proxy rebuilt and started."

# Force-rebuild voice-proxy (e.g. after code changes to services/voice-proxy)
up-voice:
	docker compose up -d --build voice-proxy
	@echo "Voice proxy rebuilt and started."

# Start all services + Gmail proxy (rebuilds mail-proxy image)
up-mail:
	docker compose --profile mail up -d --build mail-proxy
	@echo "Gmail proxy rebuilt and started."

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
	pip install -q -r requirements-dev.txt -r services/calendar-proxy/requirements.txt -r services/voice-proxy/requirements.txt -r services/mail-proxy/requirements.txt
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

# Configure morning briefing cron (run once on existing deployment, or after reset)
# Usage: make setup-heartbeat HOST=user@x.x.x.x HEARTBEAT_TO=<telegram-chat-id>
setup-heartbeat:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	ssh "$(HOST)" "cd ~/openclaw-deploy && \
	  sudo docker compose exec -T openclaw openclaw cron add \
	    --name 'Morning briefing' \
	    --cron '0 9 * * *' \
	    --tz 'America/Los_Angeles' \
	    --session isolated \
	    --announce \
	    --model 'anthropic/claude-haiku-4-5-20251001' \
	    --timeout-seconds 480 \
	    --channel telegram \
	    $(if $(HEARTBEAT_TO),--to '$(HEARTBEAT_TO)',) \
	    --message 'Run the morning briefing: check today'"'"'s full calendar schedule for gcal accounts personal, jobs, and work. Check unread emails from overnight for gmail accounts personal, jobs, and work (use gmail list --limit 5 per account). Compose a concise summary — events today with times, any email action items — and send it to Evgueni via Telegram.' || true && \
	  echo 'Morning briefing cron configured.'"

# Switch interactive chat primary model to gpt-4o-mini (run once on existing deployment)
# Fixes gpt-5.1-codex always failing and falling back to Anthropic Haiku for all traffic.
setup-model:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@bash scripts/setup-model.sh "$(HOST)"

# Apply inbound firewall rules on VPS (run once after deploy, or to re-apply)
setup-inbound:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@scp scripts/inbound.sh "$(HOST):/tmp/inbound.sh"
	@ssh "$(HOST)" "sudo bash /tmp/inbound.sh"

# Apply container egress allowlist on VPS (run once after deploy, or to re-apply)
setup-egress:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@scp scripts/egress.sh "$(HOST):/tmp/egress.sh"
	@ssh "$(HOST)" "sudo bash /tmp/egress.sh"

# Deploy workspace files to container (local) or remote VPS (if HOST set)
deploy-workspace:
	@if [ -n "$(HOST)" ]; then \
		scp workspace/*.md "$(HOST):/tmp/" && \
		for f in workspace/*.md; do \
			fname=$$(basename $$f); \
			ssh "$(HOST)" "cd ~/openclaw-deploy && sudo docker compose cp /tmp/$$fname openclaw:/home/node/.openclaw/workspace/$$fname && rm -f /tmp/$$fname"; \
			echo "Deployed $$fname"; \
		done; \
	else \
		for f in workspace/*.md; do \
			docker compose cp $$f openclaw:/home/node/.openclaw/workspace/$$(basename $$f); \
			echo "Deployed $$f"; \
		done; \
	fi

# Activate manual kill switch
kill-switch:
	@echo "Activating kill switch..."
	docker compose exec -T openclaw touch /home/node/.openclaw/GUARDRAIL_DISABLE
	@echo "Kill switch activated. OpenClaw will terminate within 5s."
	@echo "To resume: remove the file from the volume, then restart:"
	@echo "  docker run --rm -v $(DATA_VOLUME):/data busybox rm -f /data/GUARDRAIL_DISABLE"
	@echo "  make restart"

# Push updated CLI binaries (gmail, contacts, gcal) into the openclaw container.
# No OAuth re-run needed. Skips binaries that haven't been set up yet.
# Usage: make deploy-clis  (requires HOST from .deploy or HOST=user@x.x.x.x)
deploy-clis:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@bash scripts/deploy-clis.sh "$(HOST)"

# Push latest code to VPS and rebuild affected services (non-interactive).
# Run this after every git push. Requires HOST from .deploy or HOST=user@x.x.x.x
push:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@bash scripts/push.sh "$(HOST)"

# Deploy to a remote VPS from this local machine
# Usage: make deploy HOST=user@x.x.x.x  (saved to .deploy for future targets)
deploy:
	@[ -n "$(HOST)" ] || (echo "Usage: make deploy HOST=user@x.x.x.x" && exit 1)
	@echo "HOST=$(HOST)" > .deploy
	@bash scripts/setup.sh "$(HOST)"

# Run health checks on the VPS
doctor:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@ssh "$(HOST)" "cd ~/openclaw-deploy && bash scripts/doctor.sh"

# Usage: make setup-gcal CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]
# No ACCOUNT= : migrate existing single-account setup to 'personal'
# ACCOUNT=jobs : set up a new 'jobs' account via OAuth
setup-gcal:
	@[ -n "$(CLIENT_SECRET)" ] || (echo "Usage: make setup-gcal CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]" && exit 1)
	@bash scripts/setup-gcal.sh "$(HOST)" "$(CLIENT_SECRET)" "$(ACCOUNT)"

# Usage: make setup-gmail CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]
# No ACCOUNT= : migrate existing single-account setup to 'personal'
# ACCOUNT=jobs : set up a new 'jobs' account via OAuth
setup-gmail:
	@[ -n "$(CLIENT_SECRET)" ] || (echo "Usage: make setup-gmail CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]" && exit 1)
	@bash scripts/setup-gmail.sh "$(HOST)" "$(CLIENT_SECRET)" "$(ACCOUNT)"

# Install OpenClaw skill CLIs into the container (run once after deploy)
# Opt-in per skill. Usage: make setup-skills [SKILLS="github session-logs spotify-player"]
# Default: all supported skills. Supported: github  session-logs  spotify-player
# Not on Linux: summarize (macOS brew-only)
setup-skills:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@bash scripts/setup-skills.sh "$(HOST)" $(SKILLS)

# Pair WhatsApp interactively (renders QR code in your terminal)
pair-whatsapp:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	ssh -t "$(HOST)" "sudo docker compose -f ~/openclaw-deploy/docker-compose.yml exec -it openclaw openclaw channels login --channel whatsapp"
