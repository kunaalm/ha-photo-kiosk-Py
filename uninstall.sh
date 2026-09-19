#!/bin/bash
# HA Photo Kiosk — uninstaller.
#
# Removes everything install.sh created: engine container, supervisor systemd
# unit, kiosk user, and (optionally) the photos/config dirs.
set -u

KIOSK_USER="${KIOSK_USER:-kiosk}"
PHOTO_HOST_DIR="${PHOTO_HOST_DIR:-/opt/kiosk/photos}"
CONFIG_HOST_DIR="${CONFIG_HOST_DIR:-/opt/kiosk/config}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[kiosk-uninstall] $*"; }
die() { echo "[kiosk-uninstall] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)."

# --- Stop + remove supervisor -------------------------------------------
log "stopping + disabling supervisor..."
systemctl stop ha-photo-kiosk.service 2>/dev/null
systemctl disable ha-photo-kiosk.service 2>/dev/null
rm -f /etc/systemd/system/ha-photo-kiosk.service
rm -f /usr/local/bin/kiosk-supervisor.sh
systemctl daemon-reload
log "supervisor removed."

# --- Stop + remove engine container / source service --------------------
if command -v docker >/dev/null 2>&1; then
    log "stopping + removing engine container..."
    docker compose -f "$REPO_DIR/docker-compose.yml" -f "$REPO_DIR/docker-compose.override.yml" down -v 2>/dev/null \
        || docker rm -f kiosk-engine 2>/dev/null
    rm -f "$REPO_DIR/docker-compose.override.yml"
    log "engine container removed."
fi

if [ -f /etc/systemd/system/kiosk-engine.service ]; then
    systemctl stop kiosk-engine.service 2>/dev/null
    systemctl disable kiosk-engine.service 2>/dev/null
    rm -f /etc/systemd/system/kiosk-engine.service
    systemctl daemon-reload
    log "engine source service removed."
fi

# --- Remove kiosk user ---------------------------------------------------
if id "$KIOSK_USER" >/dev/null 2>&1; then
    userdel -r "$KIOSK_USER" 2>/dev/null && log "removed kiosk user '$KIOSK_USER'." \
        || log "could not remove user '$KIOSK_USER' (remove manually)."
else
    log "kiosk user '$KIOSK_USER' not present."
fi

# --- Optional: remove data dirs ------------------------------------------
if [ "${REMOVE_DATA:-0}" = "1" ]; then
    rm -rf "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR"
    log "removed photos + config dirs."
else
    log "kept photos/config dirs (set REMOVE_DATA=1 to delete)."
fi

log "Uninstall complete."
