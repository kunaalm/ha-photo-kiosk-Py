#!/bin/bash
# HA Photo Kiosk — host-side supervisor.
#
# The deliberately-thin host process that owns the browser + display. It:
#   1. Waits for the engine container to be serving /frame/ (localhost:8080).
#   2. Launches Chromium in kiosk mode at that URL.
#   3. Restarts Chromium if it crashes or exits.
#
# All the smarts (proxy, photo sources, idle detection, config) live in the
# engine container; this script is intentionally dumb — display + process
# supervision only. This is the edge/device-agent pattern: thin client on the
# metal, logic in a managed service.
set -u

# --- Config (env-overridable) -------------------------------------------
ENGINE_URL="${KIOSK_ENGINE_URL:-http://localhost:8080/frame/}"
ENGINE_READY_URL="${KIOSK_ENGINE_READY_URL:-http://localhost:8080/frame/}"
ENGINE_WAIT_MAX="${KIOSK_ENGINE_WAIT_MAX:-120}"   # seconds to wait for engine
ENGINE_WAIT_INTERVAL="${KIOSK_ENGINE_WAIT_INTERVAL:-2}"
DISPLAY="${DISPLAY:-:0}"
# Shared config dir (mounted into the engine at /config). The engine writes a
# "gphotos-open" file here to tell the supervisor to point Chromium at the
# Google OAuth URL (one-time setup, approved on the kiosk screen).
CONFIG_DIR="${KIOSK_CONFIG_DIR:-/home/kiosk/config}"
GPHOTOS_OPEN_FILE="$CONFIG_DIR/gphotos-open"
# Debian's chromium package installs `chromium`; older/Raspberry Pi OS uses
# `chromium-browser`. Prefer whatever is installed, default to the common one.
if command -v chromium >/dev/null 2>&1; then
    CHROMIUM_BIN="${KIOSK_CHROMIUM_BIN:-chromium}"
elif command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM_BIN="${KIOSK_CHROMIUM_BIN:-chromium-browser}"
else
    CHROMIUM_BIN="${KIOSK_CHROMIUM_BIN:-chromium}"
fi
CHROMIUM_FLAGS="${KIOSK_CHROMIUM_FLAGS:---noerrdialogs --disable-infobars --kiosk --disable-session-crashed-bubble --disable-features=TranslateUI --no-first-run}"

log() { echo "[kiosk-supervisor] $(date '+%H:%M:%S') $*"; }

# --- 1. Wait for the engine ---------------------------------------------
wait_for_engine() {
    local waited=0
    log "waiting for engine at $ENGINE_READY_URL (max ${ENGINE_WAIT_MAX}s)..."
    while [ "$waited" -lt "$ENGINE_WAIT_MAX" ]; do
        if curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$ENGINE_READY_URL" 2>/dev/null | grep -q "200"; then
            log "engine is up."
            return 0
        fi
        sleep "$ENGINE_WAIT_INTERVAL"
        waited=$((waited + ENGINE_WAIT_INTERVAL))
    done
    log "WARNING: engine not reachable after ${ENGINE_WAIT_MAX}s; launching Chromium anyway (it will retry)."
    return 0
}

# --- 2. Launch + supervise Chromium -------------------------------------
# If the engine has written a gphotos-open file (Google OAuth in progress),
# point Chromium at that URL instead of the kiosk page. The user approves on
# the kiosk screen; once the engine clears the file, we return to /frame/.
run_kiosk() {
    local target="$ENGINE_URL"
    if [ -f "$GPHOTOS_OPEN_FILE" ]; then
        local oauth_url
        oauth_url="$(head -1 "$GPHOTOS_OPEN_FILE" 2>/dev/null | tr -d '\n')"
        if [ -n "$oauth_url" ]; then
            target="$oauth_url"
            log "Google OAuth in progress — pointing Chromium at the auth URL."
        fi
    fi
    log "launching Chromium kiosk at $target"
    # shellcheck disable=SC2086  # flags intentionally word-split
    "$CHROMIUM_BIN" $CHROMIUM_FLAGS "$target"
}

main() {
    wait_for_engine
    while true; do
        # If an OAuth flow is in progress, remember it so we can watch for
        # completion and return to /frame/.
        local oauth_active=0
        [ -f "$GPHOTOS_OPEN_FILE" ] && oauth_active=1
        run_kiosk &
        local chrome_pid=$!
        # While Chromium runs, watch for two transitions:
        #  - if we were showing /frame/ and gphotos-open appears (OAuth started),
        #    kill Chromium so it relaunches at the auth URL;
        #  - if we were showing the OAuth URL and gphotos-open clears (token
        #    stored), kill Chromium so it relaunches at /frame/.
        while kill -0 "$chrome_pid" 2>/dev/null; do
            if [ "$oauth_active" = "0" ] && [ -f "$GPHOTOS_OPEN_FILE" ]; then
                log "Google OAuth started — pointing Chromium at the auth URL."
                kill "$chrome_pid" 2>/dev/null
                break
            fi
            if [ "$oauth_active" = "1" ] && [ ! -f "$GPHOTOS_OPEN_FILE" ]; then
                log "Google OAuth complete — returning to the kiosk page."
                kill "$chrome_pid" 2>/dev/null
                break
            fi
            sleep 2
        done
        wait "$chrome_pid" 2>/dev/null
        local rc=$?
        log "Chromium exited (rc=$rc); restarting in 3s..."
        sleep 3
    done
}

main "$@"
