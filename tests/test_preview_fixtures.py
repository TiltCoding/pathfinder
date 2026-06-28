#!/usr/bin/env python3
"""Offline smoke test for the preview-dashboard fixtures (stdlib unittest).

The preview harness (`scripts/preview.py`, `python dev.py preview`) installs the
fixtures under `templates/fixtures/` into `.workflow/tasks/` and stamps the
current `templates/dashboard.html` as each task's `index.html`, so you can eyeball
the dashboard in every phase at any time.

These checks pin the *data contract* of those fixtures so a malformed one fails
CI instead of rendering a blank page:

  * every fixture's `state.json` / `dashboard.json` is valid JSON with the keys
    the server and dashboard read (`slug`, `title`, `phase`);
  * the `slug` matches the directory name (the server keys tasks by slug);
  * `chat.jsonl` / `telemetry.jsonl` are line-delimited JSON (one object/line);
  * any `demo.variants[].file` referenced for the `/ask` visualizations exists
    under that fixture's `mockups/` and has a server-safe name.

No network, no disk writes — reads the committed fixtures in place.

Run with:
    python3 -m unittest tests.test_preview_fixtures
    python3 -m unittest discover -s tests   # full suite
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_REPO, "templates", "fixtures")

sys.path.insert(0, os.path.join(_REPO, "scripts"))
import preview  # noqa: E402  (scripts/preview.py — the harness under test)


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

# mirrors the server's mockup name guard: no spaces, no traversal, ascii only
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _fixture_dirs():
    if not os.path.isdir(_FIXTURES):
        return []
    return sorted(
        n for n in os.listdir(_FIXTURES)
        if os.path.isdir(os.path.join(_FIXTURES, n))
    )


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class PreviewFixturesTest(unittest.TestCase):
    def test_at_least_one_fixture(self):
        self.assertTrue(_fixture_dirs(),
                        "no preview fixtures under templates/fixtures/")

    def test_each_fixture_is_well_formed(self):
        for name in _fixture_dirs():
            with self.subTest(fixture=name):
                d = os.path.join(_FIXTURES, name)

                # required JSON files, valid + required keys + slug match
                for fname in ("state.json", "dashboard.json"):
                    path = os.path.join(d, fname)
                    self.assertTrue(os.path.isfile(path),
                                    f"{name}/{fname} missing")
                    obj = _load_json(path)
                    self.assertIsInstance(obj, dict, f"{name}/{fname} not an object")
                    for key in ("slug", "title", "phase"):
                        self.assertIn(key, obj, f"{name}/{fname} missing '{key}'")
                    self.assertEqual(obj["slug"], name,
                                     f"{name}/{fname} slug != directory name")

                # line-delimited JSON files: every non-blank line parses
                for fname in ("chat.jsonl", "telemetry.jsonl"):
                    path = os.path.join(d, fname)
                    if not os.path.isfile(path):
                        continue
                    with open(path, encoding="utf-8") as f:
                        for i, line in enumerate(f, 1):
                            if line.strip():
                                try:
                                    json.loads(line)
                                except ValueError as e:
                                    self.fail(f"{name}/{fname}:{i} bad JSON: {e}")

                # /ask visualizations: referenced mockups exist + safe names
                dash = _load_json(os.path.join(d, "dashboard.json"))
                for vr in (dash.get("demo") or {}).get("variants", []):
                    fileref = vr.get("file") or ""
                    self.assertRegex(fileref, _SAFE_NAME,
                                     f"{name} demo variant file unsafe: {fileref!r}")
                    self.assertTrue(
                        os.path.isfile(os.path.join(d, "mockups", fileref)),
                        f"{name}/mockups/{fileref} referenced but missing")


class PreviewParityTest(unittest.TestCase):
    """The guarantee behind the user-facing promise: the dashboard the preview
    shows is byte-identical to the dashboard agents render in a live run.

    `preview.install()` stamps `templates/dashboard.html` verbatim as each task's
    `index.html` (a plain copy — the same step the skills' feedback-loop does for a
    live run). This test pins that to a checked invariant: if anyone replaces the
    copy with a transform (minify, token-inject), the stamped `index.html` would no
    longer equal the template and this fails in CI instead of drifting silently.

    Runs `install()` in an isolated tempfile root (monkeypatching `TASKS_DIR` and
    neutralizing `_sweep_legacy`) so it never touches the live `.workflow/` store.
    """

    def test_stamped_index_is_byte_identical_to_template(self):
        tmp = tempfile.mkdtemp(prefix="preview-parity-")
        tasks = os.path.join(tmp, ".workflow", "tasks")
        orig_tasks, orig_sweep = preview.TASKS_DIR, preview._sweep_legacy
        preview.TASKS_DIR = tasks
        preview._sweep_legacy = lambda: None  # don't reach into the live store
        try:
            names = preview.install()
        finally:
            preview.TASKS_DIR, preview._sweep_legacy = orig_tasks, orig_sweep
            # install() prints progress; nothing else to clean but the tmp tree
        try:
            self.assertTrue(names, "preview.install() stamped no fixtures")
            template_hash = _sha256(preview.TEMPLATE)
            for name in names:
                idx = os.path.join(tasks, name, "index.html")
                self.assertTrue(os.path.isfile(idx),
                                f"{name}/index.html was not stamped")
                self.assertEqual(
                    _sha256(idx), template_hash,
                    f"{name}/index.html differs from templates/dashboard.html — "
                    f"preview would no longer match the live dashboard")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
