# openclaw-deploy

> Hardened single-VPS deployment of [OpenClaw](https://github.com/openclaw/openclaw) with execution guardrails. Personal assistant + publishable open-source template.

## What This Is

One VPS. One Docker Compose. Telegram + WhatsApp through OpenClaw's Gateway, hardened container, log-driven execution guardrail, Redis session store. TLS via Caddy.

Out of the box you get:

- TLS termination via Caddy with automatic Let's Encrypt certificates
- OpenClaw Gateway running as a non-root user with all Linux capabilities dropped, read-only filesystem, and resource limits enforced
- Redis session store isolated to an internal Docker network — unreachable from the internet
- A Python execution guardrail that kills runaway LLM sessions before they burn tokens or abuse tools
- VPS hardening via `scripts/provision.sh` (UFW, SSH key-only auth, Fail2ban, unattended security upgrades)
- A `Makefile` with commands for bring-up, teardown, logs, backup, and upgrade

## What This Is NOT

- Not multi-tenant
- Not Kubernetes
- Not a managed SaaS
- Not hardened for enterprise (see threat model)

## Prerequisites

- A VPS (Hetzner CX22 or equivalent, ~$5-7/month)
- Ubuntu 24.04 LTS
- A domain name pointing to the VPS
- OpenClaw already set up locally (you need to onboard channels before deploying)
- Docker + Docker Compose (installed by provision.sh)
- **SSH public key loaded on the VPS** — `scripts/provision.sh` disables password authentication. Run `ssh-copy-id user@<your-vps>` before provisioning or you will be locked out.

## Quickstart

1. Clone this repo on your VPS
2. Run `sudo bash scripts/provision.sh`
3. Copy `.env.example` to `.env` and fill in your values
4. `make up`
5. Fix `/data` permissions (the OpenClaw container runs as UID 1000):
   ```bash
   docker run --rm -v "$(basename $(pwd))_openclaw_data":/data busybox chown -R 1000:1000 /data
   ```
   Run this from inside the repo directory. The volume name is `<repo-dir-name>_openclaw_data`.
6. Run through `docs/security-checklist.md`

## Security Model

This deployment shifts OpenClaw's execution risk to containment. OpenClaw can execute arbitrary code via skills and tools — the hardening around it prevents that from compromising the host.

See [docs/threat-model.md](docs/threat-model.md) for the full threat model including known gaps. Phase 1 ships with outbound egress unrestricted — read it before deploying.

## Execution Guardrails

A Python watchdog runs inside the container and kills OpenClaw if sessions exceed configurable limits (tool calls, LLM calls, session time, idle timeout). Because OpenClaw has no per-session abort API, a violation kills all sessions — the container restarts automatically.

See [docs/execution-guardrails.md](docs/execution-guardrails.md) for limits and tuning.

## Upgrading

`make backup && make update`

See [docs/upgrade-path.md](docs/upgrade-path.md).

## Pre-launch Checklist

See [docs/security-checklist.md](docs/security-checklist.md). Run through it before going live.
