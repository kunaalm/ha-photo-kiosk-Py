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
# Always point Chromium at the kiosk page. Google OAuth login happens in a
# SEPARATE browser window launched by the auth service, never here — the kiosk
# browser stays hands-off at /frame/.
run_kiosk() {
    log "launching Chromium kiosk at $ENGINE_URL"
    # shellcheck disable=SC2086  # flags intentionally word-split
    "$CHROMIUM_BIN" $CHROMIUM_FLAGS "$ENGINE_URL"
}

main() {
    wait_for_engine
    while true; do
        run_kiosk
        local rc=$?
        log "Chromium exited (rc=$rc); restarting in 3s..."
        sleep 3
    done
}

main "$@"
