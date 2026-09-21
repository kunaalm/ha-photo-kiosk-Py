# Architecture

A complete design, implementation, and testing walkthrough of **ha-photo-kiosk**
— Home Assistant dashboard + digital photo frame on one display.

> For the code-level design and implementation audit (what each file and
> function does, the data flow), see [DESIGN.md](DESIGN.md). This document is
> the high-level mental model + testing deep dive.

---

## 1. The problem & the mental model

A kiosk that shows a Home Assistant dashboard and *also* acts as a photo frame
needs to switch between two states based on user activity. The naive approach —
restart Chromium pointed at a different URL — loses the HA login and flickers.
This project's core decision is:

> **The browser is a dumb renderer that loads ONE URL once and never
> restarts. A state machine behind it decides what's shown.**

The two states:

```
                  touches / moves / key
   ┌────────┐     ─────────────────▶    ┌────────────┐
   │ ACTIVE │  (Home Assistant shown)   │    IDLE    │ (photo frame)
   └────────┘     ◀─── idle N sec ────  └────────────┘
```

This is the same "attract mode" pattern used by digital signage and conference
room displays — industry-standard, and it keeps the device software trivial.

## 2. Components

```
┌─────────────────────────────── HOST (the kiosk box) ───────────────────────────┐
│                                                                               │
│  ┌─────────────────────────────┐        ┌──────────────────────────────────┐  │
│  │  ENGINE  (Docker container) │        │  SUPERVISOR  (systemd + script)  │  │
│  │  • reverse-proxies HA       │        │  • waits for engine to be up     │  │
│  │  • serves the kiosk page    │        │  • launches Chromium at /frame/  │  │
│  │  • serves photos            │        │  • restarts Chromium on crash    │  │
│  │  • web config + photo upload│        └──────────────────────────────────┘  │
│  │  • photo sources (pluggable)│                   │                          │
│  └─────────────────────────────┘                   ▼ (owns the framebuffer)    │
│              │  HTTP :8080                    ┌──────────────┐                  │
│              ▼                                │   Chromium   │  one tab at      │
│                                   ┌─────────► │   kiosk      │  localhost:8080  │
│                                   │           └──────────────┘   /frame/       │
│  ┌─────────────────────────────────────────────────────────────┐                │
│  │  HOME ASSISTANT  (elsewhere on the network, :8123)          │ ⇐─ proxied     │
│  └─────────────────────────────────────────────────────────────┘                │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  INSTALLER  (scripts/install.sh — the deployment component)             │ │
│  │  • one-command curl|bash, no git, no clone                              │ │
│  │  • installs the fixed prerequisite set (Docker, X, Chromium, Openbox)  │ │
│  │  • configures + starts every service (engine, supervisor, gphotos)     │ │
│  │  • turns a clean Debian box into a running kiosk in one shot           │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
```

**The split is deliberate.** The engine (all the smarts) is a container: pure
network + compute, zero display dependency — so it's fully testable headless and
dependency-pinned. The supervisor (display + browser + process supervision) runs
on the host because it must own the physical framebuffer. This is the
edge/device-agent pattern: a deliberately thin client on the device, logic in a
managed service.

**The installer is the third core component** — the deployment layer that turns
a bare Debian box into the host above. It is not a convenience script bolted on
after the fact; it is the primary way the product is delivered (one command, no
git, no clone), and it owns the contract that a *clean* box becomes a *running*
kiosk. See [§6 Installer architecture](#6-installer-architecture).

## 3. Key design decisions (and why)

### 3a. Reverse proxy instead of an iframe or browser restart

Real Home Assistant sends `X-Frame-Options: SAMEORIGIN`, which makes a
cross-origin `<iframe>` fail outright (verified against HA 2026.x). So the
engine **reverse-proxies** HA behind itself and strips that header, allowing the
kiosk page to embed HA in an iframe. Browser restarts are avoided entirely, so
the HA login survives and flipping back to the dashboard is instant.

Two hard-won proxy details (found by real-browser testing, see §5):

- **Root-path proxying.** HA's frontend references assets at absolute paths
  (`/frontend_latest/...`, `/static/...`). Proxying under a `/ha/` subpath made
  the browser request `/frontend_latest/...` (not `/ha/frontend_latest/...`),
  which the engine didn't proxy → 404 → black screen. So HA is proxied at the
  **root**, and the kiosk frame page lives at `/frame/`.
- **Response-header hygiene.** aiohttp auto-decompresses response bodies, so the
  proxy drops `Content-Encoding` (forwarding it makes the browser try to inflate
  already-plain bytes) and drops HA's frame-blocking headers.

### 3b. Idle detection is client-side JavaScript

The served kiosk page watches real input events (`mousemove`, `touch`, `key`).
When idle for N seconds it fades the HA iframe out and shows the photo
slideshow; any input flips instantly back. No polling round-trip, no X-server
dependency, no host-side idleness watcher.

### 3c. Photo sources are pluggable

A tiny `Source` protocol (`list() -> List[Photo]`) means new sources are
mechanical additions. Implemented:

| Source | What it is | How the frame loads it |
|---|---|---|
| `local` | A directory of images | Engine serves them at `/images/*` (same origin) |
| `http` | Any URL/feed returning a JSON photo catalog | Direct URLs |

### 3d. Web config + upload, not filesystem edits

A browser-accessible config service (`/config/`) sets HA URL, idle timeout, and
photo source, and lets you **upload photos** — so a hobbyist never needs to ssh
into the kiosk or touch its filesystem. Google credentials are env-only (they're
secrets; the config store is deliberately whitelisted to non-secret fields).

## 4. Implementation map

```
apps/app.py                # entry point — CLI bootstrap (ENV -> Config -> KioskServer)
kiosk_py/
  config.py                 # dataclass Config, built from env (12-factor)
  config_store.py           # persistent JSON config; file overrides env; merge-on-save
  state.py                  # KioskStateMachine — the idle/active transition logic
  sources.py                # Source protocol: Local / Http / GooglePhotos + factory
  server.py                 # the HTTP app: HA reverse-proxy, /frame/, /images, /config, /api
  frame.html                # the kiosk page — iframe(HA) + slideshow + idle JS
  config.html               # the web config/upload page
scripts/supervisor/
  kiosk-supervisor.sh       # wait for engine -> launch Chromium -> restart on exit
  ha-photo-kiosk.service    # systemd unit (Restart=always, kiosk user, display)
tests/                      # unit tests (headless, CI)
  test_state.py             # state machine transitions (exact timestamps)
  test_sources.py           # Local/Http listing + factory
  test_proxy.py             # header rewriting (frame-blocking, encoding, Location)
  test_config_store.py      # persistence + merge-on-partial-save
  test_google_source.py     # Google source (mocked no-network)
  test_upload.py            # upload validation (magic bytes, safe names)
tests/vm-harness/           # real-browser tests (manual; need KVM + real HA)
scripts/install.sh / uninstall.sh   # the installer — one-command curl|bash deploy (see §6)
Dockerfile / docker-compose.yml
.github/workflows/ci.yml, docker-publish.yml
```

### The state machine (`state.py`)

Deterministic and pure — no I/O, driven by a caller-supplied clock:

```
tick(now, input_occurred) -> Transition | None
```

- On input: reset the idle clock; return to ACTIVE if we were IDLE (instant).
- Without input: once `now - idle_since >= timeout`, transition to IDLE.

The browser page's JS is a mirror of this same logic, kept in one conceptual
place so tests pin the behavior.

## 5. Testing strategy

Two layers, because the failure modes they catch are different.

### Layer 1 — Headless unit tests (CI-automated)

`python -m pytest tests/` — 48 tests, no display, no Docker. Covers the state
machine, source listing/filtering, proxy header rewriting, config persistence,
upload validation, and Google source (mocked). Fast, deterministic, runs on
every push.

### Layer 2 — Real-browser VM harness (manual, the important one)

`tests/vm-harness/` boots a QEMU/KVM VM with Xvfb + real Chromium against a real
Home Assistant instance, drives the actual ACTIVE→IDLE→ACTIVE toggle, and
captures screenshots for inspection. It needs KVM + HA, so hosted CI can't run
it — but it has **caught every real bug this project hit**, where unit tests
could not:

| Bug | Symptom (unit tests said green) | Root cause | Fix |
|---|---|---|---|
| `/ha/` subpath | black iframe | HA absolute-path assets not proxied | proxy at root, frame at `/frame/` |
| `Content-Encoding: deflate` | black screen | aiohttp decompressed body, proxy kept header | drop `content-encoding` |
| Brotli (no package) | 502 on proxied HA | HA serves Brotli-encoded frontend | add `Brotli` dep |

Each was invisible to static analysis and unit tests because the proxy's *output*
was syntactically fine — only a real browser render exposed the failure.

The harness also verified: the supervisor restarting Chromium on kill, and the
web config + photo upload round-tripping through the actual UI (driven via
Chrome's DevTools Protocol).

## 6. Installer architecture

The installer (`scripts/install.sh`) is a core component: it is the primary
delivery mechanism and owns the contract that a **clean** Debian box becomes a
**running** kiosk. It is deliberately self-contained — one `curl | sudo bash`,
no git, no clone — and fetches its companion files (compose, supervisor,
systemd units) from pinned raw URLs at install time.

### 6a. The clean-box contract (R19)

The installer must work on a fresh Debian / Raspberry Pi OS box with **none**
of the runtime present — no Docker, no X, no Chromium, no git. It installs
every prerequisite rather than assuming one exists. This is the primary use
case and is validated on a clean box, not a pre-provisioned one.

### 6b. Fixed prerequisite set (R19a)

The package list is fixed and deterministic — not ad-hoc or discovered at
runtime — so a clean box always yields the same working kiosk:

| Layer | Packages |
|---|---|
| Container runtime | Docker Engine (official repo): `docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` |
| Graphical stack | `xorg xserver-xorg xinit openbox chromium unclutter curl netcat-openbsd` |
| Google Photos sync | `rclone` |

### 6c. Install phases

The installer runs in a fixed order, each phase idempotent (safe to re-run):

1. **Prereqs** — root check, systemd present, then `install_docker()` (installs
   Docker Engine from the official apt repo if absent; starts the daemon).
2. **Kiosk user** — creates a nologin `kiosk` service account; everything kiosk
   lives under its home (`~/photos`, `~/config`, `~/bin`, `~/engine`).
3. **Engine** — pulls the published container and runs it via compose (or, with
   `--from-source`, clones the repo and installs a Python venv).
4. **Supervisor** — installs the supervisor script + systemd unit.
5. **GUI stack** — installs X/Chromium/Openbox, configures getty@tty1 autologin
   for the kiosk user, and writes the Openbox autostart that launches the
   supervisor. A kiosk is a physical display device, so this is mandatory.
6. **Photos dir** — creates + owns `~/photos`.
7. **Firewall** — ufw, opened only for SSH + the engine port (best-effort).
8. **Config auth** — generates a random config-service password, forces a
   change on first login.
9. **Google Photos sync** — installs rclone + the sync systemd timer/path.

### 6d. Configure AND start (R19b)

Installing packages is not enough — the installer **starts** every service so
the box is a working kiosk immediately, not after a manual start or reboot:

- engine (container via compose, or systemd in source mode)
- supervisor → X + Chromium on the display
- gphotos sync timer + path watcher

### 6e. The display bring-up path

The supervisor systemd unit runs `xinit /usr/bin/openbox-session -- :0 vt7`
(no display manager). Openbox's autostart launches the supervisor, which opens
Chromium in `--kiosk` at the engine's `/frame/` page. The kiosk user is in the
`tty` group to grab the vt7 session.

### 6f. Uninstall

`scripts/uninstall.sh` removes everything the installer created: the engine
container/venv, supervisor + systemd unit, GUI autologin + openbox autostart,
gphotos sync units, and (optionally) the kiosk user and data.

## 7. Install & config flow

1. `scripts/install.sh` — pulls the published container, creates the kiosk user,
   installs the supervisor systemd unit, sets up the photos dir. Points the
   user to configure from a **laptop**, not the kiosk itself.
2. Laptop opens `http://<kiosk-ip>:8080/config/` — sets HA URL, idle timeout,
   photo source; uploads photos.
3. Supervisor launches Chromium at `/frame/` on boot; the engine multiplexes.

`--from-source` installs the engine as a Python venv instead of a container
(for hacking without Docker).

## 8. Known limitations (honest)

- **Google Photos** is shown by syncing to the photo folder (no cloud API) —
  see `docs/google-photos.md`.
- **VM harness is manual** (not CI) — it needs KVM + a real HA instance.
- The photo-frame JS layer's rendering is verified via screenshots; the
  background HA session persistence across engine restarts isn't separately
  tested.
- **Config service auth is Basic auth over plain HTTP** — the password is
  sent base64-encoded, not encrypted. Fine on a trusted home LAN; use an SSH
  tunnel or HTTPS reverse proxy if you need it encrypted on an untrusted
  network.