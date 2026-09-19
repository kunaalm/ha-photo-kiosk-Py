# Testing

The engine is designed so its core (state machine, photo sources, proxy header
handling) is **headless-testable** — no display, no Docker, no browser. That's
the unit-test layer. But the whole point of a kiosk is that a *browser renders
it*, and unit tests can't prove that. So there's a second, real-browser layer.

## Level 1 — Unit tests (headless, CI-automated)

```bash
python3 -m pytest tests/
```

Covers: the idle/active state machine (exact timestamps, deterministic), photo
source listing/filtering, and proxy response-header rewriting (frame-blocking
strip, Location rewrite, Content-Encoding drop). 17 tests, no I/O, no display.

## Level 2 — Real-browser VM harness (manual, the important one)

A QEMU/KVM VM with Xvfb + real Chromium, pointed at a real Home Assistant
instance, driving the actual two-state toggle. This is the test level that
catches what unit tests structurally cannot: real Chromium rendering, real HA
frontend, real proxy behavior end-to-end.

### What it verifies

1. Engine serves the frame page (`/frame/`) and `photos.json`.
2. Chromium loads the frame page; the HA iframe **renders** (ACTIVE state).
3. After the idle timeout, the frame page fades to the **photo slideshow** (IDLE).
4. Simulated input (mouse move) flips **instantly back to HA** (ACTIVE).
5. Idle again → photo frame again.

Each state is captured as a screenshot and inspected (not just asserted on
byte size — a human/vision check confirms it's actually HA vs. a photo).

### How to run

1. **Provision the VM** — `tests/vm-harness/user-data` is a cloud-init config
   that installs Xvfb, Chromium, the engine, and 3 test photos. Build the seed
   ISO and boot a QEMU/KVM VM (see the harness README for the exact commands).
2. **Point HA at the VM** — the VM reaches the host via `10.0.2.2` (slirp
   user networking). The engine is configured with `HA_URL=http://10.0.2.2:8123`.
3. **Run the driver** — `tests/vm-harness/run-kiosk-test.sh` inside the VM
   starts Xvfb + engine + Chromium, drives the toggle, and captures screenshots.

### Prerequisites for a meaningful run

- **HA must be fully onboarded** (account created, not in first-boot onboarding
  mode). The onboarding SPA doesn't render headless; a real kiosk points at a
  configured HA. Complete onboarding via the API or storage before testing.
- **Chromium needs `--no-first-run`** or it shows a Terms-of-Service dialog
  that blocks the page (a classic kiosk gotcha).

## Real bugs this harness caught (invisible to unit tests)

Both were found only by running the actual generated proxy against real HA in
a real browser — the exact failure mode the harness exists to surface.

### Bug 1: `/ha/` subpath broke HA's absolute-path assets

Proxying HA under `/ha/` made the browser request `/frontend_latest/...` (not
`/ha/frontend_latest/...`), which the engine didn't proxy → 404 → black iframe.
**Fix:** proxy HA at the root `/`, serve the frame page at `/frame/`. HA's
absolute paths work untouched; the frame page is inline HTML with no external
assets. (Classic reverse-proxy subpath problem.)

### Bug 2: `Content-Encoding` double-decode → black screen

aiohttp auto-decompresses response bodies, but the proxy forwarded the original
`Content-Encoding: deflate` header with the already-decompressed body. The
browser tried to inflate plain bytes → black screen. Direct HA rendered; proxied
HA was black; the HTML was byte-identical — only a real browser revealed it.
**Fix:** drop `content-encoding` on proxied responses (aiohttp already decoded).

### Bug 3 (found earlier, same class): Brotli

HA serves its frontend with Brotli compression; aiohttp can't decode it without
the `Brotli` package → 502 on proxied HA. **Fix:** add `Brotli` to requirements.

## Coverage gaps (honest)

- The VM harness is **manual** (not CI-automated) — it needs KVM + a real HA
  instance, which a hosted runner can't provide. It's the documented repro
  path, run when the proxy or frame-page behavior changes.
- No test exercises a *logged-in* HA dashboard (the harness proves the login
  page renders, which exercises the same frontend machinery; the dashboard
  itself needs a session cookie the harness doesn't set up).
