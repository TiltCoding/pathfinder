#!/usr/bin/env python3
"""Offline tests for the opt-in cross-run cost roll-up `_hub_cost` (feat-19).

It sums each task's trace `totals.costUsd`/`out`, drops zero-cost tasks, and
groups the /improve drain by the queue's improveSlug. It must stay SEPARATE from
the default /hub.json aggregate (ADR-0010 keeps cost out of that). stdlib
unittest, tempfile workspace, build_trace is mocked (no real transcripts).

Run:
    python -m unittest tests.test_hub_cost
    python -m unittest discover -s tests
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import _aipf  # noqa: E402
import server  # noqa: E402


class _H:
    _hub_cost = server.Handler._hub_cost

    def __init__(self, ws, tasks):
        self.workspace = ws
        self._tasks = tasks

    def _list_tasks(self):
        return self._tasks


_TRACE = {
    "a": {"totals": {"costUsd": 1.25, "out": 100}},
    "b": {"totals": {"costUsd": 2.00, "out": 200}},
    "c": {"totals": {"costUsd": 0, "out": 0}},      # no usage -> excluded
}


class HubCostTest(unittest.TestCase):
    def setUp(self):
        server.Handler._hub_cache.pop("cost", None)
        self.addCleanup(lambda: server.Handler._hub_cache.pop("cost", None))
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ws = server.Workspace(self.root)

    def _write_queue(self, obj):
        self.ws.write_json(os.path.join(self.ws.base, "dispatch-queue.json"), obj)

    def _run(self):
        with mock.patch.object(_aipf, "build_trace",
                               side_effect=lambda root, slug: _TRACE.get(slug, {})):
            return _H(self.ws, ["a", "b", "c"])._hub_cost()

    def test_sums_runs_and_drops_zero(self):
        out = self._run()
        slugs = {r["slug"] for r in out["runs"]}
        self.assertEqual(slugs, {"a", "b"})            # c (zero) excluded
        self.assertEqual(out["totalCostUsd"], 3.25)
        self.assertEqual(out["totalOut"], 300)

    def test_groups_by_improve_queue(self):
        self._write_queue({"version": 1, "improveSlug": "imp-run",
                           "items": [{"n": 1, "slug": "a"}, {"n": 2, "slug": "b"},
                                     {"n": 3, "slug": "c"}]})
        out = self._run()
        bq = out["byQueue"]["imp-run"]
        self.assertEqual(bq["costUsd"], 3.25)          # a+b (c contributes 0)
        self.assertEqual(bq["runs"], 2)                # only a,b had cost

    def test_no_queue_is_graceful(self):
        out = self._run()
        self.assertEqual(out["byQueue"], {})           # no dispatch-queue.json


if __name__ == "__main__":
    unittest.main()
