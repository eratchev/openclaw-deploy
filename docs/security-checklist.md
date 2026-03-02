# Security Checklist

Run through this before going live.

## Secrets
- [ ] `.env` is NOT committed to git (`git status` shows nothing sensitive)
- [ ] `REDIS_PASSWORD` is set to a strong random value (`openssl rand -hex 32`)
- [ ] `DOMAIN` is set to your actual domain (not the placeholder)
- [ ] No cloud IAM credentials on the VPS

## Network
- [ ] VPS: only ports 22 and 443 open (`ufw status`)
- [ ] Redis not reachable from host (`nc -zv localhost 6379` should fail)
- [ ] Caddy is only service with exposed ports

## VPS Hardening
- [ ] SSH: password auth disabled (`grep PasswordAuthentication /etc/ssh/sshd_config`)
- [ ] SSH: root login disabled
- [ ] Fail2ban running (`systemctl status fail2ban`)
- [ ] Unattended security upgrades enabled

## Container Hardening
- [ ] OpenClaw running as UID 1000 (`docker compose exec openclaw id`)
- [ ] Capabilities dropped (`docker inspect <openclaw-container> | grep CapDrop`)
- [ ] Resource limits in effect — MEM LIMIT shows 2GiB, not 0B (`docker stats --no-stream`)
- [ ] `/data` owned by 1000:1000 (`docker compose exec openclaw ls -la /home/node/`)

## Guardrail
- [ ] Guardrail process running (`docker compose exec openclaw ps aux | grep guardrail`)
- [ ] Limits set appropriately in `.env` (review MAX_SESSION_SECONDS, MAX_TOOL_CALLS, etc.)

## Functionality
- [ ] Telegram webhook responding (send a test message to your bot)
- [ ] WhatsApp connected (check `openclaw status`)
- [ ] Logs flowing (`make logs` shows activity)
- [ ] Kill switch tested (`make kill-switch` triggers restart)

## Skills
- [ ] No third-party skills installed without review
- [ ] Dynamic remote skill installs disabled (default)
