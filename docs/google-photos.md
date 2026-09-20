# Google Photos

The kiosk shows photos from a local folder (`~/kiosk/photos`), and Google
Photos enter that folder through a **built-in sync** that runs `rclone` on the
kiosk host. No cloud API, no OAuth client in the app — the engine never talks
to Google.

## How it works

- A host-side service (`kiosk-gphotos-sync`) runs `rclone sync
  gphotos:<source> ~/kiosk/photos` on a systemd timer (hourly).
- A systemd `.path` watcher runs the sync **immediately** when the config UI
  requests a "Sync now".
- The engine only reads/writes a small state file in the shared config dir:
  `sync.json` (config) and `sync-status.json` (status). Enable/trigger are just
  file operations from the web UI.

```
        config UI (engine)          ~/kiosk/config            host service
   ┌──────────────────────┐
   │  /api/sync           │── write/read ──► sync.json ────────┐
   │  /api/sync/trigger   │── create ─────► sync-trigger ──────┤
   │  /api/sync (status)  │◄─ read ─────── sync-status.json ───┤
   └──────────────────────┘                                    ▼
                                              kiosk-gphotos-sync (rclone)
                                                     │
                                          ~/kiosk/photos  ◄── gphotos:media/...
```

## One-time setup: rclone remote

The sync needs an rclone remote that can read your Google Photos. Run once on
the kiosk (as the `kiosk` user):

```
sudo su - kiosk
rclone config        # choose "Google Photos", follow OAuth; name it "gphotos"
```

rclone will print a URL; open it on any device to authorize, then paste the
code back. This is the only interactive Google step, and it's the standard
rclone flow (same as any cloud drive).

> rclone's Google Photos backend uses Google's Photos Library / Ambient API
> under the hood, but *you* never touch that — rclone handles the OAuth and
> quota for you. If rclone reports a quota error, it's Google's daily request
> limit, not a paid tier.

## Configure & control from the web UI

Open the config service (port 8080) → **Google Photos sync**:

- **Enabled** — turns the scheduled sync on/off.
- **rclone remote name** — the name you gave in `rclone config` (default
  `gphotos`).
- **Source path** — album/collection inside the remote (default
  `media/by-month`).
- **Sync now** — requests an immediate sync (host runs it within seconds).
- **Status** — last run time, last error, and photo count.

The sync is disabled by default until you enable it — installs cleanly whether
or not you use Google Photos.

## Notes

- The sync downloads a *copy*; the kiosk shows local files. This works offline
  and keeps the frame independent of Google's availability.
- Photos appear on the frame's next refresh of the idle photo list.
- Uninstall with the normal `scripts/uninstall.sh` (removes the service,
  timer, path unit, and sync script).