#!/usr/bin/env python3
"""Offline tests for the dispatch-queue CLI (`scripts/queue.py`, stdlib unittest).

Covers the reliability-critical bits: load_queue distinguishes missing / corrupt
/ malformed / ok (a half-written queue must NOT read back as empty), validate()
catches contract breaks, and the mutating subcommands (next/done/skip/fail/
append) move statuses through atomic writes. Everything runs inside a tempfile;
no git, no network, no disk outside the tempdir.

Run with:
    python3 -m unittest tests.test_queue
    python3 -m unittest discover -s tests
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")

import queue as q  # noqa: E402  (scripts/queue.py)


def _seed(root, items, **top):
    """Write a queue file with `items` under `root/.workflow/`."""
    base = os.path.join(root, ".workflow")
    os.makedirs(base, exist_ok=True)
    data = {"version": 1, "source": "improve-runtime",
            "mode": "sequential-feature", "baseCommit": "abc1234",
            "items": items}
    data.update(top)
    with io.open(os.path.join(base, "dispatch-queue.json"), "w",
                 encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
    return os.path.join(base, "dispatch-queue.json")


def _item(n, slug, status="pending"):
    return {"n": n, "featId": "feat-%d" % n, "slug": slug,
            "title": "T%d" % n, "candId": "cand-%d" % n, "prism": "",
            "briefPath": ".workflow/tasks/%s/brief.md" % slug,
            "status": status, "startedAt": None, "doneAt": None}


def _run(root, argv):
    """Invoke the CLI with --root, capturing (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = q.main(["--root", root] + argv)
    except SystemExit as e:  # _load_or_die aborts via SystemExit, like argparse
        rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


class LoadQueueTest(unittest.TestCase):
    def test_missing_is_not_corrupt(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            data, st = q.load_queue(os.path.join(d, "nope.json"))
            self.assertEqual(st, "missing")
            self.assertIsNone(data)

    def test_corrupt_not_treated_as_empty(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = os.path.join(d, "q.json")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write('{"items": [ {bad json')
            data, st = q.load_queue(p)
            self.assertEqual(st, "corrupt")
            self.assertIsNone(data)

    def test_malformed_missing_items(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = os.path.join(d, "q.json")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write('{"version": 1}')
            _, st = q.load_queue(p)
            self.assertEqual(st, "malformed")

    def test_ok(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = _seed(d, [_item(1, "a")])
            data, st = q.load_queue(p)
            self.assertEqual(st, "ok")
            self.assertEqual(len(data["items"]), 1)


class ValidateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(q.validate(
            {"version": 1, "source": "improve-runtime", "mode": "sequential-feature",
             "baseCommit": "abc1234", "items": [_item(1, "a"), _item(2, "b")]}), [])

    def test_bad_n_sequence_and_status_and_dup(self):
        bad = {"version": 1, "items": [
            _item(2, "a"),                       # n should be 1
            {"n": 2, "featId": "f", "slug": "a", "briefPath": "p",
             "status": "weird"},                 # bad status + dup slug
        ]}
        errs = q.validate(bad)
        joined = " ".join(errs)
        self.assertIn("dense 1-based", joined)
        self.assertIn("status", joined)
        self.assertIn("duplicate slug", joined)

    def test_missing_required_key(self):
        errs = q.validate({"version": 1, "items": [{"n": 1, "slug": "a"}]})
        self.assertTrue(any("missing 'featId'" in e for e in errs))


class MutationTest(unittest.TestCase):
    def test_next_marks_lowest_pending_in_progress(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            _seed(d, [_item(1, "a", "done"), _item(2, "b"), _item(3, "c")],
                  autonomous=True)
            rc, out, _ = _run(d, ["next"])
            self.assertEqual(rc, 0)
            self.assertIn("slug=b", out)
            self.assertIn("autonomous=true", out)
            self.assertIn("baseCommit=abc1234", out)
            data, _ = q.load_queue(q.queue_path(d))
            self.assertEqual(data["items"][1]["status"], "in-progress")
            self.assertTrue(data["items"][1]["startedAt"])

    def test_next_empty_returns_3(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            _seed(d, [_item(1, "a", "done")])
            rc, _, _ = _run(d, ["next"])
            self.assertEqual(rc, 3)

    def test_done_skip_fail(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            _seed(d, [_item(1, "a"), _item(2, "b"), _item(3, "c")])
            self.assertEqual(_run(d, ["done", "a"])[0], 0)
            self.assertEqual(_run(d, ["skip", "b"])[0], 0)
            self.assertEqual(_run(d, ["fail", "c", "--reason", "boom"])[0], 0)
            data, _ = q.load_queue(q.queue_path(d))
            self.assertEqual([it["status"] for it in data["items"]],
                             ["done", "skipped", "failed"])
            self.assertEqual(data["items"][2]["failReason"], "boom")
            self.assertTrue(all(it["doneAt"] for it in data["items"]))

    def test_unknown_slug_returns_3(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            _seed(d, [_item(1, "a")])
            self.assertEqual(_run(d, ["done", "nope"])[0], 3)

    def test_corrupt_mutation_errors_not_empty(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            base = os.path.join(d, ".workflow")
            os.makedirs(base)
            with io.open(os.path.join(base, "dispatch-queue.json"), "w",
                         encoding="utf-8") as f:
                f.write("{ not json")
            rc, _, err = _run(d, ["next"])
            self.assertEqual(rc, 1)
            self.assertIn("corrupt", err)

    def test_append_creates_and_grows(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            rc, _, _ = _run(d, ["append", "--slug", "a", "--feat-id", "feat-1",
                                "--brief", ".workflow/tasks/a/brief.md"])
            self.assertEqual(rc, 0)
            rc, _, _ = _run(d, ["append", "--slug", "b", "--feat-id", "feat-2",
                                "--brief", ".workflow/tasks/b/brief.md"])
            self.assertEqual(rc, 0)
            data, _ = q.load_queue(q.queue_path(d))
            self.assertEqual([it["n"] for it in data["items"]], [1, 2])
            self.assertEqual(q.validate(data), [])

    def test_status_smoke(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            _seed(d, [_item(1, "a", "done"), _item(2, "b")])
            rc, out, _ = _run(d, ["status"])
            self.assertEqual(rc, 0)
            self.assertIn("1/2 done", out)
            self.assertIn("next pending: b", out)


if __name__ == "__main__":
    unittest.main()
