#!/bin/bash
# Test the SUPERVISOR with a REAL Chromium browser in the VM.
# Verifies: supervisor waits for engine, launches real Chromium, renders HA,
# and restarts Chromium when it's killed.
set -u
export DISPLAY=:99
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

cd /opt/kiosk
echo "=== [1] start Xvfb ==="
Xvfb :99 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 &
sleep 2
echo "Xvfb started"

echo "=== [2] start engine (HA via 10.0.2.2:8123) ==="
HA_URL="http://10.0.2.2:8123" \
PHOTO_DIR="/opt/kiosk/photos" \
IDLE_TIMEOUT_SECONDS=8 \
SLIDE_INTERVAL_SECONDS=3 \
.venv/bin/python app.py --port 8080 >/tmp/engine.log 2>&1 &
sleep 3
echo "engine: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/frame/)"

echo "=== [3] start the SUPERVISOR (not Chromium directly) ==="
DISPLAY=:99 KIOSK_ENGINE_URL="http://localhost:8080/frame/" \
  KIOSK_ENGINE_READY_URL="http://localhost:8080/frame/" \
  KIOSK_ENGINE_WAIT_MAX=30 \
  KIOSK_CHROMIUM_BIN="chromium-browser" \
  nohup bash supervisor/kiosk-supervisor.sh >/tmp/supervisor.log 2>&1 &
SUP_PID=$!
echo "supervisor pid=$SUP_PID"
sleep 12

echo "=== [4] is REAL Chromium running? ==="
CHROME_PID=$(pgrep -f 'chrome.*frame' | head -1)
if [ -n "$CHROME_PID" ]; then echo "REAL CHROMIUM RUNNING (pid=$CHROME_PID)"; else echo "NO CHROMIUM — FAIL"; fi

echo "=== [5] screenshot ACTIVE (HA should render) ==="
import -window root /tmp/sup-active.png 2>/dev/null
echo "active: $(ls -la /tmp/sup-active.png | awk '{print $5}') bytes"

echo "=== [6] KILL Chromium — supervisor should restart it ==="
kill -9 $CHROME_PID 2>/dev/null
echo "killed chromium pid=$CHROME_PID, waiting for supervisor restart..."
sleep 10
NEW_CHROME=$(pgrep -f 'chrome.*frame' | head -1)
if [ -n "$NEW_CHROME" ]; then echo "SUPERVISOR RESTARTED CHROMIUM (new pid=$NEW_CHROME)"; else echo "NO RESTART — FAIL"; fi

echo "=== [7] screenshot after restart (HA should still render) ==="
import -window root /tmp/sup-restarted.png 2>/dev/null
echo "restarted: $(ls -la /tmp/sup-restarted.png | awk '{print $5}') bytes"

echo "=== [8] supervisor log (key lines) ==="
grep -E 'kiosk-supervisor|Chromium exited|launching' /tmp/supervisor.log
echo "=== TEST COMPLETE ==="
