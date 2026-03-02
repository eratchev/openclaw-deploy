#!/usr/bin/env bash
# OpenClaw VPS provisioning script
# Run once as root on a fresh Ubuntu LTS VPS.
# Idempotent — safe to run multiple times.
set -euo pipefail

echo "[provision] Starting VPS hardening..."

# ── System updates ────────────────────────────────────────────────────────────
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q \
  ufw fail2ban unattended-upgrades curl git python3 \
  apt-transport-https ca-certificates gnupg

# ── Unattended security upgrades ──────────────────────────────────────────────
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

# ── SSH hardening ─────────────────────────────────────────────────────────────
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl reload ssh

# ── UFW inbound rules ─────────────────────────────────────────────────────────
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 443/tcp comment "HTTPS (Caddy)"
ufw --force enable
echo "[provision] UFW inbound rules applied."

# ── Optional outbound allowlist (DISABLED by default) ────────────────────────
# Uncomment and customize to restrict outbound traffic after verifying
# that all required API endpoints are listed.
#
# ufw default deny outgoing
# ufw allow out 53/udp   comment "DNS"
# ufw allow out 123/udp  comment "NTP"
# ufw allow out to any port 443 comment "HTTPS outbound"
# # Add specific IPs for Telegram, Anthropic, OpenAI, WhatsApp as needed
# ufw reload
echo "[provision] Outbound egress: unrestricted (Phase 1). See docs/threat-model.md."

# ── Fail2ban ──────────────────────────────────────────────────────────────────
systemctl enable --now fail2ban
echo "[provision] Fail2ban enabled."

# ── Docker install ────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  echo "[provision] Docker installed."
else
  echo "[provision] Docker already installed."
fi

# ── /data volume permissions ─────────────────────────────────────────────────
# Ensure openclaw_data volume is owned by UID 1000 (node user).
# Run this after `docker compose up` has created the volume.
echo "[provision] To fix /data permissions after first compose up, run:"
echo "  docker run --rm -v openclaw-deploy_openclaw_data:/home/node/.openclaw busybox chown -R 1000:1000 /home/node/.openclaw"

echo "[provision] Done. Reboot recommended before starting services."
