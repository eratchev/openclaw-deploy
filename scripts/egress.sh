#!/usr/bin/env bash
# Apply container egress allowlist via DOCKER-USER iptables chain.
#
# Restricts Docker container outbound traffic to:
#   - HTTPS (tcp/443)    — all required APIs (Anthropic, Telegram, OpenAI, etc.)
#   - DNS   (udp+tcp/53) — name resolution
#   - NTP   (udp/123)    — time sync
#   - ESTABLISHED/RELATED — return traffic for existing connections
#
# Does NOT affect container-to-container traffic on Docker bridge networks.
# Safe to run multiple times — idempotent.
#
# Usage: sudo bash scripts/egress.sh
set -euo pipefail

echo "[egress] Applying container egress allowlist..."

# ── Detect external interface ──────────────────────────────────────────────────
EXTERNAL_IF=$(ip -4 route show default | awk 'NR==1{print $5}')
if [ -z "$EXTERNAL_IF" ]; then
    echo "[egress] ERROR: Cannot detect external interface (no default route?)."
    exit 1
fi
echo "[egress] External interface: $EXTERNAL_IF"

# ── Require Docker to be running (DOCKER-USER chain must exist) ───────────────
if ! iptables -L DOCKER-USER -n &>/dev/null; then
    echo "[egress] ERROR: DOCKER-USER chain not found — is Docker running?"
    echo "[egress] Start Docker first: sudo systemctl start docker"
    exit 1
fi

# ── Install iptables-persistent if missing ────────────────────────────────────
if ! dpkg -l iptables-persistent 2>/dev/null | grep -q '^ii'; then
    echo "[egress] Installing iptables-persistent..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q iptables-persistent
fi

# ── Create/flush OPENCLAW_EGRESS chain (idempotent) ───────────────────────────
iptables -N OPENCLAW_EGRESS 2>/dev/null || true
iptables -F OPENCLAW_EGRESS

# ── Egress allowlist rules ────────────────────────────────────────────────────
# Return traffic for connections already established (must come first)
iptables -A OPENCLAW_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# DNS (UDP + TCP for large responses)
iptables -A OPENCLAW_EGRESS -p udp --dport 53 -j ACCEPT
iptables -A OPENCLAW_EGRESS -p tcp --dport 53 -j ACCEPT

# NTP (time sync)
iptables -A OPENCLAW_EGRESS -p udp --dport 123 -j ACCEPT

# HTTPS — covers all required external APIs
iptables -A OPENCLAW_EGRESS -p tcp --dport 443 -j ACCEPT

# Drop everything else (cleartext HTTP, raw TCP/UDP to arbitrary hosts, etc.)
iptables -A OPENCLAW_EGRESS -j DROP

# ── Hook into DOCKER-USER ─────────────────────────────────────────────────────
# -o "$EXTERNAL_IF" scopes the policy to internet-bound traffic only.
# Container-to-container traffic (openclaw→redis, etc.) is unaffected.
if ! iptables -C DOCKER-USER -o "$EXTERNAL_IF" -j OPENCLAW_EGRESS 2>/dev/null; then
    iptables -I DOCKER-USER 1 -o "$EXTERNAL_IF" -j OPENCLAW_EGRESS
    echo "[egress] Hooked OPENCLAW_EGRESS into DOCKER-USER (out=$EXTERNAL_IF)"
else
    echo "[egress] DOCKER-USER already has OPENCLAW_EGRESS hook"
fi

# ── Persist rules (survives reboot) ──────────────────────────────────────────
netfilter-persistent save
echo "[egress] Rules saved to /etc/iptables/rules.v4"

echo "[egress] Done. Container egress restricted to HTTPS(443), DNS(53), NTP(123)."
