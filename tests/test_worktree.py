#!/usr/bin/env python3
"""Offline tests for the worktree CLI helper (stdlib unittest only).

Covers the pure argument parser (`worktree.build_parser`), the filesystem-only
shared-store symlink (`worktree._ensure_workflow_symlink`), the anti-traversal
slug guard delegated to `_aipf.safe_slug`, and — as a cheap bonus — the
`git worktree list --porcelain` parser (`worktree.list_worktrees`) via a fake
`_git`.

Git is never invoked: every case here is either a pure function or filesystem
work inside a tempfile. No network and no disk outside a tempfile.

Run with:
    python3 -m unittest tests.test_worktree
    python3 -m unittest discover -s tests   # full suite

Note: tempdirs are passed through ``os.path.realpath`` because on macOS
``tempfile.mkdtemp`` returns a path under ``/var`` (a symlink to ``/private/var``)
while ``_aipf.workflow_base`` does not resolve symlinks; the symlink-equality
check in ``_ensure_workflow_symlink`` compares ``realpath(link)`` against the
unresolved target, so canonical roots are required to exercise the idempotent
"symlink ok" path on the real code (production behaviour, unchanged).
"""

import os
import shutil
import sys
import tempfile
import unittest

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import _aipf      # noqa: E402
import worktree   # noqa: E402


def _symlink_supported():
    """Probe whether this platform can create symlinks (privilege-aware).

    On Windows, ``os.symlink`` raises ``OSError`` (WinError 1314) without the
    ``SeCreateSymbolicLink`` privilege, but succeeds under Developer Mode/admin —
    so we probe the real capability rather than branching on ``os.name``.
    """
    tmp = tempfile.mkdtemp()
    try:
        target = os.path.join(tmp, "target")
        link = os.path.join(tmp, "link")
        open(target, "w").close()
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# Computed once at import time so the probe does not run per-test.
_SYMLINKS = _symlink_supported()


class BuildParserTest(unittest.TestCase):
    """`worktree.build_parser`: pure argparse wiring, no git involved."""

    def setUp(self):
        self.parser = worktree.build_parser()

    def test_add_minimal_defaults(self):
        args = self.parser.parse_args(["add", "my-slug"])
        self.assertIs(args.func, worktree.cmd_add)
        self.assertEqual(args.slug, "my-slug")
        self.assertIsNone(args.base)
        self.assertIsNone(args.branch)

    def test_add_with_base_and_branch(self):
        args = self.parser.parse_args(
            ["add", "s", "--base", "develop", "--branch", "b"])
        self.assertIs(args.func, worktree.cmd_add)
        self.assertEqual(args.slug, "s")
        self.assertEqual(args.base, "develop")
        self.assertEqual(args.branch, "b")

    def test_remove_default_force_false(self):
        args = self.parser.parse_args(["remove", "s"])
        self.assertIs(args.func, worktree.cmd_remove)
        self.assertEqual(args.slug, "s")
        self.assertFalse(args.force)

    def test_remove_force_flag(self):
        args = self.parser.parse_args(["remove", "s", "--force"])
        self.assertIs(args.func, worktree.cmd_remove)
        self.assertTrue(args.force)

    def test_list(self):
        args = self.parser.parse_args(["list"])
        self.assertIs(args.func, worktree.cmd_list)

    def test_add_without_slug_exits(self):
        # `add` requires a positional slug; argparse aborts via SystemExit.
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["add"])

    def test_unknown_command_exits(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["bogus-cmd"])

    def test_no_args_has_no_func(self):
        # With no subcommand, `func` is unset so main() prints help instead.
        args = self.parser.parse_args([])
        self.assertIsNone(getattr(args, "func", None))


class EnsureWorkflowSymlinkTest(unittest.TestCase):
    """`worktree._ensure_workflow_symlink`: filesystem only, never raises."""

    def setUp(self):
        # realpath() so the canonical target matches realpath(link); see module
        # docstring (macOS /var -> /private/var) — otherwise the idempotency
        # check would never report "symlink ok".
        self.main_root = os.path.realpath(tempfile.mkdtemp())
        self.wt_dir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.main_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.wt_dir, ignore_errors=True)
        self.link = os.path.join(self.wt_dir, ".workflow")
        self.target = _aipf.workflow_base(self.main_root)

    @unittest.skipUnless(_SYMLINKS, "no symlink privilege on this platform")
    def test_creates_symlink(self):
        result = worktree._ensure_workflow_symlink(self.main_root, self.wt_dir)
        self.assertIn("symlink created", result)
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.path.realpath(self.link), self.target)

    @unittest.skipUnless(_SYMLINKS, "no symlink privilege on this platform")
    def test_idempotent_second_call(self):
        # Key brief case: re-running `add` must not re-create or warn — the
        # existing correct symlink is recognised and left alone.
        first = worktree._ensure_workflow_symlink(self.main_root, self.wt_dir)
        self.assertIn("symlink created", first)
        second = worktree._ensure_workflow_symlink(self.main_root, self.wt_dir)
        self.assertIn("symlink ok", second)
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.path.realpath(self.link), self.target)

    @unittest.skipUnless(_SYMLINKS, "no symlink privilege on this platform")
    def test_foreign_symlink_warns_and_untouched(self):
        other = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        os.symlink(other, self.link)
        result = worktree._ensure_workflow_symlink(self.main_root, self.wt_dir)
        self.assertIn("warning", result)
        self.assertIn("not the shared store", result)
        # Untouched: still a symlink pointing at the foreign path.
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.path.realpath(self.link), other)

    def test_non_symlink_left_as_is(self):
        os.makedirs(self.link)
        result = worktree._ensure_workflow_symlink(self.main_root, self.wt_dir)
        self.assertIn("warning", result)
        self.assertIn("left as-is", result)
        # Not deleted, not converted to a symlink.
        self.assertTrue(os.path.isdir(self.link))
        self.assertFalse(os.path.islink(self.link))


class SafeSlugTest(unittest.TestCase):
    """Anti-traversal: slug sanitisation is delegated to `_aipf.safe_slug`.

    The CLI (`cmd_add`/`cmd_remove`/`cmd_list`) runs this guard before building
    any path, so pinning the guard's contract here pins the CLI's protection
    without standing up a git repo. Style mirrors `tests/test_hub.py:78-87`.
    """

    def test_traversal_and_separators_rejected(self):
        for bad in ("../etc", "a/b", "..", ".", ""):
            self.assertIsNone(_aipf.safe_slug(bad),
                              "expected None for %r" % (bad,))

    def test_good_slug_passes_through(self):
        self.assertEqual(_aipf.safe_slug("good-slug_1.2"), "good-slug_1.2")


class WorktreeDirTest(unittest.TestCase):
    """`worktree.worktree_dir`: pure sibling-path builder, no git."""

    def test_sibling_path(self):
        root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        wt = worktree.worktree_dir(root, "my-slug")
        self.assertTrue(wt.endswith(
            os.path.join(worktree.WORKTREES_DIRNAME, "my-slug")))
        # Sibling of root: shares the same parent directory.
        self.assertEqual(os.path.dirname(os.path.dirname(wt)),
                         os.path.dirname(os.path.abspath(root)))


class ListWorktreesPorcelainTest(unittest.TestCase):
    """Bonus: `worktree.list_worktrees` porcelain parser via a faked `_git`.

    Monkeypatch is narrow and reliable — replace `worktree._git` with a function
    returning canned `git worktree list --porcelain` output, restore on cleanup.
    No real git process is spawned.
    """

    def setUp(self):
        # Build porcelain paths from a platform-valid root so os.path.abspath()
        # in the parser (scripts/worktree.py:94) is a genuine no-op on any OS —
        # a hardcoded POSIX literal like "/repo/main" becomes "C:\\repo\\main"
        # under abspath() on Windows and breaks the equality checks.
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.main_path = os.path.join(self.root, "main")
        self.slug_path = os.path.join(
            self.root, "pathfinder-worktrees", "my-slug")
        self.porcelain = (
            "worktree {main}\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree {slug}\n"
            "HEAD def456\n"
            "branch refs/heads/my-slug\n"
        ).format(main=self.main_path, slug=self.slug_path)
        self._orig_git = worktree._git
        self.addCleanup(setattr, worktree, "_git", self._orig_git)

    def test_parses_two_worktrees(self):
        worktree._git = lambda *a, **k: (0, self.porcelain, "")
        entries = worktree.list_worktrees(self.main_path)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["path"], os.path.abspath(self.main_path))
        self.assertEqual(entries[0]["branch"], "main")
        self.assertEqual(entries[0]["head"], "abc123")
        self.assertEqual(entries[1]["path"], os.path.abspath(self.slug_path))
        # `branch` is the short ref name (refs/heads/ stripped).
        self.assertEqual(entries[1]["branch"], "my-slug")
        self.assertEqual(entries[1]["head"], "def456")

    def test_git_failure_yields_empty(self):
        worktree._git = lambda *a, **k: (1, "", "fatal: not a git repo")
        self.assertEqual(worktree.list_worktrees("/nope"), [])


if __name__ == "__main__":
    unittest.main()
