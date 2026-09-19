# Host-side supervisor

The deliberately-thin host process that owns the browser + display. It is the
counterpart to the engine container: the engine has all the smarts (proxy,
photo sources, idle detection, config), the supervisor just makes sure a
Chromium kiosk is always showing the frame page.

## What it does

1. **Waits for the engine** — polls `http://localhost:8080/frame/` until it
   returns 200 (the engine container may still be starting at boot).
2. **Launches Chromium kiosk** at that URL with the flags the VM harness
   proved are needed (`--kiosk`, `--no-first-run`, `--noerrdialogs`, ...).
3. **Restarts on crash** — if Chromium exits, relaunches after 3s. The kiosk
   never goes dark.

It does **not** do idle detection (client-side JS), proxying (engine), or
config (engine's config service). It is intentionally dumb — display + process
supervision only. This is the edge/device-agent pattern: thin client on the
metal, logic in a managed service.

## Files

- `kiosk-supervisor.sh` — the supervisor script (wait → launch → restart loop).
- `ha-photo-kiosk.service` — systemd unit that runs it as the `kiosk` user on
  the display session, `Restart=always`.

## Install

```bash
# 1. Create the kiosk user (once)
useradd -m kiosk

# 2. Install the supervisor script
sudo install -m 0755 supervisor/kiosk-supervisor.sh /usr/local/bin/kiosk-supervisor.sh

# 3. Install + enable the systemd unit
sudo install -m 0644 supervisor/ha-photo-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ha-photo-kiosk.service
```

## Configuration (env, all optional)

| Env | Default | Purpose |
|---|---|---|
| `KIOSK_ENGINE_URL` | `http://localhost:8080/frame/` | URL Chromium opens |
| `KIOSK_ENGINE_READY_URL` | same | URL polled for engine readiness |
| `KIOSK_ENGINE_WAIT_MAX` | `120` | seconds to wait for engine before launching anyway |
| `KIOSK_ENGINE_WAIT_INTERVAL` | `2` | poll interval |
| `KIOSK_CHROMIUM_BIN` | `chromium-browser` | Chromium binary |
| `KIOSK_CHROMIUM_FLAGS` | kiosk flags | Chromium launch flags |
| `DISPLAY` | `:0` | X display (physical on a real kiosk, Xvfb on headless) |

## Headless note

On a box with no physical display, point `DISPLAY` at an Xvfb instance
(e.g. `Xvfb :0 -screen 0 1280x800x24`) and run the supervisor against it. On a
real kiosk, `:0` is the physical display and the unit's `TTYPath=/dev/tty7`
gives Chromium a clean input session.
