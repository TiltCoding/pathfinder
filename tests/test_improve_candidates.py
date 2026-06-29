#!/usr/bin/env python3
"""Offline tests for the /improve transparency endpoint `_improve_candidates`.

It joins an /improve task's state.json — candidates[] + the vote aggregate
(votes[]) + topKOrder (candId->featId) + selected[] — into one list tagging each
candidate with its rank slot and whether it was picked, so the swarm's choice is
no longer a black box. Non-/improve tasks (no candidates) return isImprove:false.
stdlib unittest, tempfile workspace only, no network.

Run:
    python -m unittest tests.test_improve_candidates
    python -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server  # noqa: E402


class _H:
    _improve_candidates = server.Handler._improve_candidates

    def __init__(self, ws):
        self.workspace = ws


class ImproveCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ws = server.Workspace(self.root)
        self.ws.ensure_task("t")

    def _state(self, data):
        self.ws.write_json(self.ws.task_file("t", "state.json"), data)

    def test_join_marks_selected_topk_and_dropped(self):
        self._state({
            "candidates": [
                {"id": "cand-1", "title": "A", "prism": "perf", "size": "S", "risk": "low"},
                {"id": "cand-2", "title": "B", "prism": "security", "size": "M", "risk": "low"},
                {"id": "cand-3", "title": "C", "prism": "perf", "size": "L", "risk": "high"},
            ],
            "votes": [
                {"candId": "cand-1", "impact": 2, "effort": 1, "risk": 1, "confidence": 3, "keep": 1.0, "score": 1.0},
                {"candId": "cand-2", "impact": 3, "effort": 1, "risk": 1, "confidence": 3, "keep": 1.0, "score": 2.0},
            ],
            "topKOrder": [{"featId": "feat-1", "candId": "cand-2"},
                          {"featId": "feat-2", "candId": "cand-1"}],
            "selected": ["feat-1"],
            "prisms": ["perf", "security"], "topK": 2,
        })
        out = _H(self.ws)._improve_candidates("t")
        self.assertTrue(out["isImprove"])
        self.assertEqual(out["prisms"], ["perf", "security"])
        by = {c["id"]: c for c in out["candidates"]}
        self.assertEqual(len(by), 3)
        # cand-2: top-K feat-1 AND selected
        self.assertEqual(by["cand-2"]["featId"], "feat-1")
        self.assertTrue(by["cand-2"]["selected"])
        self.assertEqual(by["cand-2"]["vote"]["score"], 2.0)
        # cand-1: top-K feat-2 but NOT selected
        self.assertEqual(by["cand-1"]["featId"], "feat-2")
        self.assertFalse(by["cand-1"]["selected"])
        # cand-3: dropped (no rank, no vote)
        self.assertIsNone(by["cand-3"]["featId"])
        self.assertFalse(by["cand-3"]["selected"])
        self.assertIsNone(by["cand-3"]["vote"])

    def test_non_improve_task_is_flagged(self):
        self._state({"phase": "VERIFY", "title": "a feature task"})  # no candidates
        out = _H(self.ws)._improve_candidates("t")
        self.assertFalse(out["isImprove"])
        self.assertEqual(out["candidates"], [])

    def test_missing_state_is_graceful(self):
        out = _H(self.ws)._improve_candidates("nonexistent")
        self.assertFalse(out["isImprove"])


if __name__ == "__main__":
    unittest.main()
