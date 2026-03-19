#!/bin/bash
# Install OpenClaw skill CLIs into the openclaw container.
# Downloads static Linux amd64 binaries from GitHub releases onto the VPS,
# then docker-cp them into the container. Safe to re-run (idempotent).
#
# Usage:
#   bash scripts/setup-skills.sh user@host
#   bash scripts/setup-skills.sh user@host github session-logs
#   bash scripts/setup-skills.sh user@host github session-logs spotify-player
#
# Supported skills: github  session-logs  spotify-player
# NOT supported:    summarize (macOS-only brew package, no Linux binary available)
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@host [skill1 skill2 ...]"
    echo "Skills: github session-logs spotify-player"
    exit 1
fi

# Remaining args are skill names; default to all Linux-available skills.
# Must check $# BEFORE shift to distinguish "no skills given" from "user gave empty string".
shift
if [ "$#" -eq 0 ]; then
    SKILLS=(github session-logs spotify-player)
else
    SKILLS=("$@")
fi

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step()  { echo -e "\n${BOLD}▶ $1${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
skip()  { echo -e "  ${YELLOW}–${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; exit 1; }

COMPOSE="sudo docker compose -f ~/openclaw-deploy/docker-compose.yml"
BIN_DIR="/home/node/.openclaw/bin"

# ── Helpers ────────────────────────────────────────────────────────────────────

# Fetch latest GitHub release download URL matching a pattern (run on VPS)
latest_gh_asset() {
    local repo="$1" pattern="$2"
    ssh "$HOST" "curl -fsSL 'https://api.github.com/repos/${repo}/releases/latest' \
        | python3 -c \"import sys,json; assets=json.load(sys.stdin)['assets']; \
          match=[a['browser_download_url'] for a in assets if '${pattern}' in a['name']]; \
          print(match[0] if match else '')\""
}

# Ensure the bin directory exists inside the container
ensure_bin_dir() {
    ssh "$HOST" "$COMPOSE exec -T openclaw mkdir -p \"$BIN_DIR\""
}

# Install a single binary into the container from a VPS path
install_bin() {
    local name="$1" vps_path="$2"
    ssh "$HOST" "$COMPOSE cp '$vps_path' openclaw:$BIN_DIR/$name \
        && $COMPOSE exec -T openclaw chmod +x $BIN_DIR/$name"
    ok "$name installed at $BIN_DIR/$name"
}

# Register a binary on the exec approvals allowlist (|| true: idempotent)
register_approvals() {
    local name="$1"
    ssh "$HOST" "$COMPOSE exec -T openclaw openclaw approvals allowlist add '$BIN_DIR/$name' --agent main --gateway 2>/dev/null || true
        $COMPOSE exec -T openclaw openclaw approvals allowlist add '$name' --agent main --gateway 2>/dev/null || true
        $COMPOSE exec -T openclaw openclaw approvals allowlist add '$name *' --agent main --gateway 2>/dev/null || true"
    ok "$name registered on approvals allowlist"
}

# Download tarball on VPS into an isolated temp dir, extract a named binary, install it
# Usage: install_tarball_bin DISPLAY_NAME BINARY_NAME TARBALL_URL
install_tarball_bin() {
    local display="$1" name="$2" url="$3"
    ssh "$HOST" "
        TMPD=\$(mktemp -d)
        trap 'rm -rf \"\$TMPD\"' EXIT
        curl -fsSL '$url' | tar -xz -C \"\$TMPD\"
        BIN=\$(find \"\$TMPD\" -name '$name' -type f | head -1)
        [ -n \"\$BIN\" ] || { echo 'Binary $name not found in tarball'; exit 1; }
        [ \"\$BIN\" = \"\$TMPD/$name\" ] || cp \"\$BIN\" \"\$TMPD/$name\"
        chmod +x \"\$TMPD/$name\"
        $COMPOSE cp \"\$TMPD/$name\" openclaw:$BIN_DIR/$name
        $COMPOSE exec -T openclaw chmod +x $BIN_DIR/$name
    "
    ok "$display installed at $BIN_DIR/$name"
}

# ── Skill installers ───────────────────────────────────────────────────────────

install_github() {
    step "Installing github skill → gh (GitHub CLI)"
    local url
    url=$(latest_gh_asset "cli/cli" "linux_amd64.tar.gz")
    [ -n "$url" ] || fail "Could not find gh release for linux_amd64"
    install_tarball_bin "gh" "gh" "$url"
    register_approvals "gh"
}

install_session_logs() {
    step "Installing session-logs skill → jq"
    local jq_url="https://github.com/jqlang/jq/releases/latest/download/jq-linux-amd64"
    ssh "$HOST" "
        TMPD=\$(mktemp -d)
        trap 'rm -rf \"\$TMPD\"' EXIT
        curl -fsSL '$jq_url' -o \"\$TMPD/jq\"
        chmod +x \"\$TMPD/jq\"
        $COMPOSE cp \"\$TMPD/jq\" openclaw:$BIN_DIR/jq
        $COMPOSE exec -T openclaw chmod +x $BIN_DIR/jq
    "
    ok "jq installed at $BIN_DIR/jq"
    register_approvals "jq"

    step "Installing session-logs skill → rg (ripgrep)"
    local rg_url
    rg_url=$(latest_gh_asset "BurntSushi/ripgrep" "x86_64-unknown-linux-musl.tar.gz")
    [ -n "$rg_url" ] || fail "Could not find rg release for x86_64-unknown-linux-musl"
    install_tarball_bin "rg" "rg" "$rg_url"
    register_approvals "rg"
}

install_spotify_player() {
    step "Installing spotify-player skill → spotify_player.bin"
    # Pinned to v0.17.2: last release compiled on Ubuntu 22.04 (glibc 2.35).
    # Releases v0.18.0+ were compiled on Ubuntu 24.04 (requires GLIBC_2.38/2.39) and will
    # fail inside the Node.js container which ships with an older glibc.
    local SPOTIFY_VERSION="v0.17.2"
    local url="https://github.com/aome510/spotify-player/releases/download/${SPOTIFY_VERSION}/spotify_player-x86_64-unknown-linux-gnu.tar.gz"
    # Install directly as spotify_player.bin via docker cp to avoid rename inside the container
    # (docker cp creates files owned by root; mv as node user would fail).
    ssh "$HOST" "
        TMPD=\$(mktemp -d)
        trap 'rm -rf \"\$TMPD\"' EXIT
        curl -fsSL '$url' | tar -xz -C \"\$TMPD\"
        BIN=\$(find \"\$TMPD\" -name 'spotify_player' -type f | head -1)
        [ -n \"\$BIN\" ] || { echo 'ERROR: spotify_player binary not found in tarball'; exit 1; }
        chmod +x \"\$BIN\"
        $COMPOSE cp \"\$BIN\" openclaw:$BIN_DIR/spotify_player.bin
        $COMPOSE exec -T openclaw chmod +x $BIN_DIR/spotify_player.bin
    "
    ok "spotify_player.bin installed at $BIN_DIR/spotify_player.bin"

    step "spotify-player: installing shared libraries (libdbus-1.so.3, libasound.so.2)"
    # The binary (v0.17.2, compiled on Ubuntu 22.04 / glibc 2.35) needs libs with matching
    # glibc compatibility. The VPS is Ubuntu 24.04 so its apt-get fetches Noble packages that
    # require GLIBC_2.38 — which the Node.js container doesn't have. Use a ubuntu:22.04
    # container to download Jammy packages (glibc ≤ 2.35) instead.
    ssh "$HOST" "
        set -euo pipefail
        TMPD=\$(mktemp -d)
        trap 'rm -rf \"\$TMPD\"' EXIT
        $COMPOSE exec -T openclaw mkdir -p /home/node/.openclaw/lib

        sudo docker run --rm --network host ubuntu:22.04 sh -c '
            set -e
            cd /tmp
            apt-get update -qq >&2
            apt-get download libdbus-1-3 libasound2 >&2
            tar cf - *.deb
        ' | tar xf - -C \"\$TMPD\"

        for deb in \"\$TMPD\"/*.deb; do
            dpkg-deb -x \"\$deb\" \"\$TMPD/pkg_\$(basename \"\$deb\" .deb)\"
        done

        LIBDBUS=\$(find \"\$TMPD\" -name 'libdbus-1.so.3*' -type f | head -1)
        [ -n \"\$LIBDBUS\" ] || { echo 'ERROR: libdbus-1.so.3 not found in ubuntu:22.04 package'; exit 1; }
        $COMPOSE cp \"\$LIBDBUS\" openclaw:/home/node/.openclaw/lib/libdbus-1.so.3

        LIBASOUND=\$(find \"\$TMPD\" -name 'libasound.so.2*' -type f | head -1)
        [ -n \"\$LIBASOUND\" ] || { echo 'ERROR: libasound.so.2 not found in ubuntu:22.04 package'; exit 1; }
        $COMPOSE cp \"\$LIBASOUND\" openclaw:/home/node/.openclaw/lib/libasound.so.2
    "
    ok "Shared libraries installed (libdbus-1.so.3, libasound.so.2) from ubuntu:22.04"

    step "spotify-player: creating wrapper (LD_LIBRARY_PATH + --config-folder)"
    # Sets LD_LIBRARY_PATH to pick up the libs above, and bakes in --config-folder.
    # Encoded as base64 locally to avoid shell quoting issues over ssh.
    local wrapper_b64
    wrapper_b64=$(printf '%s\n' \
        '#!/bin/sh' \
        'export LD_LIBRARY_PATH="/home/node/.openclaw/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"' \
        'exec /home/node/.openclaw/bin/spotify_player.bin --config-folder /home/node/.openclaw/spotify-player "$@"' \
        | base64 | tr -d '\n')
    ssh "$HOST" "
        echo \"$wrapper_b64\" | base64 -d > /tmp/openclaw_spotify_wrapper
        chmod +x /tmp/openclaw_spotify_wrapper
        $COMPOSE cp /tmp/openclaw_spotify_wrapper openclaw:$BIN_DIR/spotify_player
        $COMPOSE exec -T openclaw chmod +x $BIN_DIR/spotify_player
        rm -f /tmp/openclaw_spotify_wrapper
    "
    ok "spotify_player wrapper created (LD_LIBRARY_PATH + --config-folder baked in)"

    register_approvals "spotify_player"
}

# ── Main ───────────────────────────────────────────────────────────────────────

ensure_bin_dir

NEW_BINS=()

for skill in "${SKILLS[@]}"; do
    case "$skill" in
        github)         install_github;         NEW_BINS+=(gh) ;;
        session-logs)   install_session_logs;   NEW_BINS+=(jq rg) ;;
        spotify-player) install_spotify_player; NEW_BINS+=(spotify_player) ;;
        summarize)
            skip "summarize — not available on Linux (macOS brew-only package)"
            ;;
        *)
            skip "$skill — unknown skill, skipping"
            ;;
    esac
done

# ── Update tools.exec.safeBins (merge, don't overwrite) ───────────────────────
# Uses a temp file to pass JSON data to python3, avoiding fragile shell string interpolation.

if [ "${#NEW_BINS[@]}" -gt 0 ]; then
    step "Merging new bins into tools.exec.safeBins"

    # Fetch current safeBins JSON from the container
    CURRENT_JSON=$(ssh "$HOST" "$COMPOSE exec -T openclaw openclaw config get tools.exec.safeBins 2>/dev/null || echo '[]'")

    # Build new-bins list and merge locally using a temp file to pass CURRENT_JSON safely
    TMPJSON=$(mktemp)
    trap 'rm -f "$TMPJSON"' EXIT
    printf '%s' "$CURRENT_JSON" > "$TMPJSON"

    NEW_BINS_PY="[$(printf "'%s'," "${NEW_BINS[@]}" | sed 's/,$//')]"
    MERGED=$(python3 - <<PYEOF
import json
with open("$TMPJSON") as f:
    raw = f.read().strip()
current = json.loads(raw) if raw else []
new = $NEW_BINS_PY
merged = sorted(set(current + new))
print(json.dumps(merged))
PYEOF
)
    ssh "$HOST" "$COMPOSE exec -T openclaw openclaw config set tools.exec.safeBins '$MERGED'"
    ok "safeBins updated: $MERGED"

    step "Restarting openclaw to apply config"
    ssh "$HOST" "cd ~/openclaw-deploy && $COMPOSE restart openclaw"
    ok "openclaw restarted"
fi

echo ""
echo -e "${BOLD}Skill setup complete.${NC}"
echo "  Run 'make doctor' to verify."
