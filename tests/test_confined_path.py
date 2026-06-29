#!/usr/bin/env python3
"""Offline tests for `_aipf.confined_path` — the single traversal guard reused
by every confined-serve endpoint (`_serve_mockup`/`_serve_image`/`_serve_artifact`/
`_changes_file`/`_knowledge_file`/`_attach`).

Pins that the shared helper:
  * returns the real, absolute path for a plain name (and a nested sub-path)
    that stays inside the base dir;
  * returns None for `..` traversal, for an absolute path that overrides the
    base, and for a symlink that escapes the base (defence in depth);
  * collapses the realpath idempotently (base passed already-real still works).

No network and no disk outside a tempfile. Mirrors the test stubs in this dir.

Run with:
    python3 -m unittest tests.test_confined_path
    python3 -m unittest discover -s tests   # full suite
"""

import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")

import _aipf     # noqa: E402


def _can_symlink(base):
    try:
        tgt = os.path.join(base, "_probe_target")
        lnk = os.path.join(base, "_probe_link")
        open(tgt, "w").close()
        os.symlink(tgt, lnk)
        os.remove(lnk)
        os.remove(tgt)
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False


class TestConfinedPath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.base, "sub"), exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_plain_name_confined(self):
        got = _aipf.confined_path(self.base, "file.txt")
        self.assertEqual(got, os.path.join(self.base, "file.txt"))

    def test_nested_subpath_confined(self):
        got = _aipf.confined_path(self.base, os.path.join("sub", "x.md"))
        self.assertEqual(got, os.path.join(self.base, "sub", "x.md"))

    def test_dotdot_traversal_rejected(self):
        self.assertIsNone(_aipf.confined_path(self.base, os.path.join("..", "etc")))
        self.assertIsNone(
            _aipf.confined_path(self.base, os.path.join("sub", "..", "..", "x")))

    def test_absolute_override_rejected(self):
        # an absolute name replaces the base in os.path.join -> must be rejected
        outside = os.path.realpath(os.path.join(self.base, os.pardir))
        self.assertIsNone(_aipf.confined_path(self.base, outside))

    def test_exact_base_is_confined(self):
        # name "" resolves to the base itself, which is inside the base
        self.assertEqual(_aipf.confined_path(self.base, ""), self.base)

    @unittest.skipUnless(_can_symlink(tempfile.gettempdir()),
                         "symlink creation not permitted on this platform/user")
    def test_symlink_escape_rejected(self):
        outside_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(outside_dir, ignore_errors=True))
        secret = os.path.join(outside_dir, "secret.txt")
        open(secret, "w").close()
        link = os.path.join(self.base, "escape")
        os.symlink(secret, link)
        # realpath resolves the symlink to the outside target -> not confined
        self.assertIsNone(_aipf.confined_path(self.base, "escape"))


if __name__ == "__main__":
    unittest.main()
