"""Tests for the HA reverse-proxy header handling."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.server import KioskServer
from kiosk_py import config as cfgmod


def _server(cfg_attrs):
    cfg = cfgmod.Config()
    for k, v in cfg_attrs.items():
        setattr(cfg, k, v)
    return KioskServer(cfg)


def test_drops_frame_blocking_headers_when_enabled():
    s = _server({"strip_x_frame_options": True})
    out = s._rewrite_response_headers({
        "X-Frame-Options": "SAMEORIGIN",
        "Content-Type": "text/html",
        "frame-ancestors": "none",
        "Content-Length": "1234",
    })
    assert "x-frame-options" not in out
    assert "frame-ancestors" not in out
    assert out.get("Content-Type") == "text/html"
    assert "content-length" not in out  # aiohttp recomputes


def test_keeps_frame_blocking_when_disabled():
    s = _server({"strip_x_frame_options": False})
    out = s._rewrite_response_headers({"X-Frame-Options": "SAMEORIGIN"})
    assert out.get("X-Frame-Options") == "SAMEORIGIN"


def test_rewrites_absolute_location_back_through_proxy():
    s = _server({"ha_url": "http://192.168.20.12:8123", "ha_proxy_prefix": "/ha"})
    out = s._rewrite_response_headers({"Location": "http://192.168.20.12:8123/auth/login"})
    assert out["Location"] == "/ha/auth/login"


def test_rewrites_relative_location():
    s = _server({"ha_proxy_prefix": "/ha"})
    out = s._rewrite_response_headers({"Location": "/auth/login"})
    assert out["Location"] == "/ha/auth/login"


def test_keeps_external_location():
    s = _server({"ha_proxy_prefix": "/ha"})
    out = s._rewrite_response_headers({"Location": "https://example.com/x"})
    assert out["Location"] == "https://example.com/x"