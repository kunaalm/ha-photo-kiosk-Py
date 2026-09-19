#!/usr/bin/env python3
"""Entry point: run the engine as a standalone process or in a container."""
import argparse
import asyncio
import logging
import os

from aiohttp import web

from kiosk_py.config import Config
from kiosk_py.config_store import ConfigStore
from kiosk_py.server import KioskServer


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HA Photo Kiosk engine")
    p.add_argument("--host", default=None, help="listen host (default: from env HOST)")
    p.add_argument("--port", type=int, default=None, help="listen port (default: from env PORT)")
    p.add_argument("--ha-url", default=None, help="Home Assistant base URL (default: env HA_URL)")
    return p


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args()
    cfg = Config.from_env()
    if args.host: cfg.host = args.host
    if args.port: cfg.port = args.port
    if args.ha_url: cfg.ha_url = args.ha_url.rstrip("/")

    srv = KioskServer(cfg, config_store=ConfigStore(os.getenv("CONFIG_FILE", "/config/kiosk.json")))
    app = srv.build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    print(f"HA Photo Kiosk listening on http://{cfg.host}:{cfg.port} (HA -> {cfg.ha_url})", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await srv.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())