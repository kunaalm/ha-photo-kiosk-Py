#!/usr/bin/env python3
"""Live test for the Google Photos Ambient integration.

Walks the full Ambient API flow against a REAL Google account, so you can
verify the integration end-to-end before wiring it into the kiosk. Requires
the OAuth client credentials from a Google Cloud project (application type
"TVs and Limited Input devices").

Usage:
    GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python3 scripts/test-google-live.py

It will:
  1. Start the device-code flow and print a user_code + verification_url.
  2. Wait for you to approve from your phone/laptop (polls the token endpoint).
  3. Create an Ambient "device" in your Photos account.
  4. Tell you to pick which albums to share in the Google Photos app, then
     poll until mediaSourcesSet is true.
  5. List the media items and fetch one image's bytes.

Secrets are read from env only — never hardcoded, never logged.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.sources import AmbientSource


def main() -> int:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("ERROR: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (env only).")
        return 1

    src = AmbientSource(client_id=client_id, client_secret=client_secret)

    # --- 1. Device-code flow -------------------------------------------
    print("\n=== 1. Starting device-code authorization ===")
    info = src.auth.start_device_code()
    if not info or "user_code" not in info:
        print("ERROR: could not start device-code flow. Is the OAuth client type")
        print("      'TVs and Limited Input devices'? (see docs/google-photos.md)")
        return 1
    print(f"  Go to:  {info['verification_url']}")
    print(f"  Enter:  {info['user_code']}")
    print("  (Approve from your phone/laptop, then wait here.)")

    # --- 2. Poll for the token ------------------------------------------
    print("\n=== 2. Waiting for approval (polling token endpoint) ===")
    token = None
    for _ in range(120):  # up to ~10 min
        token = src.auth.poll_for_token()
        if token:
            break
        time.sleep(5)
    if not token:
        print("ERROR: timed out waiting for approval.")
        return 1
    print("  Approved. Access token obtained.")
    print(f"  Refresh token: {src.auth._refresh_token}  <-- save this (GOOGLE_REFRESH_TOKEN)")

    # --- 3. Create a device ---------------------------------------------
    print("\n=== 3. Creating an Ambient device ===")
    dev = src.create_device(display_name="Photo Frame (live test)")
    if not dev or "id" not in dev:
        print("ERROR: devices.create failed. Check the API is enabled and the")
        print("      OAuth client type is correct.")
        return 1
    device_id = dev["id"]
    print(f"  Device created: {device_id}  <-- save this (GOOGLE_DEVICE_ID)")
    src.device_id = device_id

    # --- 4. Wait for media sources --------------------------------------
    print("\n=== 4. Pick which albums to share ===")
    print("  Open the Google Photos app -> Device -> 'Photo Frame (live test)'")
    print("  and choose albums/highlights. Waiting for mediaSourcesSet...")
    for _ in range(120):  # up to ~10 min
        if src.device_ready():
            print("  mediaSourcesSet = true. Sources configured.")
            break
        time.sleep(5)
    else:
        print("ERROR: timed out waiting for media sources to be set.")
        return 1

    # --- 5. List media items + fetch one image --------------------------
    print("\n=== 5. Listing media items ===")
    photos = src.list()
    if not photos:
        print("  No photos returned. Check the selected sources have images.")
        return 1
    print(f"  Got {len(photos)} photos.")
    for p in photos[:5]:
        print(f"    {p.url[:80]}  ({p.caption})")
    print("  ...")

    print("\n=== 6. Fetching the first image's bytes ===")
    encoded = photos[0].url[len("/gimg/"):]
    data = src.fetch_image_bytes(encoded)
    if not data:
        print("ERROR: could not fetch image bytes (token refresh or network).")
        return 1
    print(f"  Fetched {len(data)} bytes. OK.")

    print("\n=== LIVE TEST PASSED ===")
    print("  Save these for the kiosk config:")
    print(f"    GOOGLE_REFRESH_TOKEN={src.auth._refresh_token}")
    print(f"    GOOGLE_DEVICE_ID={device_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())