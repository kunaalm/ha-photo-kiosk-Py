"""HTTP server: reverse-proxy for HA + frame page + /images.

Built on aiohttp because HA dashboards use WebSockets for live state — a
stdlib http.server cannot forward them, and proxying HA without WS support
would silently break live updates. Within a container the dep is free.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from aiohttp import web

from .config import Config
from .sources import get_source

log = logging.getLogger("kiosk-engine")

# Response headers that must be rewritten or dropped on the HA proxy path.
RESPONSE_HEADERS_TO_DROP = {
    "content-length",   # recomputed by aiohttp
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
}
FRAME_BLOCKING_HEADERS = {"x-frame-options", "frame-ancestors"}


class KioskServer:
    def __init__(self, config: Config):
        self.config = config
        self.source = get_source(config)
        self.ha_origin = config.ha_url  # e.g. http://192.168.20.12:8123
        self._client: Optional[aiohttp.ClientSession] = None

    async def _get_client(self) -> aiohttp.ClientSession:
        # aiohttp requires a running event loop to create a ClientSession, so
        # create it lazily on first use (inside the async request path).
        if self._client is None or self._client.closed:
            self._client = aiohttp.ClientSession()
        return self._client

    # ---- HA reverse proxy ------------------------------------------------
    async def proxy_ha(self, request: web.Request) -> web.Response:
        """Proxy everything under /ha/* to the real HA instance."""
        prefix = self.config.ha_proxy_prefix  # "/ha"
        downstream_path = request.path[len(prefix):] or "/"
        if request.query_string:
            downstream_path += "?" + request.query_string

        target = urljoin(self.ha_origin, downstream_path)
        timeout = aiohttp.ClientTimeout(total=self.config.http_proxy_timeout)

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
            async with client.ws_connect(target, timeout=self.config.http_proxy_timeout) as client_ws:
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
            if self.config.strip_x_frame_options and k_l in FRAME_BLOCKING_HEADERS:
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
            remainder = location[len(self.ha_origin):]
            return self.config.ha_proxy_prefix + remainder
        if location.startswith("/"):
            return self.config.ha_proxy_prefix + location
        return location

    # ---- Frame page + images --------------------------------------------
    async def serve_frame(self, request: web.Request) -> web.Response:
        # The frame page: loads HA in an iframe (proxied), and a slideshow layer.
        html = Path(__file__).with_name("frame.html").read_text()
        html = html.replace("{{IDLE_TIMEOUT_SECONDS}}", str(self.config.idle_timeout_seconds))
        html = html.replace("{{IDLE_FADE_SECONDS}}", str(self.config.idle_fade_seconds))
        html = html.replace("{{SLIDE_INTERVAL_SECONDS}}", str(self.config.slide_interval_seconds))
        html = html.replace("{{HA_PROXY_PREFIX}}", self.config.ha_proxy_prefix)
        return web.Response(text=html, content_type="text/html")

    async def serve_photos(self, request: web.Request) -> web.Response:
        """Return the current photo list as JSON for the frame JS"""
        return web.json_response({"photos": [
            {"url": p.url, "caption": p.caption} for p in self.source.list()
        ]})

    async def serve_local_image(self, request: web.Request) -> web.Response:
        rel = request.match_info["path"]
        base = Path(self.config.photo_dir).resolve()
        full = (base / rel).resolve()
        if not full.is_relative_to(base) or not full.is_file():
            raise web.HTTPNotFound()
        data = full.read_bytes()
        import mimetypes
        return web.Response(body=data, content_type=mimetypes.guess_type(str(full))[0] or "application/octet-stream")

    # ---- app assembly ----------------------------------------------------
    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self.serve_frame)
        app.router.add_get("/photos.json", self.serve_photos)
        prefix = self.config.ha_proxy_prefix
        app.router.add_route("*", f"{prefix}/{{tail:.*}}", self.proxy_ha)
        app.router.add_get("/images/{path:.*}", self.serve_local_image)
        return app

    async def close(self) -> None:
        if self._client is not None and not self._client.closed:
            await self._client.close()