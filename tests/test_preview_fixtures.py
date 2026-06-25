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

import json
import os
import re
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_REPO, "templates", "fixtures")

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


if __name__ == "__main__":
    unittest.main()
