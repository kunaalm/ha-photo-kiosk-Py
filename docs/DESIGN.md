# Design

The high-level design of **ha-photo-kiosk** — what it is, how it's put
together, and the decisions that shape it. For the full implementation and
testing walkthrough, see [ARCHITECTURE.md](ARCHITECTURE.md).

## The problem

A kiosk that shows a Home Assistant dashboard and *also* acts as a photo frame
needs to switch between two states based on user activity. The naive approach —
restart Chromium pointed at a different URL — loses the HA login and flickers.

## The core idea

> **The browser is a dumb renderer that loads ONE URL once and never
> restarts. A state machine behind it decides what's shown.**

The kiosk has two states:

```
                  touches / moves / key
   ┌────────┐     ─────────────────▶    ┌────────────┐
   │ ACTIVE │  (Home Assistant shown)   │    IDLE    │ (photo frame)
   └────────┘     ◀─── idle N sec ────  └────────────┘
```

This is the "attract mode" pattern used by digital signage and conference-room
displays. It keeps the device software trivial.

## Two components

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
└───────────────────────────────────────────────────────────────────────────────┘
```

**The split is deliberate.** The engine (all the smarts) is a container: pure
network + compute, zero display dependency — so it's fully testable headless and
dependency-pinned. The supervisor (display + browser + process supervision) runs
on the host because it must own the physical framebuffer. This is the
edge/device-agent pattern: a thin client on the device, logic in a managed
service.

## Key decisions

- **Reverse-proxy HA instead of an iframe or browser restart.** HA sends
  `X-Frame-Options: SAMEORIGIN`, which blocks a cross-origin iframe. The engine
  proxies HA behind itself and strips that header, so the kiosk page can embed
  it. No browser restart means the HA login survives and returning to the
  dashboard is instant.
- **Idle detection is client-side JavaScript.** The kiosk page watches real
  input events and flips layers when idle. No polling, no X-server dependency,
  no host-side watcher.
- **Photo sources are pluggable.** A tiny `Source` interface (`list() ->
  List[Photo]`) makes new sources mechanical additions. Implemented: local
  directory, Google Photos (Ambient API), and an HTTP catalog.
- **Web config + upload, not filesystem edits.** A browser-accessible config
  service sets HA URL, idle timeout, and photo source, and lets you upload
  photos — so a hobbyist never needs to ssh into the kiosk.
- **Everything lives under a dedicated `kiosk` user's home.** The installer
  creates a `kiosk` user and puts all runtime state (photos, config, engine,
  supervisor) under `~kiosk`, owned by that user — not scattered in system dirs.

## Install & config flow

1. `curl -fsSL …/scripts/install.sh | sudo bash` — pulls the published
   container, creates the `kiosk` user, installs the supervisor, sets up the
   photos dir. Points you to configure from a **laptop**, not the kiosk.
2. Laptop opens `http://<kiosk-ip>:8080/config/` — sets HA URL, idle timeout,
   photo source; uploads photos.
3. Supervisor launches Chromium at `/frame/` on boot; the engine multiplexes.

`--from-source` installs the engine as a Python venv instead of a container
(for hacking without Docker).

## Known limitations

- **No auth on the web config/upload service.** It binds to the LAN so a laptop
  can reach it; anyone on your network can too. Fine on a trusted home LAN, not
  on an untrusted network.
- **Google Photos is unit-tested but not live-verified** (needs real OAuth
  credentials) — see [google-photos.md](google-photos.md).
