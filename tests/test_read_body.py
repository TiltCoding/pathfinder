#!/usr/bin/env python3
"""Offline tests for the global POST body cap in `Handler._read_body`
(stdlib unittest, no network).

`_read_body` is the single choke point every POST route reads its body through.
These pin its contract:

  * a Content-Length over MAX_BODY_BYTES is rejected 413 *without* reading the
    body into memory (the DoS/OOM guard) and returns None so the caller stops;
  * a body that arrives short of Content-Length is rejected 400 ("incomplete
    body") rather than silently passed off as an empty/partial object;
  * a complete, well-formed body parses to its dict;
  * a genuinely malformed JSON body stays graceful → {} (callers handle it);
  * a non-object JSON body normalizes to {} (no 500).

Mirrors the fake-handler harness style in tests/test_attach.py: bind the real
unbound Handler methods on a stub with a `headers` dict and a BytesIO `rfile`.

Run:
    python -m unittest tests.test_read_body -v
    python -m unittest discover -s tests
"""
import io
import json
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")

import server  # noqa: E402


class _BodyHandler:
    """Drives the real `_read_body`/`_send`/`_json` offline: a `headers` dict, a
    BytesIO `rfile`, and a captured response. `read_length` lets a test simulate a
    client that under-delivers (fewer bytes than Content-Length claims)."""

    def __init__(self, content_length, payload=b"", read_length=None):
        self.headers = {"Content-Length": str(content_length)}
        self.rfile = io.BytesIO(payload if read_length is None
                                else payload[:read_length])
        self.status = None
        self._chunks = []
        self.close_connection = False

    # --- BaseHTTPRequestHandler surface used by _send ---
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

    # --- bound real handler methods ---
    _send = server.Handler._send
    _json = server.Handler._json
    _read_body = server.Handler._read_body


class ReadBodyTest(unittest.TestCase):
    def test_oversize_rejected_without_reading(self):
        # Claim a body far over the cap but supply no bytes: the guard must fire on
        # Content-Length alone, never touching rfile (so .read isn't even reached).
        claimed = server.MAX_BODY_BYTES + 1
        h = _BodyHandler(claimed, payload=b"")
        out = h._read_body()
        self.assertIsNone(out)
        self.assertEqual(h.status, 413)
        self.assertEqual(h.json_body().get("error"), "too large")
        self.assertTrue(h.close_connection)
        # nothing was consumed from rfile
        self.assertEqual(h.rfile.tell(), 0)

    def test_incomplete_body_rejected(self):
        full = json.dumps({"slug": "x", "pad": "y" * 100}).encode("utf-8")
        # advertise the full length but only deliver half the bytes
        h = _BodyHandler(len(full), payload=full, read_length=len(full) // 2)
        out = h._read_body()
        self.assertIsNone(out)
        self.assertEqual(h.status, 400)
        self.assertEqual(h.json_body().get("error"), "incomplete body")

    def test_complete_body_parses(self):
        full = json.dumps({"slug": "abc", "n": 3}).encode("utf-8")
        h = _BodyHandler(len(full), payload=full)
        out = h._read_body()
        self.assertEqual(out, {"slug": "abc", "n": 3})
        self.assertIsNone(h.status)   # no error response sent

    def test_empty_body_is_empty_dict(self):
        h = _BodyHandler(0)
        self.assertEqual(h._read_body(), {})

    def test_malformed_json_is_graceful(self):
        bad = b"{not valid json"
        h = _BodyHandler(len(bad), payload=bad)
        self.assertEqual(h._read_body(), {})   # graceful, not an error response
        self.assertIsNone(h.status)

    def test_non_object_json_normalizes(self):
        arr = b"[1, 2, 3]"
        h = _BodyHandler(len(arr), payload=arr)
        self.assertEqual(h._read_body(), {})


if __name__ == "__main__":
    unittest.main()
