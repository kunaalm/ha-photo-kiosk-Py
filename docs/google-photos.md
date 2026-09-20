# Google Photos integration (Ambient API)

Point the photo frame at your **own Google Photos library**. This uses the
official **Google Photos Ambient API** — the successor to the Library API,
built specifically for "view photos from your Google Photos library on
connected devices" (i.e. exactly a photo frame).

> **Why Ambient and not the Libraries API?** The old `photo-library` scope
> (`photoslibrary.readonly`) was **removed after March 31, 2025** — apps can
> only keep accessing app-created content. Google now directs personal-photo
> use to the **Ambient API** and the **Picker API**. Ambient is the right fit
> here: it's for devices (like a kiosk) that *display* a user's photos, and its
> device-code OAuth works great when there's no browser on the device.

> **Honest status:** the code is implemented and unit-tested against the
> **live Ambient discovery document**
> (`https://photosambient.googleapis.com/$discovery/rest`), but it has **not
> been live-tested against a real Google account** — that needs your OAuth2
> credentials to exercise end-to-end. A live-test harness is included:
> `scripts/test-google-live.py` walks the full flow (device-code auth → create
> device → pick sources → list photos → fetch one) against a real account.
> The flow below is the standard one per Google's current docs; if you hit a
> snag it's almost always a consent-screen or scope issue (Section 6).

## What you need

1. A **Google Cloud project**.
2. An **OAuth2 client ID + secret** — application type **"TVs and Limited
   Input devices"** (this is what enables the device-code flow).
3. An **Ambient API device** created in the engine once, then the user's
   choice of which sources (albums) to share, made in the Google Photos app.

## 1. Create the Google Cloud client

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and
   create a project (or open an existing one).
2. Open the **Google Photos Library API** for your project — in the API
   console, enable the Photos Library API and its **Ambient API**.
3. **OAuth consent screen** — configure it (user type "External", add your
   Google account as a test user).
4. **Credentials → Create Credentials → OAuth client ID**:
   - **Application type: TVs and Limited Input devices** ⚠️ — use this exact
     type. It enables the device-code (user_code + verification_url) flow
     that a kiosk needs (no callback redirect URI involved).
   - Note the **Client ID** and **Client secret**.

## 2. Scope

The Ambient API uses a single, dedicated scope:

```
https://www.googleapis.com/auth/photosambient.mediaitems
```

The engine requests exactly this scope (with `offline` access so it receives
a refresh token). No `photoslibrary.*` scopes — those are the removed ones.

## 3. Authorize + create a device

Because this is a **TV / limited-input-device** flow, authorization is
device-code: the engine (or you) starts the flow, Google returns a
`user_code` and a `verification_url`, you approve from your phone/laptop, and
the engine polls until approved. This is the *one-time* setup step.

At runtime the ambient source does the following (see
`kiosk_py/sources.py`, `AmbientSource`):

1. **`POST /v1/devices`** with `{ "displayName": "Photo Frame" }` → creates a
   device in your Photos account, returns its `id`.
2. You pick which **sources** to share — in the **Google Photos app →
   Device → the Photo Frame device** you just created, choose albums or
   highlights.
3. The engine polls **`GET /v1/devices/{id}`** until `mediaSourcesSet` is
   `true`.
4. It lists photos with **`GET /v1/mediaItems?deviceId={id}`** (paginated via
   `pageToken`/`pageSize`, default 50, max 100).

Each returned item has `id`, `name`, `createTime`, and a `mediaFile` with
`baseUrl` (+ `thumbnailBaseUrl`, `backgroundBaseUrl`, `mimeType`).

## 4. Configure the engine

Point the engine at Google Photos and give it the credentials as environment
variables (in `docker-compose.override.yml`, a `.env`, or the systemd unit
for a `--from-source` install):

```
PHOTO_SOURCE=google-photos
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...     # captured after the first device-code approval
GOOGLE_DEVICE_ID=...         # the device id from step 3
```

Then set `photo_source` to **Google Photos** in the web config (`/config/`)
and restart the engine. If `GOOGLE_DEVICE_ID` is blank, the engine logs a
clear message telling you to run the one-time device setup (Section 3) first.

## 5. Rendering: why the engine proxies the images

Ambient `mediaFile.baseUrl` values live on Google's CDN (`lh3...`) but a
request to them **must include `Authorization: Bearer <access token>` in the
header** — a bare `<img src="lh3...">` in the frame page cannot attach that
header.

So instead of loading Google's CDN directly, the engine:

1. Builds a sized URL from the base URL: `baseUrl =w<max-width>-h<max-height>`
   (fit within bounds; Google also supports `=d` for full metadata and `=c`
   crop — and offers `backgroundBaseUrl`, Google's own blurred fill).
2. Points the frame at an internal route — `/gimg/<url-encoded URL>`.
3. The engine fetches the bytes with the token attached and **streams them
   back** to the frame, so Google photos render like local ones (same origin,
   no CORS).

## 6. Troubleshooting

- **No photos after authorizing** — the user hasn't selected media sources
  yet: open the Google Photos app → the device name → pick albums. The
  engine only lists items once `mediaSourcesSet` is true.
- **`devices.create` fails** — you created the OAuth client as the wrong
  type. It must be **"TVs and Limited Input devices"**. On the wrong type the
  device-code endpoint won't work.
- **Refresh token invalid / expires** — Google's non-production "test user"
  flow expires refresh tokens after ~7 days unless the app is published.
  For a personal kiosk, re-run the one-time device flow, or configure the
  OAuth consent screen so your Google account isn't limited to "test user"
  (publish the app "In production" with your user as an allowed domain).
- **Images 502 from /gimg/** — the access token couldn't be refreshed (check
  the two secrets above), or the network can't reach `lh3.googleusercontent.com`.
- **"Ambient API" not available in the console** — make sure Photos Library
  API + Ambient are enabled for the project in the API console.