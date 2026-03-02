# Architecture

## Diagram

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

## Network Isolation

| Service  | ingress network | internal network |
|----------|:--------------:|:----------------:|
| Caddy    | yes            | no               |
| OpenClaw | yes            | yes              |
| Redis    | no             | yes              |

This is enforced explicitly in `docker-compose.yml` — not just implied by convention.

## Service Roles

**Caddy** sits at the edge and is the only service that accepts inbound traffic from the internet. It terminates TLS using an automatically provisioned Let's Encrypt certificate and reverse-proxies HTTPS requests to OpenClaw. Caddy is on the `ingress` network only. It has no path to Redis and no knowledge of session state. Keeping Caddy at the edge means the TLS termination point has no access to any sensitive internal data.

**OpenClaw** runs the Gateway daemon that handles Telegram and WhatsApp messaging, invokes LLM APIs, and executes tools and skills. It sits on both networks because it must accept proxied requests from Caddy (via `ingress`) and read and write session state in Redis (via `internal`). It is the only service that spans both networks, which is intentional — it is the integration point, and it is the most constrained: non-root UID 1000, all Linux capabilities dropped, read-only root filesystem, resource limits, and no Docker socket access.

**Redis** stores session state for the OpenClaw Gateway. It runs on the `internal` network only, meaning it is completely unreachable from Caddy and from the internet. It requires a password (`REDIS_PASSWORD`) even on the internal network, providing a second layer of defense if the OpenClaw container is compromised and an attacker gains access to the internal network.

## Why Two Networks

The two-network design exists for one reason: Redis must never be reachable from the internet, directly or indirectly. A single shared network would give Caddy a route to Redis. By splitting traffic into an `ingress` network (internet-facing) and an `internal` network (service-to-service only), Docker's network isolation enforces the separation at the kernel level. Even if Caddy were fully compromised, it cannot reach Redis because there is no network path between them.
