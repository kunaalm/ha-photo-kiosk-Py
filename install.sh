#!/bin/bash
# HA Photo Kiosk — installer.
#
# Turns a Debian-based box into a two-state kiosk in one command.
#
# Default install: pulls the published engine container from GHCR (no local
# build). The engine (smarts) runs in the container; the supervisor (browser +
# display) runs on the host.
#
# Alternative: `--from-source` installs the engine as a Python venv on the host
# instead of a container — useful for hacking on the code or boxes without
# Docker.
#
# Either way, after install you are directed to the WEB CONFIG SERVICE
# (http://localhost:PORT/config/) to complete the setup — set your HA URL,
# photo source, and timings there, not via this script.
set -u

# --- Config (env-overridable) -------------------------------------------
KIOSK_USER="${KIOSK_USER:-kiosk}"
ENGINE_PORT="${ENGINE_PORT:-8080}"
PHOTO_HOST_DIR="${PHOTO_HOST_DIR:-/opt/kiosk/photos}"
CONFIG_HOST_DIR="${CONFIG_HOST_DIR:-/opt/kiosk/config}"
ENGINE_SRC_DIR="${ENGINE_SRC_DIR:-/opt/kiosk/engine}"
IMAGE="${IMAGE:-ghcr.io/kunaalm/ha-photo-kiosk-py:latest}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="container"   # container | source

log() { echo "[kiosk-install] $*"; }
die() { echo "[kiosk-install] ERROR: $*" >&2; exit 1; }

# --- CLI ----------------------------------------------------------------
parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --from-source) MODE="source" ;;
            --help|-h) echo "Usage: install.sh [--from-source]"; exit 0 ;;
            *) die "unknown option: $1 (try --help)" ;;
        esac
        shift
    done
}

# --- 1. Prerequisites ----------------------------------------------------
check_prereqs() {
    [ "$(id -u)" -eq 0 ] || die "run as root (sudo)."
    command -v systemctl >/dev/null 2>&1 || die "systemd not found (systemctl missing)."
    if [ "$MODE" = "container" ]; then
        command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker, or use: bash install.sh --from-source"
        docker info >/dev/null 2>&1 || die "docker daemon not running (or no permission)."
        command -v docker compose >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 \
            || die "docker compose plugin not found."
    else
        command -v python3 >/dev/null 2>&1 || die "python3 not found for --from-source mode."
    fi
    log "prerequisites OK (root, systemd, mode=$MODE)."
}

# --- 2. Engine: container (default) or venv (--from-source) -------------
install_engine() {
    if [ "$MODE" = "container" ]; then
        log "pulling engine image ($IMAGE)..."
        docker pull "$IMAGE" >/dev/null 2>&1 || die "could not pull $IMAGE."
        # Write a compose override pinning runtime config + volumes so the web
        # config service persists across restarts.
        cat > "$REPO_DIR/docker-compose.override.yml" <<EOF
services:
  kiosk-engine:
    image: ${IMAGE}
    environment:
      HA_URL: "http://localhost:8123"   # seed; edit via web config
      PHOTO_DIR: "/photos"
      IDLE_TIMEOUT_SECONDS: "120"
      SLIDE_INTERVAL_SECONDS: "10"
      CONFIG_FILE: "/config/kiosk.json"
    volumes:
      - "${CONFIG_HOST_DIR}:/config"
      - "${PHOTO_HOST_DIR}:/photos:ro"
EOF
        mkdir -p "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR"
        docker compose -f "$REPO_DIR/docker-compose.yml" -f "$REPO_DIR/docker-compose.override.yml" up -d \
            || die "engine container failed to start."
        log "engine container up."
    else
        log "installing engine as Python venv at $ENGINE_SRC_DIR..."
        mkdir -p "$ENGINE_SRC_DIR" "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR"
        # rsync the repo into place (keep .git so it's updatable).
        command -v rsync >/dev/null 2>&1 && {
            rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
                "$REPO_DIR/" "$ENGINE_SRC_DIR/"
        } || {
            cp -r "$REPO_DIR"/. "$ENGINE_SRC_DIR/" 2>/dev/null
            rm -rf "$ENGINE_SRC_DIR/.venv" "$ENGINE_SRC_DIR/__pycache__"
        }
        python3 -m venv "$ENGINE_SRC_DIR/.venv" || die "could not create venv."
        "$ENGINE_SRC_DIR/.venv/bin/pip" install --quiet -r "$ENGINE_SRC_DIR/requirements.txt" \
            || die "could not install Python deps."
        log "engine venv ready."
    fi
}

# --- 3. Engine service (source mode runs app.py under systemd) ----------
install_engine_service() {
    [ "$MODE" = "source" ] || return 0
    cat > /etc/systemd/system/kiosk-engine.service <<EOF
[Unit]
Description=HA Photo Kiosk engine (Python venv)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$ENGINE_SRC_DIR/.venv/bin/python $ENGINE_SRC_DIR/app.py
Environment=PORT=$ENGINE_PORT
Environment=HA_URL=http://localhost:8123
Environment=PHOTO_DIR=/photos
Environment=CONFIG_FILE=$CONFIG_HOST_DIR/kiosk.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable kiosk-engine.service >/dev/null 2>&1
    log "engine systemd service installed + enabled."
}

# --- 4. Kiosk user --------------------------------------------------------
install_user() {
    if id "$KIOSK_USER" >/dev/null 2>&1; then
        log "kiosk user '$KIOSK_USER' already exists."
    else
        useradd -m -s /bin/bash "$KIOSK_USER" || die "could not create user '$KIOSK_USER'."
        log "created kiosk user '$KIOSK_USER'."
    fi
}

# --- 5. Supervisor + systemd --------------------------------------------
install_supervisor() {
    log "installing supervisor script + systemd unit..."
    install -m 0755 "$REPO_DIR/supervisor/kiosk-supervisor.sh" /usr/local/bin/kiosk-supervisor.sh
    sed -e "s|^User=.*|User=$KIOSK_USER|" \
        -e "s|^Group=.*|Group=$KIOSK_USER|" \
        -e "s|ExecStart=.*|ExecStart=/usr/local/bin/kiosk-supervisor.sh|" \
        "$REPO_DIR/supervisor/ha-photo-kiosk.service" > /etc/systemd/system/ha-photo-kiosk.service
    systemctl daemon-reload
    systemctl enable ha-photo-kiosk.service >/dev/null 2>&1
    log "supervisor installed + enabled."
}

# --- 6. Photos dir -------------------------------------------------------
setup_photos() {
    mkdir -p "$PHOTO_HOST_DIR"
    chown -R "$KIOSK_USER":"$KIOSK_USER" "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR" 2>/dev/null || true
    log "photos dir ready at $PHOTO_HOST_DIR (drop JPG/PNG/WebP files here)."
}

# --- Main ----------------------------------------------------------------
main() {
    parse_args "$@"
    log "HA Photo Kiosk installer starting (mode=$MODE)."
    check_prereqs
    install_user
    install_engine
    install_engine_service
    install_supervisor
    setup_photos

    # Start the engine now (container starts via compose; source via systemd).
    if [ "$MODE" = "source" ]; then
        systemctl start kiosk-engine.service && log "engine started."
    fi

    log ""
    log "=========================== INSTALL COMPLETE ========================"
    log ""
    log "  NEXT STEP — not done until you configure it:"
    log "  Open the WEB CONFIG SERVICE in a browser:"
    log ""
    log "      http://localhost:$ENGINE_PORT/config/"
    log ""
    log "  There you set:"
    log "    - Home Assistant URL (http://<your-ha-ip>:8123)"
    log "    - Photo source (local dir or HTTP catalog)"
    log "    - Photo directory / Idle timeout / Slide interval"
    log "  Then restart the engine for changes to take effect:"
    log "      sudo systemctl restart kiosk-engine   # source install"
    log "      sudo docker restart kiosk-engine       # container install"
    log ""
    log "  Start the kiosk now:   systemctl start ha-photo-kiosk.service"
    log "  The kiosk page itself:  http://localhost:$ENGINE_PORT/frame/"
    log ""
    log "  Photos dir:      $PHOTO_HOST_DIR"
    log ""
}

main "$@"