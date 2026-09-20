"""HTTP server: reverse-proxy for HA + frame page + /images + config service.

Built on aiohttp because HA dashboards use WebSockets for live state — a
stdlib http.server cannot forward them, and proxying HA without WS support
would silently break live updates. Within a container the dep is free.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from aiohttp import web

from .config import Config
from .config_store import ConfigStore, EDITABLE_FIELDS
from .sources import IMAGE_EXTENSIONS, get_source
from .auth import AuthStore, parse_basic_auth

log = logging.getLogger("kiosk-engine")

# Response headers that must be rewritten or dropped on the HA proxy path.
RESPONSE_HEADERS_TO_DROP = {
    "content-length",   # recomputed by aiohttp
    "content-encoding", # aiohttp already decompressed the body; forwarding the
                        # header makes the browser try to inflate plain bytes → black screen
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
}
FRAME_BLOCKING_HEADERS = {"x-frame-options", "frame-ancestors"}


class KioskServer:
    def __init__(self, config: Config, config_store: Optional[ConfigStore] = None,
                 auth_store: Optional[AuthStore] = None):
        self.config = config
        self.store = config_store or ConfigStore()
        self.auth = auth_store or AuthStore()
        # Effective config = env defaults + file overrides (from the web service).
        self.effective = self.store.effective_config()
        self.source = get_source(self.effective)
        self.ha_origin = self.effective.ha_url  # e.g. http://192.168.20.12:8123
        self._client: Optional[aiohttp.ClientSession] = None

    async def _get_client(self) -> aiohttp.ClientSession:
        # aiohttp requires a running event loop to create a ClientSession, so
        # create it lazily on first use (inside the async request path).
        if self._client is None or self._client.closed:
            self._client = aiohttp.ClientSession()
        return self._client

    # ---- Auth guard -----------------------------------------------------
    def _require_auth(self, request: web.Request) -> Optional[web.Response]:
        """Return a 401 response if the request isn't authenticated, else None.

        The config/upload service is LAN-reachable; it must not be open to
        anyone on the network. Basic auth against the kiosk-owned auth file.
        """
        creds = parse_basic_auth(request.headers.get("Authorization"))
        if not creds:
            return self._unauthorized()
        user, pw = creds
        if not self.auth.verify(user, pw):
            return self._unauthorized()
        return None

    def _unauthorized(self) -> web.Response:
        return web.Response(
            status=401,
            text="Authentication required.",
            headers={"WWW-Authenticate": 'Basic realm="kiosk-config"'},
        )

    # ---- HA reverse proxy ------------------------------------------------
    async def proxy_ha(self, request: web.Request) -> web.Response:
        """Proxy everything not handled by engine routes to the real HA."""
        downstream_path = request.path or "/"
        if request.query_string:
            downstream_path += "?" + request.query_string

        target = urljoin(self.ha_origin, downstream_path)
        timeout = aiohttp.ClientTimeout(total=self.effective.http_proxy_timeout)

        is_upgrade = request.headers.get("Upgrade", "").lower() == "websocket"
        if is_upgrade:
            return await self._proxy_websocket(request, target)

        # Plain HTTP(S) forward.
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "connection", "content-length", "transfer-encoding")
        }
        method = request.method
        body = await request.read()

        client = await self._get_client()
        try:
            async with client.request(
                method, target, headers=headers, data=body,
                timeout=timeout, allow_redirects=False,
            ) as resp:
                resp_body = await resp.read()
                out_headers = self._rewrite_response_headers(dict(resp.headers))
                return web.Response(
                    status=resp.status, body=resp_body, headers=out_headers,
                    reason=resp.reason,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("HA proxy error for %s: %s", target, exc)
            return web.Response(status=502, text="Home Assistant unreachable through engine.")

    async def _proxy_websocket(self, request: web.Request, target: str) -> web.WebSocketResponse:
        # Upgrade the server→client socket.
        server_ws = web.WebSocketResponse(heartbeat=30)
        await server_ws.prepare(request)
        client = await self._get_client()
        try:
            async with client.ws_connect(target, timeout=self.effective.http_proxy_timeout) as client_ws:
                async def pump_s2c():
                    async for msg in client_ws:
                        if msg.type == aiohttp.WSMsgType.ERROR:
                            break
                        await server_ws.send_str(msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8", "replace"))
                async def pump_c2s():
                    async for msg in server_ws:
                        if msg.type == aiohttp.WSMsgType.ERROR:
                            break
                        data = msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8", "replace")
                        await client_ws.send_str(data)
                s2c = asyncio.create_task(pump_s2c())
                c2s = asyncio.create_task(pump_c2s())
                done, pending = await asyncio.wait(
                    {s2c, c2s}, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
            log.warning("WebSocket proxy failed: %s", exc)
        return server_ws

    def _rewrite_response_headers(self, headers: dict) -> dict:
        out = {}
        for k, v in headers.items():
            k_l = k.lower()
            if k_l in RESPONSE_HEADERS_TO_DROP:
                continue
            if self.effective.strip_x_frame_options and k_l in FRAME_BLOCKING_HEADERS:
                continue  # drop frame-blocking so the iframe is allowed
            # Rewrite any Location headers that would point back at bare HA.
            if k_l == "location" and v:
                out[k] = self._rewrite_location(v)
            else:
                out[k] = v
        return out

    def _rewrite_location(self, location: str) -> str:
        # If HA redirects to itself (login), point back through our proxy.
        if location.startswith(self.ha_origin):
            return location[len(self.ha_origin):]
        if location.startswith("/"):
            return location
        return location

    # ---- Frame page + images --------------------------------------------
    async def serve_frame(self, request: web.Request) -> web.Response:
        # The frame page: loads HA in an iframe (proxied), and a slideshow layer.
        html = Path(__file__).with_name("frame.html").read_text()
        html = html.replace("{{IDLE_TIMEOUT_SECONDS}}", str(self.effective.idle_timeout_seconds))
        html = html.replace("{{IDLE_FADE_SECONDS}}", str(self.effective.idle_fade_seconds))
        html = html.replace("{{SLIDE_INTERVAL_SECONDS}}", str(self.effective.slide_interval_seconds))
        return web.Response(text=html, content_type="text/html")

    async def serve_photos(self, request: web.Request) -> web.Response:
        """Return the current photo list as JSON for the frame JS"""
        return web.json_response({"photos": [
            {"url": p.url, "caption": p.caption} for p in self.source.list()
        ]})

    async def serve_local_image(self, request: web.Request) -> web.Response:
        rel = request.match_info["path"]
        base = Path(self.effective.photo_dir).resolve()
        full = (base / rel).resolve()
        if not full.is_relative_to(base) or not full.is_file():
            raise web.HTTPNotFound()
        data = full.read_bytes()
        import mimetypes
        return web.Response(body=data, content_type=mimetypes.guess_type(str(full))[0] or "application/octet-stream")

    # ---- Config service (web UI + API) ----------------------------------
    async def serve_config_page(self, request: web.Request) -> web.Response:
        denied = self._require_auth(request)
        if denied:
            return denied
        html = Path(__file__).with_name("config.html").read_text()
        return web.Response(text=html, content_type="text/html")

    async def get_config(self, request: web.Request) -> web.Response:
        denied = self._require_auth(request)
        if denied:
            return denied
        return web.json_response(self.store.public_state())

    async def post_config(self, request: web.Request) -> web.Response:
        denied = self._require_auth(request)
        if denied:
            return denied
        # While the password must be changed, refuse config writes so the
        # operator is forced to set a real password first.
        if self.auth.must_change():
            return web.json_response(
                {"ok": False, "error": "change the default password first"},
                status=403,
            )
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        # Validate types before persisting.
        for k, v in data.items():
            if k not in EDITABLE_FIELDS:
                continue
            t = EDITABLE_FIELDS[k]
            if t == "int" and not isinstance(v, int):
                return web.json_response({"ok": False, "error": f"{k} must be an integer"}, status=400)
            if t == "bool" and not isinstance(v, bool):
                return web.json_response({"ok": False, "error": f"{k} must be a boolean"}, status=400)
            if t == "str" and not isinstance(v, str):
                return web.json_response({"ok": False, "error": f"{k} must be a string"}, status=400)
        self.store.save(data)
        return web.json_response({"ok": True})

    async def change_password(self, request: web.Request) -> web.Response:
        """Change the config password (authenticated with the current one).

        Body: {"current": "...", "new": "..."}. On success clears the
        must-change flag so config writes are allowed again.
        """
        denied = self._require_auth(request)
        if denied:
            return denied
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        current = data.get("current", "")
        new = data.get("new", "")
        if not self.auth.verify(self.auth.username(), current):
            return web.json_response({"ok": False, "error": "current password incorrect"}, status=403)
        if len(new) < 8:
            return web.json_response({"ok": False, "error": "new password must be at least 8 characters"}, status=400)
        self.auth.set_password(self.auth.username(), new, must_change=False)
        log.info("config password changed")
        return web.json_response({"ok": True})

    async def auth_status(self, request: web.Request) -> web.Response:
        """Whether the password must be changed (for the UI to show the form)."""
        denied = self._require_auth(request)
        if denied:
            return denied
        return web.json_response({"must_change": self.auth.must_change()})

    # ---- Photo upload / management API ----------------------------------
    def _safe_photo_name(self, filename: str) -> Optional[str]:
        """Sanitize an uploaded filename: basename only, image extension only,
        no path separators. Returns None if rejected."""
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            return None
        base, ext = os.path.splitext(filename)
        if ext.lower() not in IMAGE_EXTENSIONS or not base:
            return None
        # Keep only safe characters; collapse to avoid oddities.
        import re
        base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
        return base + ext.lower()

    def _is_real_image(self, data: bytes) -> bool:
        """Magic-byte sniff: reject anything that isn't a known image format,
        so we don't store arbitrary uploads served back as images."""
        return (
            data.startswith(b"\xff\xd8\xff")            # JPEG
            or data.startswith(b"\x89PNG\r\n\x1a\n")    # PNG
            or data[:6] in (b"GIF87a", b"GIF89a")      # GIF
            or data.startswith(b"RIFF")                 # WEBP
            or data.startswith(b"BM")                   # BMP
        )

    async def serve_photos_list(self, request: web.Request) -> web.Response:
        """Names of stored photos (for the upload UI)."""
        denied = self._require_auth(request)
        if denied:
            return denied
        from urllib.parse import unquote
        photos = self.source.list()
        return web.json_response({"photos": [
            {"url": p.url, "name": unquote(p.url.rsplit("/", 1)[-1])} for p in photos
        ]})

    async def upload_photo(self, request: web.Request) -> web.Response:
        """Accept a multipart image upload and store it in the photo dir."""
        denied = self._require_auth(request)
        if denied:
            return denied
        try:
            reader = await request.multipart()
            part = await reader.next()
            if part is None:
                return web.json_response({"ok": False, "error": "no file part"}, status=400)
            # Name from the part's filename header.
            filename = part.filename or ""
            safe = self._safe_photo_name(filename)
            if safe is None:
                return web.json_response({"ok": False, "error": f"invalid filename: {filename}"}, status=400)
            # Read, then enforce a size cap (20 MB) manually — aiohttp's
            # BodyPartReader.read() doesn't take max_size in this version.
            data = await part.read()
            if len(data) > 20 * 1024 * 1024:
                return web.json_response({"ok": False, "error": "file too large (max 20 MB)"}, status=400)
            if not self._is_real_image(data):
                return web.json_response({"ok": False, "error": "not a recognized image file"}, status=400)
            dest = Path(self.effective.photo_dir) / safe
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            log.info("uploaded photo %s (%d bytes)", safe, len(data))
            return web.json_response({"ok": True, "name": safe})
        except Exception as exc:
            log.warning("photo upload failed: %s", exc)
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def delete_photo(self, request: web.Request) -> web.Response:
        """Delete a stored photo by name (path-traversal safe)."""
        denied = self._require_auth(request)
        if denied:
            return denied
        name = request.match_info["name"]
        safe = self._safe_photo_name(name)
        if safe is None:
            return web.json_response({"ok": False, "error": "invalid name"}, status=400)
        base = Path(self.effective.photo_dir).resolve()
        full = (base / safe).resolve()
        if not full.is_relative_to(base) or not full.is_file():
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        full.unlink()
        log.info("deleted photo %s", safe)
        return web.json_response({"ok": True})

    # ---- app assembly ----------------------------------------------------
    def build_app(self) -> web.Application:
        app = web.Application()
        # Engine routes FIRST (aiohttp matches in registration order), so they
        # win over the HA catch-all below.
        app.router.add_get("/frame/", self.serve_frame)
        app.router.add_get("/frame", self.serve_frame)
        app.router.add_get("/photos.json", self.serve_photos)
        app.router.add_get("/images/{path:.*}", self.serve_local_image)
        # Config service (web UI + API) — all behind basic auth.
        app.router.add_get("/config/", self.serve_config_page)
        app.router.add_get("/config", self.serve_config_page)
        app.router.add_get("/api/config", self.get_config)
        app.router.add_post("/api/config", self.post_config)
        app.router.add_get("/api/auth/status", self.auth_status)
        app.router.add_post("/api/auth/change", self.change_password)
        # Photo upload / management API — behind basic auth.
        app.router.add_get("/api/photos", self.serve_photos_list)
        app.router.add_post("/api/photos", self.upload_photo)
        app.router.add_delete("/api/photos/{name}", self.delete_photo)
        # Everything else → HA, proxied at the ROOT. This is deliberate: HA's
        # frontend references assets at absolute paths (/frontend_latest/...,
        # /static/..., /api/...). Proxying under a subpath (/ha/) breaks those
        # (the browser requests /frontend_latest/... which isn't proxied → 404
        # → black screen). Root proxying keeps HA's absolute paths intact.
        app.router.add_route("*", "/{tail:.*}", self.proxy_ha)
        return app

    async def close(self) -> None:
        if self._client is not None and not self._client.closed:
            await self._client.close()