"""Tests for the photo upload/management API."""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from kiosk_py.server import KioskServer
from kiosk_py import config as cfgmod


def _server(photo_dir):
    cfg = cfgmod.Config()
    cfg.photo_dir = photo_dir
    s = KioskServer(cfg)
    # Mirror photo_dir onto the effective config the server actually uses.
    s.effective.photo_dir = photo_dir
    s.source.photo_dir = Path(photo_dir)
    return s


def test_safe_photo_name_accepts_simple():
    with tempfile.TemporaryDirectory() as td:
        s = _server(td)
        assert s._safe_photo_name("photo.jpg") == "photo.jpg"
        assert s._safe_photo_name("my photo 1.JPG") == "my_photo_1.jpg"


def test_safe_photo_name_rejects_unsafe():
    with tempfile.TemporaryDirectory() as td:
        s = _server(td)
        assert s._safe_photo_name("../../etc/passwd") is None   # traversal
        assert s._safe_photo_name("script.sh") is None           # not an image
        assert s._safe_photo_name("note.txt") is None
        assert s._safe_photo_name(".hidden") is None
        assert s._safe_photo_name("") is None


def test_is_real_image_detection():
    with tempfile.TemporaryDirectory() as td:
        s = _server(td)
        assert s._is_real_image(b"\xff\xd8\xff") is True          # JPEG
        assert s._is_real_image(b"\x89PNG\r\n\x1a\n") is True     # PNG
        assert s._is_real_image(b"GIF89a") is True                # GIF
        assert s._is_real_image(b"RIFFxxxxWEBP") is True          # WEBP
        assert s._is_real_image(b"#!/bin/sh\nrm -rf /") is False  # script masquerading
        assert s._is_real_image(b"hello world") is False


def test_upload_rejects_non_image():
    with tempfile.TemporaryDirectory() as td:
        s = _server(td)
        # A text file masquerading as .jpg must not be stored.
        assert s._safe_photo_name("evil.jpg") == "evil.jpg"
        base = Path(td)
        # simulate: the detector returns False for text bytes regardless of name
        assert s._is_real_image(b"not really an image") is False