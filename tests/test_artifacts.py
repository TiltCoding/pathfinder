#!/usr/bin/env python3
"""Offline tests for the Artifacts panel server endpoints (feat-17, stdlib unittest).

`/artifacts` lists browsable agent deliverables from <task>/mockups/ + /artifacts/;
`/artifact` serves one, confined to those dirs (realpath/commonpath guard, mirroring
_serve_mockup). These pin:
  * the listing reports files from both dirs with kind/dir/version, and ignores
    names outside the allowlist;
  * ACTIVE content (html/svg) is served with the mockup sandbox CSP + nosniff (so
    it can only run inside a sandbox iframe), inert content (md) gets nosniff only;
  * `download=1` forces a Content-Disposition attachment;
  * a bad / traversal name 404s and never escapes the task dirs.

No network, disk only inside a tempfile. Reuses the `_CapturingHandler` pattern
from tests/test_mockup_security.py.
"""

import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")

import server  # noqa: E402


class _CapturingHandler:
    def __init__(self, workspace):
        self.workspace = workspace
        self.status = None
        self.headers = {}
        self._chunks = []

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

    _send = server.Handler._send
    _json = server.Handler._json
    _artifacts_list = server.Handler._artifacts_list
    _serve_artifact = server.Handler._serve_artifact


_HTML = ("<!doctype html><html><body><h1>art</h1>"
         "<script>document.title='ok'</script></body></html>")


class ArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        self.slug = "art-task"
        self.mockups = os.path.join(self.ws.task_dir(self.slug), "mockups")
        self.artifacts = os.path.join(self.ws.task_dir(self.slug), "artifacts")
        os.makedirs(self.mockups, exist_ok=True)
        os.makedirs(self.artifacts, exist_ok=True)
        with open(os.path.join(self.mockups, "redesign.html"), "w", encoding="utf-8") as f:
            f.write(_HTML)
        with open(os.path.join(self.artifacts, "plan.v2.md"), "w", encoding="utf-8") as f:
            f.write("# plan v2")
        # a name outside the allowlist must be ignored
        with open(os.path.join(self.artifacts, "secret.exe"), "w", encoding="utf-8") as f:
            f.write("nope")

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _h(self):
        return _CapturingHandler(self.ws)

    def test_listing_spans_both_dirs_and_filters(self):
        h = self._h()
        h._artifacts_list  # bound
        data = h._artifacts_list(self.slug)
        names = {a["name"]: a for a in data["artifacts"]}
        self.assertIn("redesign.html", names)
        self.assertIn("plan.v2.md", names)
        self.assertNotIn("secret.exe", names)             # outside the allowlist
        self.assertEqual(names["redesign.html"]["dir"], "mockups")
        self.assertEqual(names["redesign.html"]["kind"], "html")
        self.assertTrue(names["redesign.html"]["active"])
        self.assertEqual(names["plan.v2.md"]["dir"], "artifacts")
        self.assertEqual(names["plan.v2.md"]["base"], "plan")
        self.assertEqual(names["plan.v2.md"]["version"], 2)
        self.assertFalse(names["plan.v2.md"]["active"])

    def test_active_html_carries_sandbox_csp(self):
        h = self._h()
        h._serve_artifact(self.slug, "redesign.html")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.headers.get("Content-Security-Policy"), server.MOCKUP_CSP)
        self.assertEqual(h.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn(b"<h1>art</h1>", h.body)

    def test_inert_doc_gets_nosniff_no_csp(self):
        h = self._h()
        h._serve_artifact(self.slug, "plan.v2.md")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertNotIn("Content-Security-Policy", h.headers)

    def test_download_forces_attachment(self):
        h = self._h()
        h._serve_artifact(self.slug, "plan.v2.md", download=True)
        self.assertEqual(h.status, 200)
        self.assertIn("attachment", h.headers.get("Content-Disposition", ""))
        self.assertIn("plan.v2.md", h.headers.get("Content-Disposition", ""))

    def test_bad_and_traversal_names_404(self):
        for bad in ("../../etc/passwd", "nope.exe", "", "a/b.html", "x.html/.."):
            h = self._h()
            h._serve_artifact(self.slug, bad)
            self.assertEqual(h.status, 404, "%r must 404" % bad)

    def test_missing_file_404(self):
        h = self._h()
        h._serve_artifact(self.slug, "absent.html")
        self.assertEqual(h.status, 404)

    def test_base_version_helper(self):
        self.assertEqual(server._artifact_base_version("redesign.v3.html"), ("redesign", 3))
        self.assertEqual(server._artifact_base_version("plan.md"), ("plan", None))


if __name__ == "__main__":
    unittest.main()
