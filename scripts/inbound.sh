#!/usr/bin/env bash
# Apply inbound firewall rules via iptables INPUT chain.
#
# Allows:
#   - Loopback
#   - ESTABLISHED/RELATED return traffic
#   - SSH  (tcp/22)
#   - HTTP (tcp/80)  — Let's Encrypt ACME challenge
#   - HTTPS (tcp/443)
# Drops everything else.
#
# Safe to run multiple times — idempotent.
# Requires iptables-persistent (installed by egress.sh).
#
# Usage: sudo bash scripts/inbound.sh
set -euo pipefail

echo "[inbound] Applying inbound firewall rules..."

# ── Apply INPUT rules (idempotent — flush and rebuild) ────────────────────────
iptables -F INPUT
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -p tcp --dport 22  -j ACCEPT
iptables -A INPUT -p tcp --dport 80  -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables -P INPUT DROP

# ── Persist rules (survives reboot) ──────────────────────────────────────────
# Skip if iptables-persistent is not installed yet (provision.sh calls us before
# egress.sh, which installs iptables-persistent; egress.sh saves all rules at the end).
if command -v netfilter-persistent &>/dev/null; then
    netfilter-persistent save
    echo "[inbound] Rules saved to /etc/iptables/rules.v4"
else
    echo "[inbound] iptables-persistent not yet installed — rules will be persisted by egress.sh"
fi

echo "[inbound] Done. Inbound: SSH(22), HTTP(80), HTTPS(443) open — all else blocked."
