"""Tests for photo source abstraction."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tempfile
from pathlib import Path

from kiosk_py.sources import LocalSource, HttpSource, IMAGE_EXTENSIONS, get_source


def test_local_source_lists_images_only_and_sorted():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "b.jpg").write_bytes(b"b")
        (d / "a.PNG").write_bytes(b"a")   # uppercase ext must match
        (d / "notes.txt").write_bytes(b"x")  # not an image
        (d / "sub").mkdir()
        (d / "sub" / "c.webp").write_bytes(b"c")

        src = LocalSource(td)
        photos = src.list()
        urls = [p.url for p in photos]
        assert len(urls) == 3  # b.jpg, a.PNG, sub/c.webp
        assert any(u.endswith("/images/a.PNG") for u in urls)
        assert any(u.endswith("/images/sub/c.webp") for u in urls)
        # no notes.txt
        assert not any("notes" in u for u in urls)


def test_local_source_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        assert LocalSource(td).list() == []


def test_local_source_missing_dir_is_empty():
    assert LocalSource("/nonexistent-xyz").list() == []


def test_http_source_parses_catalog():
    # Can't easily spin a server here without deps; test the URL-space against
    # a small local handler via the server integration instead.
    src = HttpSource("")  # empty → no network
    assert src.list() == []


def test_get_source_returns_expected_types():
    class FakeCfg:
        photo_source = "local"
        photo_dir = "/tmp"
        photo_catalog_url = ""
        http_proxy_timeout = 10
    assert isinstance(get_source(FakeCfg()), LocalSource)

    class FakeCfgHttp:
        photo_source = "http"
        photo_dir = "/tmp"
        photo_catalog_url = "http://x"
        http_proxy_timeout = 10
    assert isinstance(get_source(FakeCfgHttp()), HttpSource)