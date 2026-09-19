# Google Photos integration

Point the photo frame at your **own Google Photos library**. This uses the
official **Google Photos Library API** (read-only) via an OAuth2 client.

> **Honest status:** the code is implemented and unit-tested (with mocked
> Google responses), but **not live-tested against a real Google account** —
> it needs your OAuth2 credentials to exercise end-to-end, which aren't
> available in CI. The flow below is the standard one; if you hit a snag, the
> error is almost always a token scope or consent-screen issue (Section 5).

## What you need

1. A **Google Cloud project** with the Photos Library API enabled.
2. An **OAuth2 client ID + secret** created in that project.
3. A **refresh token** granting read access to your own library.

These are real secrets. They're read from **environment variables** for the
engine — never committed to the repo or the vault.

## 1. Create the Google Cloud client

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and
   create a project (or open an existing one).
2. **APIs & Services → Library** — search for **Photos Library API** and
   enable it.
3. **APIs & Services → OAuth consent screen** — configure it (user type
   "External", add your Google account as a test user).
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Web application** (or Desktop).
   - Authorized redirect URI: `http://localhost` (the consent flow is
     copy-paste, so no real callback server is needed).
   - Note the **Client ID** and **Client secret**.

## 2. Get a refresh token

The engine talks to Google on its own (no browser), so it needs a *refresh*
token, not a one-time access token. Get one by completing an OAuth consent
flow once, manually, from your laptop:

1. Build this authorization URL (replace the `CLIENT_ID` and `SCOPE`):

   ```
   https://accounts.google.com/o/oauth2/auth?client_id=CLIENT_ID&redirect_uri=http://localhost&response_type=code&scope=https://www.googleapis.com/auth/photoslibrary.readonly&access_type=offline&prompt=consent
   ```

2. Open it in a browser, sign in as the Google account whose Photos you want
   to show, approve consent.
3. You'll be redirected to `http://localhost/?code=AUTHORIZATION_CODE`. Copy
   the `code` value.
4. Exchange it for tokens (from your laptop):

   ```bash
   curl -s -X POST https://oauth2.googleapis.com/token \
     -d "code=AUTHORIZATION_CODE" \
     -d "client_id=CLIENT_ID" \
     -d "client_secret=CLIENT_SECRET" \
     -d "redirect_uri=http://localhost" \
     -d "grant_type=authorization_code"
   ```

   The response has `access_token` and — because you used `access_type=offline`
   — a long-lived **`refresh_token`**. Save the `refresh_token`.

## 3. Configure the engine

Point the engine at Google Photos and give it the credentials as environment
variables (either in `docker-compose.override.yml`, a `.env`, or the systemd
unit for a `--from-source` install):

```
PHOTO_SOURCE=google-photos
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_ALBUM_ID=            # optional: blank shows your whole library
```

Then set `photo_source` to **Google Photos** in the web config
(`/config/`) and restart the engine.

## 4. Optional: show one album

To limit to a single album, grab its ID from the Library API
(`GET https://photoslibrary.googleapis.com/v1/albums` with your access
token), and set `GOOGLE_ALBUM_ID`.

## 5. Troubleshooting

- **"Too many requests" / token invalid** — refresh tokens for Google's
  non-production "test user" flow expire after ~7 days unless the app is
  published (`verification` in the API console). For a personal kiosk, just
  re-run step 2 to get a fresh refresh token when this happens, or configure
  the OAuth consent screen so your Google account isn't a "test user"
  (publish the app as "In production" with just your user as an allowed
  domain).
- **No photos show** — confirm the grant scope includes
  `photoslibrary.readonly`, and that the album ID (if set) is correct.
- **Style of these URLs** — the frame page loads images directly from
  Google's CDN (`lh3.googleusercontent.com`), which works cross-origin for
  `<img>` tags (no proxy needed).