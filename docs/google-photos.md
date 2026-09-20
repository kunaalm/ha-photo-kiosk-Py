# Showing Google Photos

The kiosk shows photos from a local directory (`~/kiosk/photos` on the host,
mounted at `/photos` in the engine). To show your **Google Photos**, sync them
into that directory with an external tool — the kiosk itself touches no cloud
API.

This is the deliberate design for a homelab kiosk. Google's Ambient and Picker
APIs — the ones built for shipping consumer/enterprise photo-display *products*
— require a Google Cloud project, an OAuth client, a consent flow, and device
management in Google's console. None of that belongs in a home photo frame.
The kiosk just reads files; a sync job keeps them fresh.

## How it works

```
Google Photos  ──sync job──▶  ~/kiosk/photos  ──LocalSource──▶  the frame
   (cloud)                     (local files)                      (kiosk)
```

The sync job downloads your photos into `~/kiosk/photos` on a schedule (e.g.
daily, or on a timer). The kiosk's `LocalSource` picks them up — it walks the
directory, so new files appear automatically.

## Option A: rclone (recommended)

[rclone](https://rclone.org) syncs remote storage to a local folder and
supports Google Photos.

```bash
# install rclone
sudo apt install rclone

# one-time config (choose Google Photos as the remote)
rclone config

# sync your photos into the kiosk's photo dir
rclone sync gphotos:media/by-month ~/.rclone-cache/photos \
  --transfers 4 --fast-list

# then copy into the kiosk dir (or mount directly)
cp -r ~/.rclone-cache/photos/* /home/kiosk/photos/
```

> **Note:** Google Photos via rclone can be rate-limited. The important thing
> is the *pattern* — sync to a folder, let the kiosk serve files. Use whatever
> sync tool you prefer.

## Option B: Takeout downloader

Google Takeout exports your photos as a zip. A small script can download the
latest export and unpack it into `~/kiosk/photos`. This is the most
"hands-off" and needs no API credentials.

## Option C: skip it — use local-only photos

If you don't need cloud photos, just upload images from the web config page
(`/config/`, **Photos** section) or drop files into `~/kiosk/photos`. The
kiosk works perfectly with zero Google involvement.

## Scheduling the sync

Add a systemd timer or cron job (as the `kiosk` user) to run the sync
periodically:

```bash
# /etc/systemd/system/gphotos-sync.timer
[Unit]
Description=Sync Google Photos

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

## Why not the Ambient/Picker APIs?

- They're built for shipping **products** (digital picture-frame devices,
  kiosk vendors) — Google Cloud project, OAuth client typed specifically, a
  device to create and manage in Google's console, a consent flow.
- For a homelab, that's a large setup burden for zero benefit over a sync job.
- rclone/Takeout/sync-to-folder does the same job with no API credentials and
  no quota limits to hit (Google's APIs cap requests per day).