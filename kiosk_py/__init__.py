"""ha-photo-kiosk Py — engine package.

Two-state kiosk engine: reverse-proxies a Home Assistant dashboard and a
photo frame behind one local URL, switching on client-side idle detection.

The browser stays on a single URL; the engine multiplexes content.
"""

__version__ = "0.2.0"