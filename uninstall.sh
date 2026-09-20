#!/bin/bash
# HA Photo Kiosk — uninstaller.
#
# Removes what install.sh created: engine container/venv, supervisor systemd
# unit + script, kiosk user, and (optionally) the photos/config data.
set -u

KIOSK_USER="${KIOSK_USER:-kiosk}"
KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
[ -n "$KIOSK_HOME" ] || KIOSK_HOME="/home/$KIOSK_USER"
PHOTO_HOST_DIR="${PHOTO_HOST_DIR:-$KIOSK_HOME/photos}"
CONFIG_HOST_DIR="${CONFIG_HOST_DIR:-$KIOSK_HOME/config}"
SUPERVISOR_BIN="${SUPERVISOR_BIN:-$KIOSK_HOME/bin/kiosk-supervisor.sh}"

log() { echo "[kiosk-uninstall] $*"; }
die() { echo "[kiosk-uninstall] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)."

# --- Stop + remove supervisor -------------------------------------------
log "stopping + disabling supervisor..."
systemctl stop ha-photo-kiosk.service 2>/dev/null
systemctl disable ha-photo-kiosk.service 2>/dev/null
rm -f /etc/systemd/system/ha-photo-kiosk.service
rm -f "$SUPERVISOR_BIN"
systemctl daemon-reload
log "supervisor removed."

# --- Stop + remove engine container / source service --------------------
if command -v docker >/dev/null 2>&1; then
    log "stopping + removing engine container..."
    docker rm -f kiosk-engine 2>/dev/null
    docker image rm -f "${IMAGE:-ghcr.io/kunaalm/ha-photo-kiosk-py:latest}" 2>/dev/null
    log "engine container removed."
fi

if [ -f /etc/systemd/system/kiosk-engine.service ]; then
    systemctl stop kiosk-engine.service 2>/dev/null
    systemctl disable kiosk-engine.service 2>/dev/null
    rm -f /etc/systemd/system/kiosk-engine.service
    systemctl daemon-reload
    log "engine source service removed."
fi

# --- Optional: remove data (before deleting the user, while still known) ---
if [ "${REMOVE_DATA:-0}" = "1" ]; then
    rm -rf "$PHOTO_HOST_DIR" "$CONFIG_HOST_DIR"
    log "removed photos + config dirs."
else
    log "kept photos/config (set REMOVE_DATA=1 to delete)."
fi

# --- Remove kiosk user (home + everything under it) ---------------------
if id "$KIOSK_USER" >/dev/null 2>&1; then
    userdel -r "$KIOSK_USER" 2>/dev/null && log "removed kiosk user '$KIOSK_USER' (incl. home)." \
        || log "could not remove user '$KIOSK_USER' (remove manually)."
else
    log "kiosk user '$KIOSK_USER' not present."
fi

log "Uninstall complete."