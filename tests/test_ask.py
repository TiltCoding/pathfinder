#!/usr/bin/env python3
"""Offline tests for the `/ask` command's server-side surface (stdlib unittest).

`/ask` rides almost entirely on existing contracts; the only mechanical server
changes worth pinning are:

  * the hub `kind` field — `_build_hub`/`_hub_run` carry `state.json.kind` (e.g.
    "ask") into each run card, while tasks without `kind` (/feature, /improve)
    stay backward-compatible (`kind is None`);
  * the `/mockup` route that serves the answer's self-contained visualizations
    (`infographic.html`, `process.svg`) with the right Content-Type and rejects
    names outside `MOCKUP_RE` (Cyrillic / spaces / `..` traversal).

A couple of structural smoke checks for the skill/agent are included but skip
gracefully when those files are still being authored by a parallel work-stream
(the full run happens at VERIFY once every stream lands).

No network and no disk outside a tempfile. Run with:
    python3 tests/test_ask.py
    python3 -m unittest tests.test_ask
"""

import json
import os
import sys
import tempfile
import time
import unittest

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server     # noqa: E402


def _now_iso_utc():
    """A fresh ISO-8601/Z timestamp — what state.json updatedAt carries."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _make_handler(workspace):
    """A Handler bound to `workspace` without the HTTP/socket machinery.

    Mirrors `tests/test_hub.py`: the hub methods read only `self.workspace` and
    class caches, so we drive them via `__new__` — fully offline, no port.
    """
    h = server.Handler.__new__(server.Handler)
    h.workspace = workspace
    return h


class _CapturingHandler:
    """A minimal stand-in that drives `Handler._serve_mockup` and records the
    response, capturing status, headers and body without a real socket.

    `_serve_mockup` only reaches out via `_json`/`_send`, which call
    `send_response`/`send_header`/`end_headers`/`self.wfile.write` — all stubbed
    here. We reuse the real (unbound) `_serve_mockup`/`_send`/`_json` methods.
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

    def serve_mockup(self, slug, name):
        self._serve_mockup(slug, name)
        return self.status, self.headers.get("Content-Type", ""), self.body


class HubKindTest(unittest.TestCase):
    """`_build_hub`/`_hub_run` carry state.json `kind` into the run card, and
    stay backward-compatible for tasks that have no `kind`."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        server.Handler._hub_cache.clear()
        self.handler = _make_handler(self.ws)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        server.Handler._hub_cache.clear()

    def _task(self, slug, state):
        _write_json(self.ws.task_file(slug, "state.json"), state)

    def test_ask_kind_flows_into_run(self):
        self._task("ask-q1", {
            "slug": "ask-q1", "kind": "ask", "phase": "ANSWER",
            "updatedAt": _now_iso_utc(),
        })
        runs = {r["slug"]: r for r in self.handler._build_hub()["runs"]}
        self.assertIn("ask-q1", runs)
        self.assertEqual(runs["ask-q1"]["kind"], "ask")

    def test_missing_kind_is_none_backcompat(self):
        # A /feature-style task with no `kind` must still render: kind is None.
        self._task("feat-x", {
            "slug": "feat-x", "phase": "IMPLEMENT",
            "updatedAt": _now_iso_utc(),
        })
        runs = {r["slug"]: r for r in self.handler._build_hub()["runs"]}
        self.assertIn("feat-x", runs)
        self.assertIn("kind", runs["feat-x"])          # key always present
        self.assertIsNone(runs["feat-x"].get("kind"))  # but None without state

    def test_kind_via_hub_run_directly(self):
        self._task("ask-q2", {
            "slug": "ask-q2", "kind": "ask", "phase": "ANSWER",
            "updatedAt": _now_iso_utc(),
        })
        run = self.handler._hub_run("ask-q2", time.time())
        self.assertEqual(run["kind"], "ask")


class MockupRouteTest(unittest.TestCase):
    """`_serve_mockup` / `GET /mockup`: serves the answer's visualizations with
    the right Content-Type and rejects names outside MOCKUP_RE."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        self.slug = "ask-viz"
        self.mockups = os.path.join(self.ws.task_dir(self.slug), "mockups")
        os.makedirs(self.mockups, exist_ok=True)
        # tiny self-contained fixtures (no CDN), matching the demo contract
        with open(os.path.join(self.mockups, "infographic.html"), "w",
                  encoding="utf-8") as f:
            f.write("<!doctype html><html><body>инфографика</body></html>")
        with open(os.path.join(self.mockups, "process.svg"), "w",
                  encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg"></svg>')

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _serve(self, name):
        return _CapturingHandler(self.ws).serve_mockup(self.slug, name)

    def test_html_content_type(self):
        status, ctype, body = self._serve("infographic.html")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn("инфографика".encode("utf-8"), body)

    def test_svg_content_type(self):
        status, ctype, body = self._serve("process.svg")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("image/svg+xml"))
        self.assertIn(b"<svg", body)

    def test_rejects_cyrillic_name(self):
        status, _ctype, _body = self._serve("схема.svg")
        self.assertEqual(status, 404)

    def test_rejects_name_with_space(self):
        status, _ctype, _body = self._serve("my file.html")
        self.assertEqual(status, 404)

    def test_rejects_traversal(self):
        # A `..` traversal name is outside MOCKUP_RE -> rejected before any read.
        status, _ctype, _body = self._serve("../../etc/passwd")
        self.assertEqual(status, 404)

    def test_rejects_bad_extension(self):
        status, _ctype, _body = self._serve("answer.txt")
        self.assertEqual(status, 404)

    def test_missing_file_is_404(self):
        # Valid name, but no such file -> 404 (never raises / leaks).
        status, _ctype, _body = self._serve("absent.html")
        self.assertEqual(status, 404)


class SkillSmokeTest(unittest.TestCase):
    """Structural smoke checks for the /ask skill + agent. These skip when the
    files are still being authored by a parallel work-stream; the full run lands
    at VERIFY once every stream is merged."""

    def test_skill_frontmatter_name(self):
        skill = os.path.join(_REPO, "skills", "ask", "SKILL.md")
        if not os.path.isfile(skill):
            self.skipTest("skills/ask/SKILL.md not authored yet")
        with open(skill, "r", encoding="utf-8") as f:
            head = f.read(2000)
        self.assertIn("name: ask", head)

    def test_researcher_is_read_only(self):
        agent = os.path.join(_REPO, "agents", "ask-researcher.md")
        if not os.path.isfile(agent):
            self.skipTest("agents/ask-researcher.md not authored yet")
        with open(agent, "r", encoding="utf-8") as f:
            text = f.read()
        # The `tools:` frontmatter line must not grant write access.
        tools_line = next(
            (ln for ln in text.splitlines() if ln.strip().startswith("tools:")),
            "")
        self.assertNotIn("Write", tools_line)
        self.assertNotIn("Edit", tools_line)


if __name__ == "__main__":
    unittest.main()
