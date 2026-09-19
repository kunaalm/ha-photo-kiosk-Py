"""Tests for the Google Photos source (mocked — no live credentials)."""
import sys, os, json, unittest.mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.sources import GooglePhotosSource, get_source


class _FakeResp:
    def __init__(self, data): self._d = data
    def read(self): return json.dumps(self._d).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _router(req, timeout=10):
    """Mock urlopen, routing on the request URL (robust, not order-based)."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "oauth2.googleapis.com/token" in url:
        return _FakeResp({"access_token": "fake-access", "expires_in": 3600})
    if "mediaItems" in url:
        return _FakeResp({"mediaItems": [
            {"baseUrl": "https://lh3.googleusercontent.com/aaa", "filename": "one.jpg"},
            {"baseUrl": "https://lh3.googleusercontent.com/bbb", "filename": "two.jpg"},
        ]})
    return _FakeResp({})


def test_google_source_requires_auth():
    s = GooglePhotosSource(client_id="", client_secret="", refresh_token="")
    assert s.list() == []


def test_google_source_unauthenticated_returns_empty():
    s = GooglePhotosSource("id", "secret", "refresh")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=OSError("no net")):
        assert s.list() == []


def test_google_source_parses_media_items():
    s = GooglePhotosSource("id", "secret", "refresh")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=_router):
        photos = s.list()
    assert len(photos) == 2
    assert photos[0].url.startswith("https://lh3.googleusercontent.com/aaa=w1920-h1200")
    assert photos[0].caption == "one.jpg"


def test_google_source_refresh_plus_fetch():
    # Refresh happens only once; a second list() reuses the cached access token.
    s = GooglePhotosSource("id", "secret", "refresh")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=_router) as m:
        assert len(s.list()) == 2
        assert len(s.list()) == 2
    # token + media for first list; second list only fetches media (token cached)
    urls = []
    for c in m.call_args_list:
        u = c.args[0]
        urls.append(getattr(u, "full_url", str(u)))
    token_calls = sum(1 for u in urls if "oauth2.googleapis.com/token" in u)
    assert token_calls == 1  # access token cached after first refresh


def test_get_source_routes_google():
    class Cfg:
        photo_source = "google-photos"
        google_client_id = "id"
        google_client_secret = "sec"
        google_refresh_token = "tok"
        google_album_id = ""
        http_proxy_timeout = 10
    s = get_source(Cfg())
    assert isinstance(s, GooglePhotosSource)