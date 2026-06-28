#!/usr/bin/env python3
"""Security headers on every response (feat-11, stdlib unittest).

Pins the anti-clickjacking + Referer-leak hardening so a regression fails CI:
- every response carries `Referrer-Policy: no-referrer`;
- HTML responses (dashboard/hub) carry `X-Frame-Options: SAMEORIGIN` and
  `Content-Security-Policy: frame-ancestors 'self'`;
- a non-HTML (JSON) response gets the Referrer-Policy but NOT the HTML-only
  frame headers;
- a caller that already set its own CSP (the mockup sandbox) keeps it, and the
  mockup CSP itself now restricts `frame-ancestors`.

Exercises the real `server.Handler._send` via a tiny header-capturing fake — no
socket, no network.
"""

import os
import sys
import unittest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server  # noqa: E402


class _CaptureHandler:
    def __init__(self):
        self.status = None
        self.headers_sent = {}
        self._chunks = []

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers_sent[key] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self._chunks.append(data)

    _send = server.Handler._send


class SecurityHeadersTest(unittest.TestCase):
    def _send(self, content_type, extra_headers=None):
        h = _CaptureHandler()
        h._send(200, b"<x>", content_type, extra_headers=extra_headers)
        return h.headers_sent

    def test_html_has_frame_and_referrer_headers(self):
        hd = self._send("text/html; charset=utf-8")
        self.assertEqual(hd.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(hd.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", hd.get("Content-Security-Policy", ""))

    def test_json_has_referrer_but_no_frame_headers(self):
        hd = self._send("application/json; charset=utf-8")
        self.assertEqual(hd.get("Referrer-Policy"), "no-referrer")
        self.assertNotIn("X-Frame-Options", hd)
        self.assertNotIn("Content-Security-Policy", hd)

    def test_caller_csp_is_not_overridden(self):
        # the mockup serves its own sandbox CSP; _send must keep it, not replace it
        hd = self._send("text/html", extra_headers=dict(server.MOCKUP_SEC_HEADERS))
        self.assertEqual(hd.get("Content-Security-Policy"), server.MOCKUP_CSP)
        self.assertEqual(hd.get("Referrer-Policy"), "no-referrer")

    def test_mockup_csp_restricts_frame_ancestors(self):
        self.assertIn("frame-ancestors 'self'", server.MOCKUP_CSP)


if __name__ == "__main__":
    unittest.main()
