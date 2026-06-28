#!/usr/bin/env python3
"""Executable contract spec for `.workflow/dispatch-queue.json` (stdlib unittest).

The dispatch queue is the writer (`/improve` DISPATCH) ↔ drainer (`/feature`
queue-mode) handoff; until now its schema lived only in prose
(`skills/improve/dispatch-queue.md`) and a drift slipped through silently. This
pins the schema as a test that fails CI on drift, via the same `queue.validate`
the CLI uses, and verifies the corrupt-quarantine behavior (a broken queue must
NOT masquerade as a drained one). Pure functions + a tempfile; no git, no net.

Run with:
    python3 -m unittest tests.test_dispatch_queue
    python3 -m unittest discover -s tests
"""

import datetime
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import queue as q  # noqa: E402  (scripts/queue.py)


def _item(n, slug, status="pending"):
    return {"n": n, "featId": "feat-%d" % n, "slug": slug, "title": "T%d" % n,
            "candId": "cand-%d" % n, "prism": "",
            "briefPath": ".workflow/tasks/%s/brief.md" % slug,
            "status": status, "startedAt": None, "doneAt": None}


def _queue(items, **over):
    d = {"version": 1, "source": "improve-runtime", "mode": "sequential-feature",
         "baseCommit": "abc1234", "createdAt": "2026-06-28T00:00:00", "items": items}
    d.update(over)
    return d


class ContractTest(unittest.TestCase):
    """queue.validate() IS the contract — these cases pin each rule."""

    def test_full_queue_with_all_statuses_is_valid(self):
        items = [_item(i + 1, "s%d" % (i + 1), st) for i, st in enumerate(q.STATUSES)]
        self.assertEqual(q.validate(_queue(items)), [])

    def test_each_missing_top_level_key_is_flagged(self):
        for key in q.TOP_REQUIRED:
            d = _queue([_item(1, "a")])
            d.pop(key, None)
            errs = q.validate(d)
            self.assertTrue(any(("'%s'" % key) in e and "top-level" in e for e in errs),
                            "expected a missing-top-level error for %r, got %r" % (key, errs))

    def test_each_missing_item_key_is_flagged(self):
        for key in q.ITEM_REQUIRED:
            it = _item(1, "a")
            it.pop(key, None)
            errs = q.validate(_queue([it]))
            self.assertTrue(any(("'%s'" % key) in e for e in errs),
                            "expected a missing-item error for %r, got %r" % (key, errs))

    def test_status_outside_enum_is_flagged(self):
        errs = q.validate(_queue([dict(_item(1, "a"), status="weird")]))
        self.assertTrue(any("status" in e for e in errs))

    def test_n_must_be_dense_1_based_in_order(self):
        # hole: 1,3
        errs = q.validate(_queue([_item(1, "a"), dict(_item(3, "b"), n=3)]))
        self.assertTrue(any("dense 1-based" in e for e in errs))
        # out of order: 2,1
        errs2 = q.validate(_queue([dict(_item(2, "a"), n=2), dict(_item(1, "b"), n=1)]))
        self.assertTrue(any("dense 1-based" in e for e in errs2))

    def test_duplicate_slug_is_flagged(self):
        errs = q.validate(_queue([_item(1, "dup"), dict(_item(2, "dup"), slug="dup")]))
        self.assertTrue(any("duplicate slug" in e for e in errs))

    def test_non_list_items_is_flagged(self):
        self.assertTrue(q.validate({"version": 1, "source": "x", "mode": "y",
                                    "baseCommit": "z", "items": {}}))


class QuarantineTest(unittest.TestCase):
    """A corrupt queue is moved aside (never silently treated as empty)."""

    def _run(self, root, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = q.main(["--root", root] + argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        return rc, out.getvalue(), err.getvalue()

    def test_corrupt_queue_is_quarantined_not_empty(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, ".workflow")
            os.makedirs(base)
            qp = os.path.join(base, "dispatch-queue.json")
            with io.open(qp, "w", encoding="utf-8") as f:
                f.write('{"items": [ half-written')
            rc, _, err = self._run(d, ["next"])
            self.assertEqual(rc, 1)
            self.assertIn("quarantined", err)
            # original moved aside, a .corrupt-* sibling exists, and it is NOT empty
            self.assertFalse(os.path.exists(qp), "corrupt queue should be moved aside")
            corrupt = [n for n in os.listdir(base) if n.startswith("dispatch-queue.json.corrupt-")]
            self.assertEqual(len(corrupt), 1, "expected exactly one quarantine file")

    def test_missing_queue_is_not_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".workflow"))
            rc, _, err = self._run(d, ["next"])
            self.assertEqual(rc, 3)               # "nothing queued", distinct from corrupt
            self.assertNotIn("quarantined", err)


def _ago(secs):
    """ISO local timestamp `secs` ago, in the format `_aipf.now_iso` writes."""
    dt = datetime.datetime.now() - datetime.timedelta(seconds=secs)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class StaleRecoveryTest(unittest.TestCase):
    """A crashed drain leaves an item `in-progress` forever; the next drain must
    return a stale one to `pending` instead of losing it (feat-14)."""

    def _run(self, root, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = q.main(["--root", root] + argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        return rc, out.getvalue(), err.getvalue()

    def _seed(self, root, items, **top):
        base = os.path.join(root, ".workflow")
        os.makedirs(base, exist_ok=True)
        data = _queue(items, **top)
        with io.open(os.path.join(base, "dispatch-queue.json"), "w",
                     encoding="utf-8") as f:
            f.write(__import__("json").dumps(data))

    def test_recover_stale_but_not_recent(self):
        with tempfile.TemporaryDirectory() as d:
            stale = dict(_item(1, "stale", "in-progress"), startedAt=_ago(4000))
            recent = dict(_item(2, "recent", "in-progress"), startedAt=_ago(10))
            self._seed(d, [stale, recent])
            rc, out, _ = self._run(d, ["recover", "--age", "1800"])
            self.assertEqual(rc, 0)
            self.assertIn("stale", out)
            data, _ = q.load_queue(q.queue_path(d))
            self.assertEqual(data["items"][0]["status"], "pending")
            self.assertEqual(data["items"][0]["resumedFrom"], "in-progress")
            self.assertIsNone(data["items"][0]["startedAt"])
            # the recent in-progress is left running
            self.assertEqual(data["items"][1]["status"], "in-progress")

    def test_next_recovers_then_picks_it(self):
        with tempfile.TemporaryDirectory() as d:
            stale = dict(_item(1, "crashed", "in-progress"), startedAt=_ago(4000))
            self._seed(d, [stale])               # no pending — only the crashed one
            rc, out, err = self._run(d, ["next"])
            self.assertEqual(rc, 0)              # recovered -> picked, not "nothing to do"
            self.assertIn("slug=crashed", out)
            self.assertIn("recovered", err)
            data, _ = q.load_queue(q.queue_path(d))
            self.assertEqual(data["items"][0]["status"], "in-progress")  # re-picked
            self.assertEqual(data["items"][0]["resumedFrom"], "in-progress")

    def test_fresh_in_progress_survives_next(self):
        with tempfile.TemporaryDirectory() as d:
            running = dict(_item(1, "running", "in-progress"), startedAt=_ago(5))
            self._seed(d, [running, _item(2, "queued")])
            rc, out, _ = self._run(d, ["next"])
            self.assertEqual(rc, 0)
            self.assertIn("slug=queued", out)    # picks the pending, not the running one
            data, _ = q.load_queue(q.queue_path(d))
            self.assertEqual(data["items"][0]["status"], "in-progress")  # untouched


if __name__ == "__main__":
    unittest.main()
