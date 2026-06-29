#!/usr/bin/env python3
"""Offline tests for image attachments (stdlib unittest, no network, tempfile only).

Covers the three server pieces of the image-attachment feature:

  * `POST /attach` (`_attach`) — decode + validate + write bytes + return a ref:
      - a tiny valid PNG round-trips: 200, ok:true, safe `file` name, the bytes
        land under `<task>/attachments/<file>` byte-identical to the original;
      - a non-image mime is rejected 400 `error:"type"` and writes nothing;
      - an oversize payload is rejected 400 `error:"size"` (never 500);
      - undecodable base64 is rejected 400 `error:"decode"` (never 500).
  * `GET /image` (`_serve_image`) — confined read-only serve:
      - a saved image → 200 + `image/*` + `nosniff` + byte-identical body;
      - dot-dot / absolute / wrong-extension / missing names → 404 (no 500).
  * `_chat_post` — carries a *sanitized* `images` list onto the chat.jsonl line:
      - a mix of one valid ref + a malformed dict + a non-dict keeps only the
        valid ref; a post with no images writes no `images` key (unchanged).

Mirrors the fake-handler harness in tests/test_chat_anchor.py and
tests/test_mockup_security.py: bind the real unbound Handler methods on a socket
stub over a real `server.Workspace(tempfile.mkdtemp())`; call the methods
directly — no HTTP server.

Run:
    python -m unittest tests.test_attach -v
    python -m unittest discover -s tests
"""
import base64
import json
import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")

import server  # noqa: E402


# A minimal but structurally valid PNG: 8-byte signature + IHDR (1x1) + IDAT + IEND.
# The bytes only need to be a deterministic blob we can round-trip byte-for-byte;
# this is a real decodable 1x1 PNG.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _AttachHandler:
    """Drives the real `_attach`/`_serve_image`/`_chat_post` offline: real
    workspace + real `_send`/`_json`/`_append_signal`; socket and waker stubbed
    (we assert on disk + the captured response, not the wake plumbing)."""

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

    # stub the wake plumbing (no /wait long-poll in a unit test)
    def _wake(self, slug):
        pass

    # --- bound real handler methods ---
    _send = server.Handler._send
    _json = server.Handler._json
    _append_signal = server.Handler._append_signal
    _attach = server.Handler._attach
    _serve_image = server.Handler._serve_image
    _chat_post = server.Handler._chat_post

    # convenience: parse the JSON response body
    def json_body(self):
        return json.loads(self.body.decode("utf-8"))


class AttachBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        self.slug = "t-attach"

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _attach_dir(self, slug=None):
        return os.path.join(self.ws.task_dir(slug or self.slug), "attachments")

    def _do_attach(self, body, slug=None):
        h = _AttachHandler(self.ws)
        h._attach(slug or self.slug, body)
        return h

    def _do_serve(self, name, slug=None):
        h = _AttachHandler(self.ws)
        h._serve_image(slug or self.slug, name)
        return h


class AttachEndpointTest(AttachBase):
    def test_attach_happy_path(self):
        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        h = self._do_attach({"name": "shot.png", "mime": "image/png", "dataB64": b64})
        r = h.json_body()
        self.assertEqual(h.status, 200)
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("name"), "shot.png")
        self.assertEqual(r.get("mime"), "image/png")
        self.assertEqual(r.get("bytes"), len(_PNG_BYTES))
        # server-generated safe name
        self.assertIsNotNone(server.ATTACH_RE.match(r["file"]))
        # the decoded bytes landed on disk, byte-identical
        path = os.path.join(self._attach_dir(), r["file"])
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), _PNG_BYTES)

    def test_reject_wrong_mime(self):
        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        h = self._do_attach({"name": "doc.pdf", "mime": "application/pdf", "dataB64": b64})
        r = h.json_body()
        self.assertEqual(h.status, 400)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), "type")
        # nothing written (the attachments dir was never created)
        self.assertFalse(os.path.isdir(self._attach_dir()))

    def test_reject_oversize(self):
        # A base64 string long enough that len(b64)*3//4 exceeds the cap trips the
        # pre-decode guard — without allocating a 5 MB+ decoded buffer.
        oversize_b64 = "A" * ((server.ATTACH_MAX_BYTES + 16) * 4 // 3 + 8)
        h = self._do_attach({"name": "big.png", "mime": "image/png", "dataB64": oversize_b64})
        r = h.json_body()
        self.assertEqual(h.status, 400)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), "size")
        self.assertFalse(os.path.isdir(self._attach_dir()))

    def test_reject_oversize_post_decode(self):
        # Real bytes just over the cap: the pre-decode upper-bound guard catches
        # it, but either way it is a clean 400 size, never a 500.
        big = b"\x00" * (server.ATTACH_MAX_BYTES + 1)
        b64 = base64.b64encode(big).decode("ascii")
        h = self._do_attach({"name": "big.png", "mime": "image/png", "dataB64": b64})
        r = h.json_body()
        self.assertEqual(h.status, 400)
        self.assertEqual(r.get("error"), "size")

    def test_reject_magic_mismatch(self):
        # Valid base64 + an allowed image mime, but the decoded bytes are not
        # actually that image type: the magic-byte check rejects it 400 "format"
        # and writes nothing (the trusted-mime gap is closed).
        b64 = base64.b64encode(b"this is plainly not an image").decode("ascii")
        h = self._do_attach({"name": "fake.png", "mime": "image/png", "dataB64": b64})
        r = h.json_body()
        self.assertEqual(h.status, 400)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), "format")
        self.assertFalse(os.path.isdir(self._attach_dir()))

    def test_accept_non_png_by_magic(self):
        # A non-PNG format whose header matches its declared type passes the check
        # (the guard is per-format, not PNG-only): a GIF89a header → 200, written.
        gif = b"GIF89a" + b"\x00" * 16
        b64 = base64.b64encode(gif).decode("ascii")
        h = self._do_attach({"name": "x.gif", "mime": "image/gif", "dataB64": b64})
        r = h.json_body()
        self.assertEqual(h.status, 200)
        self.assertTrue(r.get("ok"))
        self.assertTrue(r["file"].endswith(".gif"))
        with open(os.path.join(self._attach_dir(), r["file"]), "rb") as f:
            self.assertEqual(f.read(), gif)

    def test_reject_undecodable_base64(self):
        h = self._do_attach({"name": "x.png", "mime": "image/png", "dataB64": "!!!not base64!!!"})
        r = h.json_body()
        self.assertEqual(h.status, 400)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), "decode")
        self.assertFalse(os.path.isdir(self._attach_dir()))

    def test_attach_non_string_mime_no_500(self):
        # A non-string (unhashable) mime must not reach ATTACH_MIME_EXT.get(),
        # which would raise TypeError: unhashable type → 500. It is a clean 400
        # error:"type", and the call never raises.
        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        for bad_mime in (["image/png"], {"image/png": 1}):
            h = self._do_attach({"name": "x.png", "mime": bad_mime, "dataB64": b64})
            r = h.json_body()
            self.assertEqual(h.status, 400, bad_mime)
            self.assertFalse(r.get("ok"), bad_mime)
            self.assertEqual(r.get("error"), "type", bad_mime)
            self.assertFalse(os.path.isdir(self._attach_dir()), bad_mime)

    def test_attach_non_string_datab64(self):
        # A non-string dataB64 would break b64decode → 500. It must be a clean
        # 400 (error:"decode"), and the call never raises.
        for bad_b64 in (["AAAA"], 12345):
            h = self._do_attach({"name": "x.png", "mime": "image/png", "dataB64": bad_b64})
            r = h.json_body()
            self.assertEqual(h.status, 400, bad_b64)
            self.assertFalse(r.get("ok"), bad_b64)
            self.assertEqual(r.get("error"), "decode", bad_b64)
            self.assertFalse(os.path.isdir(self._attach_dir()), bad_b64)


class ImageServeTest(AttachBase):
    def _save_one(self):
        """Attach a valid PNG and return its server filename."""
        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        h = self._do_attach({"name": "shot.png", "mime": "image/png", "dataB64": b64})
        return h.json_body()["file"]

    def test_serve_round_trip(self):
        name = self._save_one()
        h = self._do_serve(name)
        self.assertEqual(h.status, 200)
        ctype = h.headers.get("Content-Type", "")
        self.assertTrue(ctype.startswith("image/"), ctype)
        self.assertEqual(h.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(h.body, _PNG_BYTES)

    def test_serve_traversal_dotdot(self):
        h = self._do_serve("../../etc/passwd")
        self.assertEqual(h.status, 404)

    def test_serve_absolute_path(self):
        # An absolute path must not be honoured (regex rejects the separators).
        abs_name = os.path.abspath(__file__)
        h = self._do_serve(abs_name)
        self.assertEqual(h.status, 404)

    def test_serve_wrong_extension(self):
        for bad in ("x.txt", "x.svg"):
            h = self._do_serve(bad)
            self.assertEqual(h.status, 404, bad)

    def test_serve_missing_but_valid_name(self):
        # Passes the regex but no such file exists → 404, not 500.
        h = self._do_serve("att-deadbeef.png")
        self.assertEqual(h.status, 404)

    def test_serve_empty_name(self):
        h = self._do_serve("")
        self.assertEqual(h.status, 404)


class ChatImagesTest(AttachBase):
    def _do_chat(self, body, slug=None):
        h = _AttachHandler(self.ws)
        slug = slug or self.slug
        self.ws.ensure_task(slug)
        h._chat_post(slug, body)
        return h

    def _last_msg(self, slug=None):
        path = self.ws.task_file(slug or self.slug, "chat.jsonl")
        with open(path, "r", encoding="utf-8", newline="") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        return json.loads(lines[-1])

    def test_chat_sanitizes_images(self):
        images = [
            {"file": "att-deadbeef.png", "name": "a.png", "mime": "image/png"},
            {"file": "../evil.png"},        # malformed: bad filename → dropped
            "not-a-dict",                    # non-dict → dropped
            {"name": "b.png"},               # missing file → dropped
        ]
        h = self._do_chat({"text": "look here", "images": images})
        self.assertEqual(h.status, 200)
        m = self._last_msg()
        self.assertIn("images", m)
        self.assertEqual(len(m["images"]), 1)
        self.assertEqual(m["images"][0]["file"], "att-deadbeef.png")
        self.assertEqual(m["images"][0]["name"], "a.png")
        self.assertEqual(m["images"][0]["mime"], "image/png")

    def test_chat_no_images_unchanged(self):
        h = self._do_chat({"text": "plain message"}, slug="t-plain")
        self.assertEqual(h.status, 200)
        m = self._last_msg("t-plain")
        self.assertNotIn("images", m)

    def test_chat_all_images_invalid_drops_key(self):
        h = self._do_chat({"text": "msg", "images": [{"file": "../x"}, 5]}, slug="t-bad")
        self.assertEqual(h.status, 200)
        m = self._last_msg("t-bad")
        self.assertNotIn("images", m)

    def test_chat_image_only_accepted(self):
        # Empty text + a valid image is the screenshot-paste path: accepted (not
        # 400), stored with text:"" and the sanitized image present.
        images = [{"file": "att-deadbeef.png", "name": "a.png", "mime": "image/png"}]
        h = self._do_chat({"text": "", "images": images}, slug="t-imgonly")
        self.assertEqual(h.status, 200)
        m = self._last_msg("t-imgonly")
        self.assertEqual(m["text"], "")
        self.assertIn("images", m)
        self.assertEqual(len(m["images"]), 1)
        self.assertEqual(m["images"][0]["file"], "att-deadbeef.png")

    def test_chat_empty_no_images_rejected(self):
        # No text AND no valid images → still 400 "empty message", nothing written.
        slug = "t-empty"
        self.ws.ensure_task(slug)
        path = self.ws.task_file(slug, "chat.jsonl")
        self.assertFalse(os.path.exists(path))
        for body in ({"text": ""}, {"text": "", "images": [{"file": "../evil.png"}, 5]}):
            h = _AttachHandler(self.ws)
            h._chat_post(slug, body)
            self.assertEqual(h.status, 400)
            self.assertEqual(h.json_body().get("error"), "empty message")
            self.assertFalse(os.path.exists(path))

    def test_chat_name_mime_capped(self):
        # A valid file but an oversized name/mime: stored values are capped to 200.
        images = [{"file": "att-deadbeef.png", "name": "a" * 500, "mime": "b" * 500}]
        h = self._do_chat({"text": "", "images": images}, slug="t-cap")
        self.assertEqual(h.status, 200)
        m = self._last_msg("t-cap")
        self.assertEqual(len(m["images"][0]["name"]), 200)
        self.assertEqual(len(m["images"][0]["mime"]), 200)


if __name__ == "__main__":
    unittest.main()
