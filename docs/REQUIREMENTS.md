# Kiosk Requirements

**Project:** HA Photo Kiosk (Python)
**Living document.** Requirements change; this is the source of truth for *what*
the product must do and *why*. Every new feature starts here: state the
requirement, get agreement, then design, then build. See
[How to use this document](#how-to-use-this-document).

---

## 1. Purpose

A wall-mounted, hands-off two-state kiosk on a light Debian / Raspberry Pi OS
box:

1. **Active** — shows a Home Assistant dashboard (interactive).
2. **Idle** — after a timeout, becomes a digital photo frame cycling photos.

The box is configured **from a PC, not the kiosk screen**, and is simple enough
for a hobbyist to deploy with one command and leave running.

## 2. Users

| User | Goal |
|------|------|
| Homelab owner (primary) | A cheap screen shows HA + personal photos, set up once |
| Hobbyist with a spare SBC | Same, without needing to read a lot of docs |

No shared/multi-user product; no managed cloud **product** — this is a
personal device. That matters: it excludes product-grade integrations that
assume a fleet to manage.

## 3. Environment & constraints

- **Host OS:** light Debian or Raspberry Pi OS. Not Ubuntu, not snap-dogmatic.
- **Compute:** low-power SBC acceptable; the frame and dashboard are driven by
  a browser (Chromium).
- **Network:** local LAN. The kiosk reaches Home Assistant and (optionally)
  Google Photos over the internet.
- **Maintenance:** the owner is technical (a CTO) but the *deployment* is
  meant to be married-and-simple.
- **Credentials / security:** there are secrets (HA token, config password).
  They must never be committed or written into docs/notes.

## 4. Functional requirements

Prioritized: **M**andatory, **S**hould, **C**ould.

### 4.1 Two-state kiosk (M)
- R1. Display a Home Assistant dashboard while the user is interacting.
- R2. After `idle_timeout` seconds of no input, switch to a photo frame.
- R3. The photo frame cycles photos on `slide_interval` seconds.
- R4. Resuming interaction returns to the HA dashboard without a browser
  restart. **The engine must keep the browser alive across state changes**
  (proxy the HA dashboard through the engine; do not reload Chromium).

### 4.2 Photo sources (M)
- R5. **Local directory** source (default): serve photos from a folder that the
  owner can populate by (a) uploading via the web config, or (b) mounting a
  volume.
- R6. **HTTP catalog source**: an optional URL returning a photo list/feed.
- R7. **Google Photos**: NOT read directly in-app. The owner opts in to a
  **built-in sync mechanism** (rclone on the host) that pulls their Google
  Photos into the local photo folder on a schedule. The engine never talks to
  Google.

See [5. Non-goals](#5-non-goals) for why Google is sync-not-API.

### 4.3 Web config service (M)
- R8. Served by the engine; reachable from the LAN so configuration happens
  from a PC, not the kiosk screen.
- R9. Configure: HA URL, photo source, photo directory, idle timeout, slide
  interval, idle fade.
- R10. Manage photos: upload, list, delete.
- R11. **Google Photos sync section:** enable/disable, rclone remote name,
  source path, trigger "Sync now", and show sync status (last run / error /
  count).
- R12. All config/management endpoints are **basic-auth protected**.

### 4.4 Security & hardening (M)
- R13. Installer generates a random config-service password.
- R14. **Forced password change on first login** (a `must_change` flag).
- R15. Engine runs as a **non-root** user; Chromium runs **with sandbox**
  enabled.
- R16. The `kiosk` user is a nologin service account.
- R17. Optional host firewall (ufw) opened only for needed ports.
- R18. No credentials in the codebase, docs, or notes — reference by location.

### 4.5 Installation & deployment (M)
- R19. **Works on a clean Debian install.** The installer must take a fresh
  Debian / Raspberry Pi OS box (no Docker, no X, no Chromium, no git) and
  turn it into a working kiosk — installing every prerequisite it needs
  rather than assuming they exist. This is the primary use case and must be
  validated on a clean box, not a pre-provisioned one.
- R19a. **All prerequisites are installed by the installer; the package set
  is fixed and complete.** The installer installs the full, deterministic
  set of packages the kiosk needs — Docker Engine (official repo), the
  graphical stack (X server, Chromium, Openbox, xinit, unclutter), and
  supporting tools (curl, netcat) — plus rclone for Google Photos sync. It
  does not leave any prerequisite to the user or assume one is present. The
  exact package list is fixed in the installer (not ad-hoc or discovered at
  runtime) so a clean box always ends up with the same working kiosk.
- R19b. **The installer configures and starts every required service.** After
  install the kiosk is *running*, not just configured: the installer enables
  **and starts** the engine, the display supervisor (X + Chromium), and the
  Google Photos sync timer/path. It does not leave the user to start services
  by hand or wait for a reboot to bring the kiosk up. A clean box is a working
  kiosk immediately after install.
- R20. One-command install: `curl -fsSL …/scripts/install.sh | sudo bash`.
- R21. Default install runs the **published container** image; a Python
  stdlib venv is an install-time option (`--from-source`).
- R22. Installer completes setup by directing the user to the web config.
- R23. Real GitHub releases with versioned container tags.
- R24. A host supervisor keeps Chromium alive.
- R25. Clean uninstall script removes everything it created.

### 4.6 Hobbyist-friendly presentation (S)
- R26. README is demo-first and short.
- R27. Real photographs everywhere in docs/demo — **no gradient/pattern
  stand-ins**.
- R28. Architecture and design docs for auditing generated code.

## 5. Non-goals

Explicitly *out of scope* — writing these down prevents scope creep and
regression:

- **NG1. Google Photos Ambient API / Picker API in-app.** These are product
  integrations for commercial "ambient display" devices (a fleet to manage, an
  OAuth client, a consent/device flow). This is a personal homelab box; the
  correct mechanism is **sync-to-folder** (R7). Rejected and removed.
- **NG2. Apple Photos.** No public API; not built.
- **NG3. Ubuntu / snap-first.** Target is light Debian / Raspberry Pi OS.
- **NG4. Multi-user / fleet / cloud account management.** Not a product.
- **NG5. Enterprise SSO, Active Directory, etc.**
- **NG6. A Photo Cloud API at all inside the app.** Cloud photos enter via
  local files only.
- **NG7. Running Chromium unsandboxed / as root.**
- **NG8. Anything that breaks the "configure from a PC, not the kiosk screen"**
  model.

## 6. Non-functional requirements

- **NFR1. Operability:** set-and-forget; survives reboots (systemd).
- **NFR2. Offline photo frame:** once synced/uploaded, the frame works without
  internet.
- **NFR3. Config persistence:** config survives restarts; stored on the host,
  shared with the engine via a mounted volume.
- **NFR4. Testability:** a unit test suite (pytest) that runs in CI, plus a VM
  harness for real-browser validation.
- **NFR5. Maintainability:** generated code is documented (docstrings +
  design doc) so it can be audited.

## 7. Current implementation status

| Requirement | Status |
|-------------|--------|
| R1–R4 two-state kiosk | ✅ implemented |
| R5 local source | ✅ implemented |
| R6 HTTP source | ✅ implemented |
| R7 Google Photos sync | ✅ implemented (rclone, `0f4dd62`) |
| R8–R11 config service | ✅ implemented |
| R12 basic auth | ✅ implemented |
| R13–R18 security/hardening | ✅ implemented |
| R19–R25 install/deploy | ✅ implemented (released `v0.1.0`; installer auto-installs Docker + the full GUI stack: X server, Chromium, Openbox, autologin). **R19 validated on a clean Debian 12 VM** — real `curl|bash` from a box with no Docker/X/Chromium/git, booted to a working kiosk showing a real photo. **R19b: installer now starts the engine, supervisor, and gphotos timer/path** |
| R26–R28 hobbyist presentation | ✅ implemented |
| Test suite + VM harness | ✅ 48 tests; VM harness in `tests/vm-harness/` |

## How to use this document

This is the **development process**, not just documentation:

1. **New feature / change → add or update a requirement first.** State what and
   why, and get agreement, *before* writing code.
2. **Design against the requirement** (see `docs/DESIGN.md` for architecture),
   then implement, then test.
3. **Non-goals are guardrails.** If a proposal touches a listed non-goal,
   stop and reconsider — it was likely rejected for a reason documented here.
4. **Keep this doc current.** When a requirement is implemented, flip its
   status; when scope is rejected, add it to non-goals with the reason.

A requirement that isn't written here (or explicitly deferred) is **not
approved to build**.