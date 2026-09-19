#!/bin/bash
# HA Photo Kiosk — installer.
#
# Turns a Debian-based box into a two-state kiosk in one command:
#   1. Checks prerequisites (root, docker, systemd).
#   2. Builds + starts the engine container (localhost-only).
#   3. Creates the kiosk user.
#   4. Installs the supervisor script + systemd unit.
#   5. Sets up the photos directory.
#
# The engine (smarts) runs in a container; the supervisor (browser + display)
# runs on the host. This script wires the two together.
set -u

# --- Config (env-overridable) -------------------------------------------
KIOSK_USER="${KIOSK_USER:-kiosk}"
ENGINE_PORT="${ENGINE_PORT:-8080}"
PHOTO_HOST_DIR="${PHOTO_HOST_DIR:-/opt/kiosk/photos}"
CONFIG_HOST_DIR="${CONFIG_HOST_DIR:-/opt/kiosk/config}"
HA_URL="${HA_URL:-http://192.168.20.12:8123}"
IDLE_TIMEOUT="${IDLE_TIMEOUT_SECONDS:-120}"
SLIDE_INTERVAL="${SLIDE_INTERVAL_SECONDS:-10}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[kiosk-install] $*"; }
die() { echo "[kiosk-install] ERROR: $*" >&2; exit 1; }

# --- 1. Prerequisites ----------------------------------------------------
check_prereqs() {
    [ "$(id -u)" -eq 0 ] || die "run as root (sudo)."
    command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker first."
    docker info >/dev/null 2>&1 || die "docker daemon not running (or no permission)."
    command -v systemctl >/dev/null 2>&1 || die "systemd not found (systemctl missing)."
    command -v docker compose >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 \
        || die "docker compose plugin not found."
    log "prerequisites OK (root, docker, systemd, compose)."
}

# --- 2. Engine container ------------------------------------------------
install_engine() {
    log "building + starting engine container (localhost:$ENGINE_PORT)..."
    # Write a compose override that pins the runtime config (HA URL, photos,
    # config volume) so the web config service persists across restarts.
    cat > "$REPO_DIR/docker-compose.override.yml" <<EOF
services:
  kiosk-engine:
    environment:
      HA_URL: "${HA_URL}"
      PHOTO_DIR: "/photos"
      IDLE_TIMEOUT_SECONDS: "${IDLE_TIMEOUT}"
      SLIDE_INTERVAL_SECONDS: "${SLIDE_INTERVAL}"
      CONFIG_FILE: "/config/kiosk.json"
    volumes:
      - "${CONFIG_HOST_DIR}:/config"
      - "${PHOTO_HOST_DIR}:/photos:ro"
EOF
    mkdir -p "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR"
    docker compose -f "$REPO_DIR/docker-compose.yml" -f "$REPO_DIR/docker-compose.override.yml" up -d --build \
        || die "engine container failed to start."
    log "engine container up."
}

# --- 3. Kiosk user -------------------------------------------------------
install_user() {
    if id "$KIOSK_USER" >/dev/null 2>&1; then
        log "kiosk user '$KIOSK_USER' already exists."
    else
        useradd -m -s /bin/bash "$KIOSK_USER" || die "could not create user '$KIOSK_USER'."
        log "created kiosk user '$KIOSK_USER'."
    fi
}

# --- 4. Supervisor + systemd --------------------------------------------
install_supervisor() {
    log "installing supervisor script + systemd unit..."
    install -m 0755 "$REPO_DIR/supervisor/kiosk-supervisor.sh" /usr/local/bin/kiosk-supervisor.sh
    # Point the unit at the engine port and kiosk user.
    sed -e "s|^User=.*|User=$KIOSK_USER|" \
        -e "s|^Group=.*|Group=$KIOSK_USER|" \
        -e "s|ExecStart=.*|ExecStart=/usr/local/bin/kiosk-supervisor.sh|" \
        "$REPO_DIR/supervisor/ha-photo-kiosk.service" > /etc/systemd/system/ha-photo-kiosk.service
    systemctl daemon-reload
    systemctl enable ha-photo-kiosk.service >/dev/null 2>&1
    log "supervisor installed + enabled."
}

# --- 5. Photos dir -------------------------------------------------------
setup_photos() {
    mkdir -p "$PHOTO_HOST_DIR"
    chown -R "$KIOSK_USER":"$KIOSK_USER" "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR" 2>/dev/null || true
    log "photos dir ready at $PHOTO_HOST_DIR (drop JPG/PNG/WebP files here)."
}

# --- Main ----------------------------------------------------------------
main() {
    log "HA Photo Kiosk installer starting."
    check_prereqs
    install_user
    install_engine
    install_supervisor
    setup_photos
    log ""
    log "Install complete."
    log "  - Engine:  http://localhost:$ENGINE_PORT/frame/  (the kiosk page)"
    log "  - Config:  http://localhost:$ENGINE_PORT/config/  (web config service)"
    log "  - Photos:  $PHOTO_HOST_DIR"
    log "  - HA URL:  $HA_URL"
    log ""
    log "Start the kiosk now:  systemctl start ha-photo-kiosk.service"
    log "Or reboot to auto-start. Point Chromium at http://localhost:$ENGINE_PORT/frame/."
}

main "$@"
