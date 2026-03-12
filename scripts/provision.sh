#!/usr/bin/env bash
# OpenClaw VPS provisioning script
# Run once as root on a fresh Ubuntu LTS VPS.
# WARNING: UFW rules are reset on each run — do not re-run on a live system with custom rules.
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

# ── SSH key guard ─────────────────────────────────────────────────────────────
if ! find /root/.ssh /home -name authorized_keys -size +0c 2>/dev/null | grep -q .; then
  echo "[provision] ERROR: No authorized_keys found. Add your SSH public key before running."
  echo "[provision] Example: ssh-copy-id user@<this-vps>"
  exit 1
fi

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
ufw allow 443/tcp comment "HTTPS Caddy"
ufw allow 80/tcp comment "HTTP ACME challenge"
ufw --force enable
echo "[provision] UFW inbound rules applied."

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

# ── Container egress allowlist ────────────────────────────────────────────────
# Restricts Docker container outbound to HTTPS(443), DNS(53), NTP(123).
# Requires Docker daemon to be running (DOCKER-USER chain must exist).
# Explicitly start daemon — the installer may not have started it yet.
systemctl start docker
bash "$(dirname "$0")/egress.sh"

# ── /data volume permissions ─────────────────────────────────────────────────
# Ensure openclaw_data volume is owned by UID 1000 (node user).
# Run this after `docker compose up` has created the volume.
echo "[provision] To fix /data permissions after first compose up, run:"
echo "  (cd /path/to/openclaw-deploy && docker run --rm -v \$(basename \$(pwd))_openclaw_data:/data busybox chown -R 1000:1000 /data)"

echo "[provision] Done. Reboot recommended before starting services."
