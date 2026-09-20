# How to set up the HA Photo Kiosk

This guide walks through installing and configuring the kiosk on a light Debian
OS (Debian, or Raspberry Pi OS), using the **published container** (the
default) and the **web config service** to complete setup. All screenshots are
real captures from a test VM running the actual kiosk against a real Home
Assistant instance.

## What you'll end up with

A Chromium kiosk that shows your **Home Assistant dashboard** when active, and
flips to a **photo frame** when the display is idle. The browser loads one URL
once and never restarts — the engine (a container) multiplexes HA + photos, and
a thin host supervisor keeps Chromium alive.

## 1. Prerequisites

- A light Debian OS (Debian / Raspberry Pi OS) with a **physical display**
  attached (the kiosk boots straight into the UI on it).
- A reachable **Home Assistant** instance (e.g. `http://192.168.20.12:8123`).
- Root/sudo access.

> **Docker and the graphical stack (X server, Chromium, Openbox) are installed
> automatically by the installer** — you don't need them pre-installed. On a
> box with no physical display, add `--no-x` to skip the GUI stack (headless).

## 2. Install

Download the repo and run the installer. It installs the graphical stack
(X server, Chromium, Openbox), pulls the published container from GHCR (no
local build), creates the `kiosk` user, installs the supervisor + systemd
unit, and sets up the photos dir.

```bash
git clone https://github.com/kunaalm/ha-photo-kiosk-Py.git
cd ha-photo-kiosk-Py
sudo bash scripts/install.sh
```

The installer prints a summary and — importantly — **tells you to open the web
config service from a laptop**:

```
=========================== INSTALL COMPLETE ========================

  NEXT STEP — not done until you configure it:
  Open the WEB CONFIG SERVICE in a browser:

      http://localhost:8080/config/
```

## 3. Configure from your laptop (not the kiosk screen)

> **Configure this from a laptop/phone, not from the kiosk itself.** The kiosk
> is a single-purpose device you set up once and barely touch.

Open the kiosk's config page from your laptop (replace `192.168.1.50` with the
kiosk's real IP — the installer printed it):

```
http://192.168.1.50:8080/config/
```

![Web config service](images/vm-test/howto-config-full.png)

Set:
- **Home Assistant URL** — your HA instance, e.g. `http://192.168.20.12:8123`.
- **Photo source** — `Local directory` (uploaded, the default) or `HTTP catalog/feed`. For Google Photos, use the **Google Photos sync** section below instead (syncs into the local folder).
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

## 4. Add photos — upload them from the config page

**The supported way to add photos is the upload box in the config page** — no
ssh, no file copying, no flash drives. Open `/config/` on your laptop, scroll to
**Photos**, pick images with your laptop's file picker, and click **Upload**.
They're stored by the engine and shown in the frame.

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

For how everything works under the hood, see
[ARCHITECTURE.md](ARCHITECTURE.md) (design + implementation + testing) and
[`google-photos.md`](google-photos.md) for showing Google Photos by syncing to the photo folder.

## Troubleshooting

- **"Unable to fetch auth providers"** — the engine can't reach your HA. Check
  the HA URL in the web config and that HA is running.
- **No photos in the frame** — upload some via the web config, or check the
  photos dir has JPG/PNG/WebP files.
- **Google Photos sync not running** — enable it in the web config (**Google
  Photos sync** → **Enabled**), confirm the rclone remote exists (`sudo su -
  kiosk && rclone config`), then hit **Sync now** and check
  `systemctl status kiosk-gphotos-sync`.
- **Kiosk not starting** — `journalctl -u ha-photo-kiosk.service` shows the
  supervisor log; it waits for the engine then launches Chromium.
- **Headless box** — the supervisor defaults to `DISPLAY=:0`. On a box with no
  physical display, run Xvfb on `:0` (see the supervisor README).

## Uninstall

```bash
sudo bash scripts/uninstall.sh
```

Add `REMOVE_DATA=1` to also delete the photos and config dirs.
