# How to set up the HA Photo Kiosk

This guide walks through installing and configuring the kiosk on a Debian-based
box, using the **published container** (the default) and the **web config
service** to complete setup. All screenshots are real captures from a test VM
running the actual kiosk against a real Home Assistant instance.

## What you'll end up with

A Chromium kiosk that shows your **Home Assistant dashboard** when active, and
flips to a **photo frame** when the display is idle. The browser loads one URL
once and never restarts — the engine (a container) multiplexes HA + photos, and
a thin host supervisor keeps Chromium alive.

## 1. Prerequisites

- A Debian-based box (Ubuntu/Debian/Raspberry Pi OS) with **Docker** installed.
- A reachable **Home Assistant** instance (e.g. `http://192.168.20.12:8123`).
- Root/sudo access.

> No Docker? You can still install the engine as a Python venv on the host
> with `bash install.sh --from-source` — see the README.

## 2. Install

Download the repo and run the installer. It pulls the published container from
GHCR (no local build), creates the `kiosk` user, installs the supervisor +
systemd unit, and sets up the photos dir.

```bash
git clone https://github.com/kunaalm/ha-photo-kiosk-Py.git
cd ha-photo-kiosk-Py
sudo bash install.sh
```

The installer prints a summary and — importantly — **tells you the next step is
the web config service**:

```
=========================== INSTALL COMPLETE ========================

  NEXT STEP — not done until you configure it:
  Open the WEB CONFIG SERVICE in a browser:

      http://localhost:8080/config/
```

## 3. Configure via the web config service

Open `http://localhost:8080/config/` in a browser on the kiosk box. This is
where you complete the setup — set your HA URL, photo source, and timings.

![Web config service](images/vm-test/howto-config-full.png)

Set:
- **Home Assistant URL** — your HA instance, e.g. `http://192.168.20.12:8123`.
- **Photo source** — `Local directory` (default) or `HTTP catalog`.
- **Photo directory** — where photos live inside the engine (default `/photos`).
- **Idle timeout** — seconds of no input before switching to the photo frame.
- **Slide interval** — seconds each photo is shown.
- **Idle fade** — crossfade duration between HA and the photo frame.

Click **Save configuration**, then restart the engine for changes to take
effect:

```bash
sudo docker restart kiosk-engine     # container install
# or: sudo systemctl restart kiosk-engine   # --from-source install
```

## 4. Add photos

You can upload photos directly from the web config — no need to touch the
filesystem. Scroll to the **Photos** section, choose an image, and click
**Upload photo**. Uploaded photos appear in the list and are shown in the
photo frame.

![Photos section](images/vm-test/howto-config-photos.png)

> Photos are validated (must be a real JPEG/PNG/GIF/WebP/BMP) and stored in
> the engine's photos directory. You can also drop files into the host photos
> dir (`/opt/kiosk/photos` by default) or point the source at an HTTP catalog.

## 5. Start the kiosk

```bash
sudo systemctl start ha-photo-kiosk.service
```

The supervisor launches Chromium at `http://localhost:8080/frame/`. You should
see your Home Assistant login/dashboard:

![Active state — Home Assistant](images/vm-test/howto-active.png)

## 6. Watch it flip to the photo frame

After the idle timeout with no input, the frame fades to the photo slideshow:

![Idle state — photo frame](images/vm-test/howto-idle.png)

Any touch, mouse move, or key press flips instantly back to the dashboard. The
dashboard stays loaded in the background, so returning is instant — no reload,
no re-login.

## 7. Make it auto-start

The supervisor unit is already enabled, so it starts on boot. To start it now
without rebooting:

```bash
sudo systemctl start ha-photo-kiosk.service
```

## Troubleshooting

- **"Unable to fetch auth providers"** — the engine can't reach your HA. Check
  the HA URL in the web config and that HA is running.
- **No photos in the frame** — upload some via the web config, or check the
  photos dir has JPG/PNG/WebP files.
- **Kiosk not starting** — `journalctl -u ha-photo-kiosk.service` shows the
  supervisor log; it waits for the engine then launches Chromium.
- **Headless box** — the supervisor defaults to `DISPLAY=:0`. On a box with no
  physical display, run Xvfb on `:0` (see the supervisor README).

## Uninstall

```bash
sudo bash uninstall.sh
```

Add `REMOVE_DATA=1` to also delete the photos and config dirs.
