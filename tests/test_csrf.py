#!/usr/bin/env python3
"""Offline tests for the CSRF / DNS-rebinding guard on state-changing POSTs.

`do_POST` mutates (`/submit`, `/chat`, `/attach`, `/signal`, `/draft`,
`/settings`) and the server listens only on 127.0.0.1 with a port derived from
`sha1(root)` — so a foreign page the user is merely browsing could POST to it
cross-site (CORS-simple `text/plain`, no preflight) or via DNS-rebinding. The
guard `_origin_allowed` requires:

  * the Host header to name loopback (127.0.0.1 / localhost), and our port when
    the header carries one;
  * any Origin header to resolve to that same loopback origin.

These pin that a same-origin POST passes the guard (it reaches the normal slug
check) while a foreign Host or foreign Origin is refused with 403 *before* any
mutation. Driven through the real `do_POST` on a capturing stand-in (no socket),
mirroring tests/test_settings.py.

Run:
    python -m unittest tests.test_csrf -v
    python -m unittest discover -s tests   # full suite
"""

import json
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server  # noqa: E402

_PORT = 8473


class _FakeRFile:
    def __init__(self, raw):
        self._raw = raw

    def read(self, n):
        return self._raw[:n]


class _FakeServer:
    server_address = ("127.0.0.1", _PORT)


class _CapturingHandler:
    """Drives the real do_POST and records the response; carries request
    headers (Host/Origin) and a fake .server so the guard can read the port."""

    def __init__(self, path, body=None, headers=None):
        self.path = path
        self.status = None
        self.headers = dict(headers or {})
        self.server = _FakeServer()
        self._chunks = []
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers.setdefault("Content-Length", str(len(raw)))
        self.rfile = _FakeRFile(raw)

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self._chunks.append(data)

    def json_body(self):
        return json.loads(b"".join(self._chunks).decode("utf-8"))

    do_POST = server.Handler.do_POST
    _origin_allowed = server.Handler._origin_allowed
    _send = server.Handler._send
    _json = server.Handler._json
    _read_body = server.Handler._read_body

    def post(self):
        self.do_POST()
        return self.status, self.json_body()


def _post(headers, path="/draft", body=None):
    return _CapturingHandler(path, body=body or {}, headers=headers).post()


class CsrfGuardTest(unittest.TestCase):
    # --- allowed: guard passes -> request reaches the normal slug check (400) ---
    def test_loopback_host_passes_to_slug_check(self):
        status, data = _post({"Host": "127.0.0.1:%d" % _PORT})
        self.assertEqual(status, 400)
        self.assertIn("slug", json.dumps(data))   # passed origin, hit slug guard

    def test_localhost_host_passes(self):
        status, data = _post({"Host": "localhost:%d" % _PORT})
        self.assertEqual(status, 400)
        self.assertIn("slug", json.dumps(data))

    def test_loopback_host_and_origin_passes(self):
        status, data = _post({"Host": "127.0.0.1:%d" % _PORT,
                              "Origin": "http://127.0.0.1:%d" % _PORT})
        self.assertEqual(status, 400)
        self.assertIn("slug", json.dumps(data))

    # --- refused: 403 before any mutation ---
    def test_foreign_host_refused(self):
        status, data = _post({"Host": "evil.example.com:%d" % _PORT})
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "forbidden")

    def test_missing_host_refused(self):
        status, data = _post({})
        self.assertEqual(status, 403)

    def test_foreign_origin_refused(self):
        status, data = _post({"Host": "127.0.0.1:%d" % _PORT,
                              "Origin": "https://evil.example.com"})
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "forbidden")

    def test_wrong_port_host_refused(self):
        status, data = _post({"Host": "127.0.0.1:9999"})
        self.assertEqual(status, 403)

    def test_settings_branch_also_guarded(self):
        # /settings carries no slug; a foreign Host must still 403 (the guard
        # runs before the /settings branch).
        status, data = _post({"Host": "evil.example.com"},
                             path="/settings", body={"lang": "ru"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
