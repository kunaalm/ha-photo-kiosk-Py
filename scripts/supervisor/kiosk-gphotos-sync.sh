#!/bin/bash
# HA Photo Kiosk — Google Photos sync (rclone).
#
# Pulls Google Photos into the kiosk's photo folder so the frame can show
# them. The host runs this (the engine container doesn't touch the network to
# Google). It reads its config from the shared config dir, so the web config
# UI can enable/disable and trigger it.
#
# Config (env-overridable): CONFIG_DIR, PHOTO_DIR, RCLONE, RCLONE_CONFIG.
# Reads ~kiosk/config/sync.json for enabled/remote/source; writes status to
# ~kiosk/config/sync-status.json.
set -u

KIOSK_USER="${KIOSK_USER:-kiosk}"
CONFIG_DIR="${CONFIG_DIR:-/home/$KIOSK_USER/config}"
PHOTO_DIR="${PHOTO_DIR:-/home/$KIOSK_USER/photos}"
RCLONE="${RCLONE:-$(command -v rclone || echo /usr/bin/rclone)}"
RCLONE_CONFIG="${RCLONE_CONFIG:-/home/$KIOSK_USER/.config/rclone/rclone.conf}"
CONFIG_FILE="$CONFIG_DIR/sync.json"
STATUS_FILE="$CONFIG_DIR/sync-status.json"

log() { echo "[kiosk-sync] $(date '+%H:%M:%S') $*"; }

read_config() {
    if [ -f "$CONFIG_FILE" ]; then
        ENABLED="$(python3 -c "import json,sys;print(json.load(open('$CONFIG_FILE')).get('enabled',False))" 2>/dev/null || echo false)"
        REMOTE="$(python3 -c "import json,sys;print(json.load(open('$CONFIG_FILE')).get('remote','gphotos'))" 2>/dev/null || echo gphotos)"
        SOURCE="$(python3 -c "import json,sys;print(json.load(open('$CONFIG_FILE')).get('source_path','media/by-month'))" 2>/dev/null || echo media/by-month)"
    else
        ENABLED="false"; REMOTE="gphotos"; SOURCE="media/by-month"
    fi
}

write_status() {
    # write_status <state> <error-or-empty> <count>
    local state="$1" err="$2" count="$3"
    mkdir -p "$CONFIG_DIR"
    cat > "$STATUS_FILE" <<EOF
{
  "state": "$state",
  "last_run": "$(date -Is)",
  "last_error": "$err",
  "photo_count": "$count"
}
EOF
}

run_sync() {
    log "syncing $REMOTE:$SOURCE -> $PHOTO_DIR"
    if ! "$RCLONE" --config "$RCLONE_CONFIG" sync \
        "$REMOTE:$SOURCE" "$PHOTO_DIR" --transfers 4 2>/tmp/kiosk-sync-err.log; then
        local err
        err="$(tail -1 /tmp/kiosk-sync-err.log 2>/dev/null | tr -d '\n')"
        log "sync failed: $err"
        write_status "error" "$err" "0"
        return 1
    fi
    local count
    count="$(find "$PHOTO_DIR" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) | wc -l)"
    log "sync ok ($count photos)"
    write_status "ok" "" "$count"
}

main() {
    read_config
    case "${1:-run}" in
        run)
            # Always clear the trigger so the .path watcher doesn't re-fire.
            [ -f "$CONFIG_DIR/sync-trigger" ] && rm -f "$CONFIG_DIR/sync-trigger"
            if [ "$(echo "$ENABLED" | tr '[:upper:]' '[:lower:]')" != "true" ]; then
                log "sync disabled (use the web config to enable)."
                exit 0
            fi
            if ! command -v rclone >/dev/null 2>&1 && ! [ -x "$RCLONE" ]; then
                write_status "error" "rclone not installed" "0"
                log "ERROR: rclone not installed."
                exit 1
            fi
            run_sync
            ;;
        status)
            cat "$STATUS_FILE" 2>/dev/null || echo '{"state":"never-run"}'
            ;;
        *) echo "Usage: $0 {run|status}"; exit 1 ;;
    esac
}

main "$@"