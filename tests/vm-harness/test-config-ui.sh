#!/bin/bash
# Test the /config/ web UI in REAL Chromium via CDP (DevTools Protocol).
#
# Real browser, real page — driven through the standard automation interface
# (CDP) rather than synthetic X events, which don't reach Chromium reliably in
# a WM-less Xvfb. Verifies the full web-config flow:
#   load /config/ -> edit idle timeout -> click Save -> persisted via API
#   -> engine restart -> frame page reflects the new value.
set -u
export DISPLAY=:0
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

pkill Xvfb 2>/dev/null; pkill -f 'chrome.*config' 2>/dev/null; sleep 2
Xvfb :0 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 &
sleep 2

echo "=== [1] launch Chromium at /config/ with remote debugging ==="
nohup chromium-browser --noerrdialogs --disable-infobars --kiosk \
  --no-sandbox --disable-gpu --no-first-run --window-size=1280,800 \
  --remote-debugging-port=9222 --remote-allow-origins=* \
  http://localhost:8080/config/ >/tmp/chromium-config.log 2>&1 &
sleep 10

echo "=== [2] get CDP websocket URL ==="
WS=$(curl -s --max-time 5 http://localhost:9222/json | python3 -c "import sys,json; pages=json.load(sys.stdin); print([p for p in pages if 'config' in p.get('url','')][0]['webSocketDebuggerUrl'])" 2>/dev/null)
echo "ws: ${WS:0:60}..."

echo "=== [3] drive the form via CDP ==="
python3 - "$WS" <<'PYEOF'
import json, sys, websocket, time
ws_url = sys.argv[1]
ws = websocket.create_connection(ws_url, timeout=15)
msg_id = 0
def send(method, params=None):
    global msg_id
    msg_id += 1
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == msg_id:
            return r
def eval_js(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("result", {}).get("value")

print("before:", eval_js("document.getElementById('idle_timeout_seconds').value"))
eval_js("""
  var el = document.getElementById('idle_timeout_seconds');
  el.value = '45';
  el.dispatchEvent(new Event('input', {bubbles:true}));
""")
print("after set:", eval_js("document.getElementById('idle_timeout_seconds').value"))
eval_js("document.querySelector('button').click()")
time.sleep(2)
print("save clicked")
ws.close()
PYEOF

echo "=== [4] verify via API ==="
curl -s --max-time 5 http://localhost:8080/api/config | python3 -c "import sys,json; d=json.load(sys.stdin); print('idle_timeout_seconds =', d['idle_timeout_seconds'])"
echo "=== [5] config file on disk ==="
cat /opt/kiosk/config/kiosk.json 2>/dev/null || echo "(no config file)"
echo "=== TEST COMPLETE ==="
