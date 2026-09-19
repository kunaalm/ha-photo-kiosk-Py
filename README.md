# ha-photo-kiosk Py

A containerized **two-state kiosk engine**: display your Home Assistant dashboard in a Chromium kiosk as usual, but when the display goes idle, flip to a **photo frame**. The browser is a dumb renderer that loads **one URL once and never restarts** — the engine is the sole orchestrator.

## The mental model: state machine, not browser control

```
                  touches/moves/key
   ┌──────┐      ─────────────▶      ┌────────────┐
   │ ACTIVE │ (HA dashboard shown)   │   IDLE     │ (photo frame shown)
   └──────┘      ◀───── idle N sec   └────────────┘
```

- **ACTIVE** — the engine reverse-proxies your real Home Assistant at a single local URL.
- **IDLE** — after N seconds without input, the frame page (served by the engine) fades the HA iframe out and shows a photo slideshow.
- Input returns → instant flip back to the live dashboard. No reload, no re-auth: HA stays loaded in the background.

The **idle detection is client-side JavaScript** on the served page — it watches real input events and flips layers. The engine only serves content. This is the same "attract mode" pattern used by digital-signage and conference-room displays.

## Why a reverse proxy instead of just an iframe or a browser restart

- **Real Home Assistant sends `X-Frame-Options: SAMEORIGIN`**, which makes a cross-origin `<iframe>` fail outright (verified against HA 2026.x stable). The engine proxies HA at `localhost:8080/ha/*` and strips that header, so the iframe is allowed. Safe because the proxy binds to **localhost only** on the kiosk box.
- No Chromium restarts: restarting to swap URLs loses the HA session/logs you out and causes a flicker. With the multiplexer, the dashboard stays alive in the background and flipping back is *instant*.

## Architecture

- **Container: the engine** (Python) — reverse-proxies HA, serves the frame page and `/images`, caches and serves photo sources. Pure network + compute, zero display → fully CI-testable headless.
- **Host: the browser** — one Chromium tab pointing at `http://localhost:8080/`. Thin client that owns the framebuffer; all smarts live in the engine.

## Photo sources (pluggable)

The engine exposes a `Source` interface. Every source yields an ordered list of image URLs/objects the frame page cycles:

| Source | What it is |
|---|---|
| `local` | A directory of JPG/PNG/WebP files (also the target for "sync Apple iCloud → local folder" flows). |
| `http` | Any HTTP URL or feed that yields photos (e.g. a self-hosted Immich/PhotoPrism album endpoint, or a simple JSON list of image URLs). |
| *(future)* `google-photos` | Google Photos Library API via OAuth2 client. Real but heavier (client registration + token refresh). Extension point, not built yet. |

> **Apple Photos is deliberately NOT a native source.** Apple has no public photos API — only reverse-engineered iCloud endpoints that break without notice. The sane path is syncing (iCloud → machine → local folder) and pointing a `local` source at it. Don't build against a vendor API that doesn't legally exist.

## Quick start (engine, container)

```bash
docker build -t ha-photo-kiosk-py .

docker run -d --name kiosk-engine \
  -p 127.0.0.1:8080:8080 \
  -e HA_URL="http://192.168.20.12:8123" \
  -e PHOTO_SOURCE="local" \
  -e PHOTO_DIR="/photos" \
  -e IDLE_TIMEOUT_SECONDS="120" \
  -v /path/to/photos:/photos:ro \
  -v kiosk-cache:/cache \
  ha-photo-kiosk-py
```

Point your Chromium kiosk at `http://localhost:8080/` and you're done — no browser restarts ever.

## Local dev (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate   # or just use system python3 (stdlib-only)
./app.py serve --ha-url http://192.168.20.12:8123
# then open http://localhost:8080/
```

## Tests

```bash
python3 -m pytest tests/
```

The testable core (state machine, photo sources, proxy header handling) has no display or Docker dependency — it runs headless, which is why the engine (not the browser) is the container.

There's also a **real-browser VM harness** (`tests/vm-harness/`) that runs the actual kiosk in a QEMU/KVM VM with Xvfb + Chromium against a real Home Assistant instance, driving the ACTIVE→IDLE→ACTIVE toggle and capturing screenshots. It caught three real proxy bugs unit tests couldn't see. See [TESTING.md](TESTING.md) for both layers and the bugs found.

## Status

Very early scaffolding — see `docs/` and the test suite for what's real. Structured explicitly so **Google Photos OAuth** and **additional sources** are clean extension points.