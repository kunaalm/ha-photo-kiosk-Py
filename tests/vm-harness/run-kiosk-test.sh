#!/bin/bash
# VM end-to-end kiosk test harness.
#
# Runs inside a QEMU/KVM VM with Xvfb + Chromium, against a real Home
# Assistant instance. Verifies the full two-state kiosk in a real browser:
#   ACTIVE (HA renders) -> IDLE (photo frame) -> ACTIVE (input) -> IDLE
#
# This is the test level that catches what unit tests can't: real Chromium
# rendering, real HA frontend, real proxy behavior. It found two real bugs
# (see TESTING.md): the /ha/ subpath asset break and the Content-Encoding
# double-decode black screen.
set -u
export DISPLAY=:99
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

cd /opt/kiosk
echo "=== [1] start Xvfb ==="
Xvfb :99 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
sleep 2
echo "Xvfb pid=$XVFB_PID"

echo "=== [2] start engine (HA via 10.0.2.2:8123) ==="
HA_URL="http://10.0.2.2:8123" \
PHOTO_DIR="/opt/kiosk/photos" \
IDLE_TIMEOUT_SECONDS=8 \
SLIDE_INTERVAL_SECONDS=3 \
.venv/bin/python app.py --port 8080 >/tmp/engine.log 2>&1 &
ENGINE_PID=$!
sleep 3
echo "engine pid=$ENGINE_PID"
curl -s -o /dev/null -w "engine /frame/ -> %{http_code}\n" http://localhost:8080/frame/
curl -s http://localhost:8080/photos.json | head -c 200; echo ""

echo "=== [3] launch Chromium kiosk at engine frame page ==="
chromium-browser --noerrdialogs --disable-infobars --kiosk \
  --disable-session-crashed-bubble --disable-features=TranslateUI \
  --no-sandbox --disable-gpu --no-first-run --window-size=1280,800 \
  http://localhost:8080/frame/ >/tmp/chromium.log 2>&1 &
CHROME_PID=$!
sleep 8
echo "chromium pid=$CHROME_PID"

echo "=== [4] screenshot ACTIVE state (should show HA) ==="
import -window root /tmp/shot-active.png 2>/dev/null || xwd -root -out /tmp/shot-active.xwd
echo "captured active"

echo "=== [5] wait for idle timeout (8s) + fade ==="
sleep 12
import -window root /tmp/shot-idle.png 2>/dev/null || xwd -root -out /tmp/shot-idle.xwd
echo "captured idle"

echo "=== [6] simulate input (mouse move) -> should flip back to ACTIVE ==="
xdotool mousemove 640 400
sleep 3
import -window root /tmp/shot-active-again.png 2>/dev/null || xwd -root -out /tmp/shot-active-again.xwd
echo "captured active-again"

echo "=== [7] wait idle again, capture second idle ==="
sleep 12
import -window root /tmp/shot-idle-2.png 2>/dev/null || xwd -root -out /tmp/shot-idle-2.xwd
echo "captured idle-2"

echo "=== [8] results ==="
ls -la /tmp/shot-*.png /tmp/shot-*.xwd 2>/dev/null
echo "--- engine log tail ---"
tail -5 /tmp/engine.log
echo "--- chromium alive? ---"
ps -p $CHROME_PID >/dev/null && echo "chromium RUNNING" || echo "chromium DEAD"
echo "=== TEST DRIVER COMPLETE ==="
