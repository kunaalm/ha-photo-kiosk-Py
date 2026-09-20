# Host-side supervisor

The deliberately-thin host process that owns the browser + display. It is the
counterpart to the engine container: the engine has all the smarts (proxy,
photo sources, idle detection, config), the supervisor just makes sure a
Chromium kiosk is always showing the frame page.

## What it does

1. **Waits for the engine** — polls `http://localhost:8080/frame/` until it
   returns 200 (the engine container may still be starting at boot).
2. **Launches Chromium kiosk** at that URL. The flags quiet Chromium's
   first-run setup and transient error dialogs so a bare device boots straight
   to the display (`--kiosk --no-first-run --noerrdialogs --disable-infobars`).
3. **Restarts on crash** — if Chromium exits, relaunches after 3s. The kiosk
   never goes dark.

It does **not** do idle detection (client-side JS), proxying (engine), or
config (engine's config service). It is intentionally dumb — display + process
supervision only. This is the edge/device-agent pattern: thin client on the
metal, logic in a managed service.

## Files

- `kiosk-supervisor.sh` — the supervisor script (wait → launch → restart loop).
- `ha-photo-kiosk.service` — systemd unit that runs it as the `kiosk` user on
  the display session, `Restart=always`. The unit must live in
  `/etc/systemd/system/`; the script it runs lives in the kiosk user's home.

## Install

The `install.sh` installer does all of this for you (creates the `kiosk` user
and wires the machine as a kiosk). If you're running the supervisor on its
own, e.g. for a headless test box:

```bash
# 1. Create the kiosk user (once) — everything below lives in its home
useradd -m kiosk

# 2. Install the supervisor script into the kiosk user's home
sudo -u kiosk mkdir -p ~kiosk/bin
sudo install -m 0755 scripts/supervisor/kiosk-supervisor.sh ~kiosk/bin/kiosk-supervisor.sh

# 3. Install + enable the systemd unit (reads the script from ~kiosk)
sudo install -m 0644 scripts/supervisor/ha-photo-kiosk.service /etc/systemd/system/
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

## Security note: Chromium sandbox

The supervisor runs Chromium **without `--no-sandbox`** — the sandbox stays
enabled, which is the secure default. This works because the supervisor runs as
the non-root `kiosk` user.

If your Chromium is a **snap** (Ubuntu's `chromium-browser` is a transitional
package that installs the snap), snap's confinement can conflict with
Chromium's own sandbox and Chromium may fail to start. In that case, re-enable
`--no-sandbox` via the env override:

```
KIOSK_CHROMIUM_FLAGS="--noerrdialogs --disable-infobars --kiosk --no-sandbox --no-first-run"
```

Prefer a non-snap Chromium (e.g. the Debian `chromium` package) so the sandbox
can stay on.
