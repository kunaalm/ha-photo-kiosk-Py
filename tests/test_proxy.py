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


def test_drops_content_encoding():
    # aiohttp auto-decompresses the body; forwarding Content-Encoding makes the
    # browser try to inflate already-plain bytes → black screen.
    s = _server({"strip_x_frame_options": True})
    out = s._rewrite_response_headers({"Content-Encoding": "deflate", "Content-Type": "text/html"})
    assert "content-encoding" not in out
    assert out.get("Content-Type") == "text/html"


def test_keeps_frame_blocking_when_disabled():
    s = _server({"strip_x_frame_options": False})
    out = s._rewrite_response_headers({"X-Frame-Options": "SAMEORIGIN"})
    assert out.get("X-Frame-Options") == "SAMEORIGIN"


def test_rewrites_absolute_location_to_root():
    # Root proxying: an absolute HA Location becomes a root-relative path.
    s = _server({"ha_url": "http://192.168.20.12:8123"})
    out = s._rewrite_response_headers({"Location": "http://192.168.20.12:8123/auth/login"})
    assert out["Location"] == "/auth/login"


def test_keeps_relative_location():
    # Root proxying: a relative Location is already correct.
    s = _server({"ha_url": "http://192.168.20.12:8123"})
    out = s._rewrite_response_headers({"Location": "/auth/login"})
    assert out["Location"] == "/auth/login"


def test_keeps_external_location():
    s = _server({"ha_url": "http://192.168.20.12:8123"})
    out = s._rewrite_response_headers({"Location": "https://example.com/x"})
    assert out["Location"] == "https://example.com/x"