#!/usr/bin/env python3
"""Offline tests for build_trace's stale-transcript guard (stdlib unittest only).

Sub-agent / orchestrator transcripts are matched to a task by session-id glob.
A session can leak a lone marker (e.g. an old session's SessionEnd, routed to the
active task by active_slug) into an unrelated task's telemetry; without a guard,
find_subagent_files would then pull that session's days-old transcripts onto this
task's timeline, dragging t0 back by hours/days. These tests pin the fix and its
non-regression (a genuinely multi-day run is still shown in full).

No network and no disk outside a tempfile.

Run with:
    python3 -m unittest tests.test_trace_window
    python3 -m unittest discover -s tests   # full suite
"""

import json
import os
import sys
import tempfile
import time
import unittest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")

import _aipf  # noqa: E402

DAY = 86400


def _iso(epoch, millis=False):
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))
    return base + (".000Z" if millis else "Z")


def _append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _transcript(path, role, first_epoch, last_epoch):
    """A minimal sub-agent/main transcript: two timestamped lines, one with usage."""
    rows = [
        {"type": "user", "timestamp": _iso(first_epoch, True),
         "attributionAgent": role},
        {"type": "assistant", "timestamp": _iso(last_epoch, True),
         "message": {"model": "claude-opus-4-8",
                     "usage": {"input_tokens": 100, "output_tokens": 200,
                               "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}}},
    ]
    _append_jsonl(path, rows)


class TraceWindowTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.projects = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.slug = "the-task"
        self.tel = _aipf.task_file(self.root, self.slug, "telemetry.jsonl")
        # Anchor "now" to a stable recent epoch (no wall-clock flakiness needed).
        self.now = 1_700_000_000

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.projects, ignore_errors=True)

    def _subagent_path(self, sid, name):
        return os.path.join(self.projects, "proj", sid, "subagents", name + ".jsonl")

    def _main_path(self, sid):
        return os.path.join(self.projects, "proj", sid + ".jsonl")

    def test_stale_session_transcripts_are_excluded(self):
        cur, old = "11111111-1111-4111-8111-111111111111", \
                   "22222222-2222-4222-8222-222222222222"
        t = self.now
        # Real current run: a sub-agent span + its transcript, both "today".
        _append_jsonl(self.tel, [
            {"ts": _iso(t), "session_id": cur, "event": "subagent.start",
             "role": "wf-explorer", "spanId": "span-a1", "toolUseId": "a1"},
            {"ts": _iso(t + 120), "session_id": cur, "event": "subagent.end",
             "role": "wf-explorer", "spanId": "span-a1", "ok": True},
            # The leaked marker: an old session ending, routed here by active_slug.
            {"ts": _iso(t + 60), "session_id": old, "event": "session.end",
             "summary": "other"},
        ])
        _transcript(self._subagent_path(cur, "agent-cur"), "wf-explorer", t, t + 110)
        # Old session's real work — two days earlier, must NOT appear here.
        _transcript(self._subagent_path(old, "agent-old"),
                    "wf-coder", t - 2 * DAY, t - 2 * DAY + 300)
        _transcript(self._main_path(old), "оркестратор",
                    t - 2 * DAY, t - 2 * DAY + 600)

        tr = _aipf.build_trace(self.root, self.slug, projects_dir=self.projects)

        # Timeline is the current run, not dragged back two days.
        self.assertGreaterEqual(tr["timeline"]["t0"], t - _aipf.TRACE_STALE_TOL)
        # No phantom agents from the stale session.
        starts = [a.get("startTs") for a in tr["agents"] if a.get("startTs")]
        self.assertTrue(all(_aipf._ts_to_epoch(s) >= t - _aipf.TRACE_STALE_TOL
                            for s in starts), starts)
        # The empty stale session is not counted.
        self.assertEqual([s["sessionId"] for s in tr["sessions"]], [cur])

    def test_genuine_multiday_run_is_kept(self):
        """A single run with real telemetry spans on two days keeps both days —
        the guard drops stale *foreign* transcripts, not a long legitimate run."""
        sid = "33333333-3333-4333-8333-333333333333"
        t = self.now
        _append_jsonl(self.tel, [
            {"ts": _iso(t - 2 * DAY), "session_id": sid, "event": "subagent.start",
             "role": "wf-explorer", "spanId": "span-d1", "toolUseId": "d1"},
            {"ts": _iso(t - 2 * DAY + 120), "session_id": sid, "event": "subagent.end",
             "role": "wf-explorer", "spanId": "span-d1", "ok": True},
            {"ts": _iso(t), "session_id": sid, "event": "subagent.start",
             "role": "wf-coder", "spanId": "span-d3", "toolUseId": "d3"},
            {"ts": _iso(t + 120), "session_id": sid, "event": "subagent.end",
             "role": "wf-coder", "spanId": "span-d3", "ok": True},
        ])
        _transcript(self._subagent_path(sid, "agent-d1"),
                    "wf-explorer", t - 2 * DAY, t - 2 * DAY + 110)
        _transcript(self._subagent_path(sid, "agent-d3"), "wf-coder", t, t + 110)

        tr = _aipf.build_trace(self.root, self.slug, projects_dir=self.projects)
        sub_starts = sorted(_aipf._ts_to_epoch(a["startTs"]) for a in tr["agents"]
                            if a["kind"] == "subagent" and a.get("startTs"))
        self.assertEqual(len(sub_starts), 2)
        self.assertLessEqual(sub_starts[0], t - 2 * DAY + 1)  # day-1 span kept
        self.assertGreaterEqual(sub_starts[-1], t - 1)        # day-3 span kept


if __name__ == "__main__":
    unittest.main()
