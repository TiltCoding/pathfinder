#!/usr/bin/env python3
"""Offline tests for the security headers on the `/mockup` route (stdlib unittest).

`/mockup` is the one server path that serves untrusted *active* content — agent
mockups rendered inside a `sandbox="allow-scripts"` iframe. As defence-in-depth
on top of the iframe sandbox and the existing traversal guards, the response
carries two headers (and only on `/mockup`):

  * `X-Content-Type-Options: nosniff`
  * a strict `Content-Security-Policy` that allows *inline* (so the existing
    inline-<script> mockups keep working) but blocks the *network/external*
    (default-src 'none').

These pin that:
  * a served mockup carries nosniff + the strict CSP, and the body is intact;
  * an ordinary (non-mockup) response — `_json` / `_send` without extra_headers —
    does NOT carry the CSP (the headers ride on `/mockup` only);
  * a 404 mockup response (served via `_json`) carries no CSP either.

No network and no disk outside a tempfile. Mirrors the stub in `tests/test_ask.py`.

Run with:
    python3 -m unittest tests.test_mockup_security
    python3 -m unittest discover -s tests   # full suite
"""

import os
import sys
import tempfile
import unittest

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")

import server     # noqa: E402


class _CapturingHandler:
    """A minimal stand-in that drives the real (unbound) `Handler` response
    methods and records status/headers/body without a real socket.

    `_serve_mockup` only reaches out via `_json`/`_send`, which call
    `send_response`/`send_header`/`end_headers`/`self.wfile.write` — all stubbed
    here. We reuse the real `_serve_mockup`/`_send`/`_json` methods so the actual
    header-emitting code under test runs. Adapted from `tests/test_ask.py`.
    """

    def __init__(self, workspace):
        self.workspace = workspace
        self.status = None
        self.headers = {}
        self._chunks = []

    # --- BaseHTTPRequestHandler surface used by _send ---
    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self._chunks.append(data)

    @property
    def body(self):
        return b"".join(self._chunks)

    # --- bound real handler methods ---
    _send = server.Handler._send
    _json = server.Handler._json
    _serve_mockup = server.Handler._serve_mockup


# A self-contained mockup with an inline <script> — realistic for the existing
# agent mockups, and exactly what the compatible CSP must NOT break.
_MOCKUP_HTML = (
    "<!doctype html><html><head><meta charset=\"utf-8\"></head>"
    "<body><h1>демо</h1><script>document.title='ok';</script></body></html>"
)


class MockupSecurityHeadersTest(unittest.TestCase):
    """`/mockup` responses carry nosniff + a strict, inline-friendly CSP; other
    responses do not."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        self.slug = "mockup-sec"
        self.mockups = os.path.join(self.ws.task_dir(self.slug), "mockups")
        os.makedirs(self.mockups, exist_ok=True)
        with open(os.path.join(self.mockups, "x.html"), "w",
                  encoding="utf-8") as f:
            f.write(_MOCKUP_HTML)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _serve(self, name):
        h = _CapturingHandler(self.ws)
        h._serve_mockup(self.slug, name)
        return h

    def test_mockup_carries_nosniff_and_csp(self):
        h = self._serve("x.html")
        self.assertEqual(h.status, 200)
        # nosniff present and exact
        self.assertEqual(h.headers.get("X-Content-Type-Options"), "nosniff")
        # CSP present and strict
        csp = h.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'none'", csp)
        # inline is allowed (so the inline-<script> mockup keeps working) ...
        self.assertIn("script-src 'unsafe-inline'", csp)
        # ... but no external/network source is permitted (sanity).
        self.assertNotIn("http://", csp)
        self.assertNotIn("https://", csp)
        self.assertNotIn("*", csp)
        # body is the file, byte-for-byte
        self.assertEqual(h.body, _MOCKUP_HTML.encode("utf-8"))

    def test_ordinary_response_has_no_csp(self):
        # A normal JSON response (the everyday path) must NOT carry the CSP —
        # the security headers ride on /mockup only.
        h = _CapturingHandler(self.ws)
        h._json(200, {"ok": True})
        self.assertEqual(h.status, 200)
        self.assertNotIn("Content-Security-Policy", h.headers)
        self.assertNotIn("X-Content-Type-Options", h.headers)

    def test_send_without_extra_headers_has_no_csp(self):
        # `_send` with no extra_headers stays backward-compatible: no CSP.
        h = _CapturingHandler(self.ws)
        h._send(200, b"<svg/>", "image/svg+xml; charset=utf-8")
        self.assertEqual(h.status, 200)
        self.assertNotIn("Content-Security-Policy", h.headers)

    def test_missing_mockup_404_has_no_csp(self):
        # A 404 (served via _json) carries no CSP — there is no content to guard.
        h = self._serve("nope.html")
        self.assertEqual(h.status, 404)
        self.assertNotIn("Content-Security-Policy", h.headers)


if __name__ == "__main__":
    unittest.main()
