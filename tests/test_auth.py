"""Tests for the config-service basic auth (kiosk_py/auth.py)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.auth import AuthStore, parse_basic_auth, generate_password


def _store():
    d = tempfile.mkdtemp()
    return AuthStore(os.path.join(d, "auth.json"))


def test_parse_basic_auth():
    import base64
    h = "Basic " + base64.b64encode(b"kiosk:secret").decode()
    assert parse_basic_auth(h) == ("kiosk", "secret")
    assert parse_basic_auth(None) is None
    assert parse_basic_auth("Bearer xyz") is None
    assert parse_basic_auth("Basic notbase64!!") is None


def test_set_and_verify_password():
    s = _store()
    s.set_password("kiosk", "hunter2", must_change=False)
    assert s.verify("kiosk", "hunter2") is True
    assert s.verify("kiosk", "wrong") is False
    assert s.verify("other", "hunter2") is False
    assert s.must_change() is False


def test_temp_password_finalized_on_first_login():
    # Simulate the installer: plaintext temp password + must_change.
    s = _store()
    s.save({"username": "kiosk", "password": "temp123", "must_change": True})
    assert s.verify("kiosk", "temp123") is True
    # After first login the plaintext is gone, replaced by a hash.
    data = s.load()
    assert "password" not in data
    assert data.get("password_hash")
    assert data.get("salt")
    assert s.must_change() is True  # still must change
    # The temp password still works (now stored as a hash), but the plaintext
    # is gone from disk.
    assert s.verify("kiosk", "temp123") is True


def test_generate_password_is_random_and_safe():
    a = generate_password()
    b = generate_password()
    assert a != b
    assert len(a) >= 20
    # No ambiguous chars (no 0/O/1/l/I).
    assert not any(c in a for c in "0O1lI")