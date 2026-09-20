# Design & Implementation Audit

A code-level design document for **ha-photo-kiosk**: what each file and
function does, how the logic is split, and how the pieces connect. Written to
audit the generated implementation — read it alongside the code.

For the high-level mental model (what it is, why the split), see
[ARCHITECTURE.md](ARCHITECTURE.md). For the testing strategy, see
[TESTING.md](TESTING.md).

---

## 1. Process model

Two processes, split by what each must own:

| Process | Runs as | Owns | Why |
|---|---|---|---|
| **Engine** | Docker container (or venv) | HTTP server, HA proxy, photo sources, config, upload | Pure network + compute; headless-testable |
| **Supervisor** | Host systemd service | Chromium, the display/framebuffer | Must own the physical display |

The browser (Chromium) is a dumb renderer: it loads **one URL** (`/frame/`)
once and never restarts. The engine multiplexes what that URL shows.

---

## 2. Entry point — `apps/app.py`

The only thing that runs the engine. Thin bootstrap, no logic:

- `build_parser()` — CLI flags `--host`, `--port`, `--ha-url` (all optional;
  env vars are the source of truth, flags override).
- `main()` — the startup sequence:
  1. `Config.from_env()` → build config from env.
  2. Apply CLI overrides (host/port/ha_url).
  3. Construct `KioskServer(cfg, config_store=ConfigStore(CONFIG_FILE))`.
  4. `srv.build_app()` → the aiohttp app (route table).
  5. Start an `aiohttp` runner + TCP site on `cfg.host:cfg.port`.
  6. Block forever on `asyncio.Event().wait()`; on shutdown, close the server
     and the aiohttp client session.

Key point: **the config file path is `CONFIG_FILE` env, default
`/config/kiosk.json`** — the web config service writes here.

---

## 3. Configuration — `kiosk_py/config.py` + `config_store.py`

### 3a. `Config` (config.py)

A `@dataclass` holding every runtime setting, with built-in defaults so the
engine starts with zero config. `Config.from_env()` reads each field from an
env var (e.g. `HA_URL`, `PORT`, `IDLE_TIMEOUT_SECONDS`, `PHOTO_SOURCE`).

Two helper functions:
- `_env_bool(name, default)` — parses `1/true/yes/on` as True.
- `_env_int(name, default)` — parses an int, falls back on empty.

**Security rule:** Google secrets (`google_client_id/secret/refresh_token`)
have **no file default** and are **not** in `EDITABLE_FIELDS` — they are
env-only, so the web config service can never read or write them.

### 3b. `ConfigStore` (config_store.py)

Persists a JSON config file on top of env defaults. Resolution order
(highest wins):

```
1. config file  (written by the web service)
2. env vars
3. built-in defaults
```

- `EDITABLE_FIELDS` — the whitelist of non-secret fields the web UI may
  read/write. This is the security boundary: anything not listed is invisible
  to the config service.
- `load()` — read the JSON file as a dict; `{}` if missing or corrupt.
- `save(data)` — **merge** (not replace) the whitelisted fields into the
  existing file. The merge is what makes partial saves safe: posting only
  `idle_timeout` won't clobber `ha_url`.
- `effective_config()` — `Config.from_env()` then overlay file values.
- `public_state()` — the effective values of the editable fields, for the UI.

---

## 4. HTTP server — `kiosk_py/server.py`

`KioskServer` is the whole HTTP surface. It holds:
- `self.effective` — the effective `Config` (env + file).
- `self.source` — the active photo `Source` (from `get_source`).
- `self.ha_origin` — the HA base URL to proxy to.
- `self._client` — a lazily-created `aiohttp.ClientSession` (aiohttp requires
  a running event loop to create one, so it's made on first use).

### 4a. Route table — `build_app()`

Registration order matters (aiohttp matches in order; engine routes win over
the HA catch-all):

| Method | Path | Handler | Purpose |
|---|---|---|---|
| GET | `/frame/`, `/frame` | `serve_frame` | The kiosk page (HA iframe + slideshow) |
| GET | `/photos.json` | `serve_photos` | Photo list for the frame JS |
| GET | `/images/{path}` | `serve_local_image` | Serve a local photo file |
| GET | `/gimg/{path}` | `serve_google_image` | Proxy a Google Ambient image |
| GET | `/config/`, `/config` | `serve_config_page` | The web config UI |
| GET | `/api/config` | `get_config` | Read effective config |
| POST | `/api/config` | `post_config` | Save config (validated) |
| GET | `/api/photos` | `serve_photos_list` | List stored photos |
| POST | `/api/photos` | `upload_photo` | Upload a photo |
| DELETE | `/api/photos/{name}` | `delete_photo` | Delete a photo |
| * | `/{tail:.*}` | `proxy_ha` | **Everything else → HA** (catch-all) |

### 4b. HA reverse proxy — `proxy_ha()`

The core trick. HA is proxied at the **root** (`/`), not a subpath, because
HA's frontend references assets at absolute paths (`/frontend_latest/...`,
`/static/...`) — a `/ha/` subpath would 404 them (black screen).

Flow:
1. Build the downstream path from `request.path` + query string.
2. `target = urljoin(ha_origin, downstream_path)`.
3. If the request is a WebSocket upgrade (`Upgrade: websocket`), hand off to
   `_proxy_websocket` (HA dashboards need live WS state).
4. Otherwise forward the request with `client.request(...)`, dropping
   hop-by-hop headers (`host`, `connection`, `content-length`,
   `transfer-encoding`).
5. Rewrite response headers via `_rewrite_response_headers`, return the body.
6. On any client/timeout error → **502** "Home Assistant unreachable".

### 4c. WebSocket proxy — `_proxy_websocket()`

Bidirectional pump: two `asyncio` tasks (`pump_s2c` server→client,
`pump_c2s` client→server) relay messages between the browser and HA. This is
why aiohttp (not stdlib `http.server`) — stdlib can't forward WebSockets, and
proxying HA without WS support silently breaks live dashboard updates.

### 4d. Header hygiene — `_rewrite_response_headers()` + `_rewrite_location()`

Two hard-won fixes (found by real-browser testing):
- **Drop** `content-length` (aiohttp recomputes), `content-encoding` (aiohttp
  already decompressed the body; forwarding it makes the browser try to
  inflate plain bytes → black screen), `transfer-encoding`, `connection`,
  `keep-alive`, `proxy-authenticate`.
- **Drop** `x-frame-options` and `frame-ancestors` when
  `strip_x_frame_options` is on — required so the kiosk page can embed HA in
  an iframe.
- **Rewrite** `Location` headers that point back at bare HA to be
  root-relative (so HA's login redirects stay inside the proxy).

### 4e. Frame page — `serve_frame()`

Reads `frame.html`, substitutes the three timing placeholders
(`{{IDLE_TIMEOUT_SECONDS}}`, `{{IDLE_FADE_SECONDS}}`,
`{{SLIDE_INTERVAL_SECONDS}}`) from the effective config, returns it as HTML.

### 4f. Photo serving

- `serve_photos()` — `{"photos": [{url, caption}...]}` from `source.list()`.
- `serve_local_image()` — serves a file under `photo_dir`, **path-traversal
  safe** (`resolve()` + `is_relative_to` check).
- `serve_google_image()` — for the Ambient source: the frame's `<img>` can't
  attach the OAuth bearer token, so the engine fetches the CDN bytes
  authenticated (`source.fetch_image_bytes`) and streams them back. 404 if
  the source isn't google-photos; 502 if the fetch fails.

### 4g. Config API

- `get_config()` — `store.public_state()`.
- `post_config()` — parse JSON, **type-validate** each field against
  `EDITABLE_FIELDS` (int/bool/str), then `store.save()`. Rejects bad types
  with 400. **Refuses writes (403) while the password must be changed**, so the
  operator is forced to set a real password first.
- `change_password()` — authenticated with the current password; sets a new
  one and clears the must-change flag.
- `auth_status()` — whether the password must be changed (for the UI).

### 4h. Auth guard — `kiosk_py/auth.py`

The config/upload service is LAN-reachable, so it's protected by HTTP Basic
auth against a kiosk-owned file (`/config/auth.json`).

- `AuthStore` — reads/writes the auth file. `verify()` checks credentials
  against a PBKDF2-HMAC-SHA256 hash (stdlib hashlib, 200k iterations). On the
  **first** successful login with the installer's plaintext temp password, it
  hashes it and clears the plaintext from disk (so the temp secret isn't left
  around), keeping `must_change: true`.
- `set_password()` — store a new password, clear the must-change flag.
- `parse_basic_auth()` — parse an `Authorization: Basic` header.
- `generate_password()` — random URL-safe password (no ambiguous chars).

`_require_auth()` in the server returns 401 (with `WWW-Authenticate`) on any
config/upload route if the credentials don't match. The frame page and photo
serving (`/frame/`, `/photos.json`, `/images`, `/gimg`) are **not** behind
auth — the kiosk itself must render them without a login.

### 4i. Photo upload / management

- `_safe_photo_name(filename)` — sanitize: basename only, image extension
  only, no path separators, safe chars only. Returns `None` if rejected.
- `_is_real_image(data)` — magic-byte sniff (JPEG/PNG/GIF/WebP/BMP) so we
  don't store arbitrary uploads served back as images.
- `upload_photo()` — multipart read, 20 MB cap, magic-byte check, safe name,
  write to `photo_dir`.
- `delete_photo()` — safe-name + path-traversal check, then unlink.
- `serve_photos_list()` — names of stored photos for the upload UI.

---

## 5. State machine — `kiosk_py/state.py`

The **transition geometry** (when idle starts, how long, whether input
cancels it) lives here as pure, deterministic logic — no I/O, driven by a
caller-supplied clock — so it's unit-testable without a browser.

- `State` — enum: `ACTIVE` / `IDLE`.
- `Transition` — dataclass: `current`, `next`, `reason` (`"input"` |
  `"idle-timeout"`), `idle_elapsed`.
- `KioskStateMachine`:
  - `__init__(idle_timeout_seconds)` — starts ACTIVE.
  - `reset(now)` — back to ACTIVE, idle clock starts at `now`.
  - `tick(now, input_occurred)` — the core. If input: reset idle clock, and
    if we were IDLE, return to ACTIVE (instant). If no input and ACTIVE and
    `now - idle_since >= timeout`: → IDLE. Returns a `Transition` only when
    state changes, else `None`.
  - `is_idle` — property.

**The browser's JS is a mirror of this same logic** (see §7). Keeping both in
one conceptual place lets tests pin the behavior.

---

## 6. Photo sources — `kiosk_py/sources.py`

### 6a. The interface

- `Photo` — dataclass: `url` (what the frame loads) + `caption`.
- `Source` — a `Protocol` with one method: `list() -> List[Photo]`. Everything
  the engine needs to know about "where photos come from" is this one method.
- `_http_json(url, ...)` — thin stdlib JSON GET/POST helper (returns `None`
  on any error).

### 6b. `LocalSource`

Walks `photo_dir`, returns a `Photo` per image file, URL-encoded under
`/images/*`. The engine serves those bytes at `/images/<relpath>` (same
origin, no CORS).

### 6c. `HttpSource`

Fetches a JSON catalog from a URL and yields the images it lists. Catalog
shape: `{"photos": [{"url": ..., "caption": ...}]}`. This is how a
self-hosted album (Immich/PhotoPrism proxy) plugs in without a vendor OAuth
dance.

### 6d. `AmbientAuth` + `AmbientSource` (Google Photos)

The Ambient API is the official successor to the deprecated Library API
(`photoslibrary.readonly` was removed after Mar 31 2025). Two things make it
different:

1. **Auth** is OAuth 2.0 for *TVs and Limited-Input Device* apps (device-code
   flow): the user sees a `user_code` + `verification_url`, approves from a
   phone/laptop, the app polls the token endpoint. Scope:
   `photosambient.mediaitems`.
2. **Rendering** needs the token in the request **header** — a bare `<img>`
   can't attach it, so the engine proxies each image.

`AmbientAuth`:
- `start_device_code()` — POST `/device/code` → `{user_code,
  verification_url, device_code, ...}`.
- `poll_for_token()` — poll `/token` with the device_code grant until the
  user authorizes; captures a refresh token on first success.
- `refresh_access_token()` — non-interactive refresh with the refresh token.
- `access_token()` — return cached token if unexpired, else refresh.

`AmbientSource`:
- `is_configured()` — client_id + refresh_token + device_id all present.
- `create_device()` — POST `/v1/devices` `{displayName}` → AmbientDevice.
- `get_device()` / `device_ready()` — poll until `mediaSourcesSet` is true
  (the user picked which albums to share, in the Photos app).
- `_list_page()` — GET `/v1/mediaItems?deviceId=..&pageSize=..&pageToken=..`.
- `list()` — paginate media items; each `Photo.url` is `/gimg/<url-encoded
  CDN baseUrl + =w1920-h1200>` (the engine proxy route).
- `fetch_image_bytes()` — fetch one CDN URL with the bearer token attached
  (used by `serve_google_image`).

### 6e. Factory — `get_source(config)`

Maps `config.photo_source` → a `Source` instance: `"http"` → `HttpSource`,
`"google-photos"` → `AmbientSource`, default → `LocalSource`. This is the
single place new sources register.

---

## 7. The kiosk page — `kiosk_py/frame.html`

The one URL Chromium loads. Two layers:

- `#ha-frame` — an `<iframe src="/">` (the proxied HA dashboard), z-index 1.
- `#frame-layer` — the photo slideshow (`#frame-img` + `#clock`), z-index 2,
  hidden by default.

The JS (an IIFE) is the **client-side state machine** that mirrors
`state.py`:

- `loadDeck()` — GET `/photos.json`, build the slide deck.
- `advanceSlide()` — cycle to the next photo, cache-bust with `?t=Date.now()`.
- `startSlideshow()` — load deck, show first slide, set `setInterval`s for
  slide rotation and the clock.
- `setLayer(nextState)` — the visual flip: idle → fade HA out, photos in,
  show clock; active → reverse. Uses CSS `transition: opacity
  {{IDLE_FADE_SECONDS}}s`.
- `onInput()` — on any real input event, reset the idle clock; if idle, flip
  back to active **instantly**.
- `startClock()` — the idle timer: if active and `now - idleSince >=
  IDLE_TIMEOUT`, go idle.

Input events listened for: `mousemove, mousedown, touchstart, touchmove,
keydown, wheel, click, dblclick`. The idle check runs every 500 ms.

**The split:** `state.py` defines the *contract* (timing, transitions) in
testable Python; `frame.html` implements the *same* logic in JS because the
browser is the only thing that sees real input events. They must stay in
sync — the tests pin the Python side.

---

## 8. Web config UI — `kiosk_py/config.html`

A single-page form. `load()` GETs `/api/config` and fills the fields;
`save()` POSTs the JSON to `/api/config`. Photo management: `uploadPhoto()`
POSTs a multipart file to `/api/photos`; `loadPhotos()` lists them;
`deletePhoto()` DELETEs by name. No framework — plain fetch/XHR.

---

## 9. Supervisor — `scripts/supervisor/kiosk-supervisor.sh`

The deliberately-thin host process. Three jobs:

1. `wait_for_engine()` — poll `ENGINE_READY_URL` (default
   `http://localhost:8080/frame/`) until 200, up to `ENGINE_WAIT_MAX` (120s).
   If it never comes up, launch Chromium anyway (it'll retry).
2. `run_kiosk()` — launch Chromium in kiosk mode at `ENGINE_URL` with the
   flags that quiet first-run/error dialogs.
3. `main()` — wait for engine, then an infinite loop: run Chromium, and on
   exit restart it after 3s. The kiosk never goes dark.

It does **not** do idle detection (client-side JS), proxying, or config
(engine). It's display + process supervision only.

---

## 9b. Installer — `scripts/install.sh`

The one-command installer, pipeable from GitHub (`curl -fsSL … | sudo bash`).
Self-contained: it fetches companion files from the repo at install time
rather than assuming a checkout around it.

Config (all env-overridable): `KIOSK_USER` (default `kiosk`), `ENGINE_PORT`
(`8080`), `PHOTO_HOST_DIR` / `CONFIG_HOST_DIR` / `ENGINE_SRC_DIR` /
`INSTALL_DIR` / `SUPERVISOR_BIN` (all default under `~kiosk`), `IMAGE`
(GHCR), `REPO_RAW` / `REPO_GIT` (where companion files + source come from),
`MODE` (`container` | `source`).

Functions:
- `parse_args()` — `--from-source` switches to venv mode; `--help`.
- `fetch()` — download a repo file into `$INSTALL_DIR` via curl or wget.
- `check_prereqs()` — must be root; systemd present; for container mode:
  docker + compose + curl/wget; for source mode: git + python3.
- `install_user()` — create the `kiosk` user if absent; mkdir the home
  subdirs; `chown -R kiosk:kiosk ~kiosk` (the user owns everything).
- `install_engine()` — **container**: `docker pull $IMAGE`, fetch
  `docker-compose.yml`, write a `docker-compose.override.yml` pinning the
  image, ports (8080), env seeds, and the config/photos volume mounts, then
  `docker compose up -d`. **source**: `git clone --depth 1`, create a venv,
  `pip install -r requirements.txt`.
- `install_engine_service()` — source mode only: write a systemd unit that
  runs `apps/app.py` as the kiosk user, enable it.
- `install_supervisor()` — fetch the supervisor script + systemd unit, install
  the script to `~kiosk/bin`, `chown` it to kiosk, and write the unit to
  `/etc/systemd/system/` with `User/Group/ExecStart` substituted to the kiosk
  user + home path. Enable it.
- `setup_photos()` — ensure the photos/config dirs are kiosk-owned.
- `main()` — run the steps in order, then print the "configure from a laptop"
  instructions with the kiosk's LAN IP.

**Key design points:**
- Everything kiosk lives under `~kiosk` and is owned by `kiosk` — not
  `/opt/kiosk` or `/usr/local/bin`.
- The systemd unit **must** live in `/etc/systemd/system/` (systemd requires
  it), but it runs the supervisor from `~kiosk/bin` as the kiosk user.
- The engine container mounts `~kiosk/config` → `/config` and
  `~kiosk/photos` → `/photos`, so the web config service persists to the
  kiosk user's home.

## 9c. Uninstaller — `scripts/uninstall.sh`

The inverse, in reverse order:
1. Stop + disable + remove the supervisor systemd unit and script.
2. Remove the engine container + image (if docker present) and the source-mode
   systemd unit.
3. If `REMOVE_DATA=1`, delete the photos + config dirs.
4. `userdel -r` the kiosk user (removes the home + everything under it).

## 9d. Supervisor systemd unit — `scripts/supervisor/ha-photo-kiosk.service`

Runs the supervisor as the `kiosk` user on the display session:
- `After=docker.service network-online.target` — engine should be up first
  (the supervisor also waits for it itself).
- `User/Group=kiosk`, `ExecStart` → the supervisor script (path substituted
  by the installer).
- `Environment=DISPLAY=:0` — the physical display (or Xvfb on headless).
- `Restart=always` — belt-and-braces on top of the script's own Chromium
  restart loop.
- `StandardInput=tty` + `TTYPath=/dev/tty7` — a clean TTY so Chromium can
  grab input.

---

## 10. Data flow (end to end)

```
User touches screen
   │  (real input event)
   ▼
frame.html onInput() ──► resets idle clock; if idle, flip to ACTIVE
   │
   │  (no input for IDLE_TIMEOUT)
   ▼
frame.html startClock() ──► setLayer("idle") ──► fade HA out, photos in
   │
   ▼
frame.html advanceSlide() ──► GET /photos.json ──► source.list()
   │                                              │
   │                                              ├─ LocalSource: walk photo_dir
   │                                              ├─ HttpSource: fetch catalog
   │                                              └─ AmbientSource: mediaItems.list
   ▼
<img src="/images/x.jpg">  or  <img src="/gimg/<encoded>">
   │                              │
   ▼                              ▼
serve_local_image()        serve_google_image() ──► fetch_image_bytes() (token)
   │                              │
   └──────────────► bytes back to the frame
```

And the HA side:

```
Browser ──► /frame/ (iframe src="/")
                │
                ▼
          proxy_ha() ──► HA at ha_origin
                │            │
                ├─ HTTP: forward + rewrite headers (drop XFO, content-encoding)
                └─ WS:    bidirectional pump (live dashboard)
```

---

## 11. Config flow (web service)

```
Laptop ──► GET /config/ ──► serve_config_page() ──► config.html
              │
              ├─ GET /api/config ──► store.public_state() (effective values)
              │
              └─ POST /api/config ──► post_config() ──► type-validate ──► store.save()
                                                              │
                                                              ▼
                                                    /config/kiosk.json (merge)
                                                              │
                                                              ▼
                                              next engine start: effective_config()
```
