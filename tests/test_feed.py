#!/usr/bin/env python3
"""Offline tests for the delta-only live feed (stdlib unittest only).

Covers the two silent critical paths behind `/trace/feed`: the byte-cursor
tail reader `_aipf._iter_lines_from` (offset bookkeeping + at-least-once on an
unterminated trailing line) and the stateless delta builder `_aipf.build_feed`
(cursor delta, soft-degradation on a corrupt JSON line, and best-effort lane
attribution per ADR-0001/ADR-0003). Event fixtures follow the shape emitted by
`scripts/telemetry_hook.build_event` (tool.start/end, subagent.start/end).

No network and no disk outside a tempfile.

Run with:
    python3 -m unittest tests.test_feed
    python3 -m unittest discover -s tests   # full suite
"""

import json
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

import _aipf  # noqa: E402


def _append_jsonl(path, rows):
    """Append jsonl fixture rows (copy of tests/test_trace_window.py:38-42)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# --- Event fixtures (telemetry_hook.build_event shapes) ----------------------

def _tool_start(session_id, tool_use_id, tool="Bash", arg="ls", kind="bash"):
    return {"ts": "2026-06-16T00:00:00Z", "session_id": session_id,
            "event": "tool.start", "tool": tool, "toolUseId": tool_use_id,
            "spanId": "tool-" + tool_use_id, "kind": kind, "arg": arg}


def _tool_end(session_id, tool_use_id, tool="Bash", ok=True):
    return {"ts": "2026-06-16T00:00:01Z", "session_id": session_id,
            "event": "tool.end", "tool": tool, "toolUseId": tool_use_id,
            "spanId": "tool-" + tool_use_id, "ok": ok}


def _subagent_start(session_id, tool_use_id, role="wf-coder"):
    return {"ts": "2026-06-16T00:00:00Z", "session_id": session_id,
            "event": "subagent.start", "role": role,
            "spanId": "span-" + tool_use_id, "toolUseId": tool_use_id,
            "bg": False, "summary": "do a thing"}


def _subagent_end(session_id, tool_use_id, role="wf-coder", ok=True):
    return {"ts": "2026-06-16T00:00:02Z", "session_id": session_id,
            "event": "subagent.end", "role": role,
            "spanId": "span-" + tool_use_id, "toolUseId": tool_use_id,
            "ok": ok, "summary": "done"}


class IterLinesFromTest(unittest.TestCase):
    """Byte-cursor tail reader `_aipf._iter_lines_from` (scripts/_aipf.py:592)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.path = os.path.join(self.root, "feed.jsonl")

    def _size(self):
        return os.path.getsize(self.path)

    def test_missing_file_returns_offset_unchanged(self):
        missing = os.path.join(self.root, "nope.jsonl")
        self.assertEqual(_aipf._iter_lines_from(missing, 0), ([], 0))
        self.assertEqual(_aipf._iter_lines_from(missing, 7), ([], 7))

    def test_negative_offset_normalises_to_zero_and_reads_all(self):
        _append_jsonl(self.path, [{"a": 1}, {"b": 2}])
        lines, new_offset = _aipf._iter_lines_from(self.path, -5)
        self.assertEqual(len(lines), 2)
        self.assertEqual(new_offset, self._size())

    def test_offset_at_or_past_eof_returns_size(self):
        _append_jsonl(self.path, [{"a": 1}])
        size = self._size()
        self.assertEqual(_aipf._iter_lines_from(self.path, size), ([], size))
        self.assertEqual(_aipf._iter_lines_from(self.path, size + 100), ([], size))

    def test_complete_lines_from_zero(self):
        _append_jsonl(self.path, [{"a": 1}, {"b": 2}, {"c": 3}])
        lines, new_offset = _aipf._iter_lines_from(self.path, 0)
        self.assertEqual(len(lines), 3)
        self.assertEqual(new_offset, self._size())
        # Lines are returned verbatim (minus the trailing newline) and parse.
        self.assertEqual([json.loads(x) for x in lines],
                         [{"a": 1}, {"b": 2}, {"c": 3}])

    def test_cursor_delta_only_returns_new_lines(self):
        _append_jsonl(self.path, [{"a": 1}])
        _, off = _aipf._iter_lines_from(self.path, 0)
        _append_jsonl(self.path, [{"b": 2}])
        lines, new_off = _aipf._iter_lines_from(self.path, off)
        self.assertEqual([json.loads(x) for x in lines], [{"b": 2}])
        self.assertEqual(new_off, self._size())
        self.assertGreater(new_off, off)  # cursor grows monotonically

    def test_unterminated_tail_is_held_for_reread(self):
        # A complete line then a tail WITHOUT a trailing newline.
        _append_jsonl(self.path, [{"a": 1}])
        with open(self.path, "a", encoding="utf-8", newline="") as f:
            f.write('{"b": 2}')  # no '\n' yet
        complete_size = len(b'{"a": 1}\n')  # cursor must stop at the tail start

        lines, off = _aipf._iter_lines_from(self.path, 0)
        self.assertEqual([json.loads(x) for x in lines], [{"a": 1}])
        self.assertEqual(off, complete_size)
        # The tail bytes are not counted: a second read from the same cursor
        # before the newline lands yields nothing and holds the cursor.
        again, off2 = _aipf._iter_lines_from(self.path, off)
        self.assertEqual(again, [])
        self.assertEqual(off2, off)
        # Once the hook finishes the line ('\n' appended), the held tail is
        # re-read whole from the same cursor (at-least-once delivery).
        with open(self.path, "a", encoding="utf-8", newline="") as f:
            f.write("\n")
        tail, off3 = _aipf._iter_lines_from(self.path, off)
        self.assertEqual([json.loads(x) for x in tail], [{"b": 2}])
        self.assertEqual(off3, self._size())

    def test_multibyte_utf8_is_decoded_intact(self):
        _append_jsonl(self.path, [{"msg": "кириллица"}])
        lines, new_off = _aipf._iter_lines_from(self.path, 0)
        self.assertEqual([json.loads(x) for x in lines], [{"msg": "кириллица"}])
        self.assertEqual(new_off, self._size())

    def test_broken_utf8_byte_does_not_raise(self):
        # An invalid byte sequence mid-file: errors="replace" must not crash.
        with open(self.path, "wb") as f:
            f.write(b'{"ok": 1}\n\xff\xfe garbage\n')
        lines, new_off = _aipf._iter_lines_from(self.path, 0)
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), {"ok": 1})
        self.assertEqual(new_off, self._size())


class BuildFeedTest(unittest.TestCase):
    """Stateless delta builder `_aipf.build_feed` (scripts/_aipf.py:656)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.slug = "the-task"
        self.tel = _aipf.task_file(self.root, self.slug, "telemetry.jsonl")
        self.sid = "11111111-1111-4111-8111-111111111111"

    def _size(self):
        return os.path.getsize(self.tel)

    def test_missing_telemetry_returns_empty_feed(self):
        fd = _aipf.build_feed(self.root, self.slug, 0)
        self.assertEqual(fd["events"], [])
        self.assertEqual(fd["nextOffset"], 0)
        self.assertIn("generatedAt", fd)

    def test_tool_pair_from_zero(self):
        _append_jsonl(self.tel, [
            _tool_start(self.sid, "a1", tool="Bash", arg="echo hi"),
            _tool_end(self.sid, "a1", tool="Bash", ok=True),
        ])
        fd = _aipf.build_feed(self.root, self.slug, 0)
        self.assertEqual([e["event"] for e in fd["events"]], ["start", "end"])
        start, end = fd["events"]
        self.assertEqual(start["spanId"], "tool-a1")
        self.assertEqual(start["tool"], "Bash")
        self.assertEqual(start["arg"], "echo hi")
        self.assertEqual(start["kind"], "bash")
        self.assertEqual(start["session_id"], self.sid)
        self.assertEqual(end["spanId"], "tool-a1")
        self.assertIs(end["ok"], True)
        self.assertEqual(fd["nextOffset"], self._size())

    def test_cursor_delta_only_returns_new_events(self):
        _append_jsonl(self.tel, [
            _tool_start(self.sid, "a1"), _tool_end(self.sid, "a1"),
        ])
        first = _aipf.build_feed(self.root, self.slug, 0)
        off = first["nextOffset"]
        self.assertEqual(len(first["events"]), 2)

        _append_jsonl(self.tel, [
            _tool_start(self.sid, "a2"), _tool_end(self.sid, "a2"),
        ])
        second = _aipf.build_feed(self.root, self.slug, off)
        self.assertEqual([e["spanId"] for e in second["events"]],
                         ["tool-a2", "tool-a2"])
        self.assertGreater(second["nextOffset"], off)
        self.assertEqual(second["nextOffset"], self._size())

    def test_corrupt_json_line_is_skipped_not_raised(self):
        # KEY invariant: a corrupt line between valid ones is dropped, the feed
        # does not raise and the valid events still come through.
        _append_jsonl(self.tel, [_tool_start(self.sid, "a1")])
        with open(self.tel, "a", encoding="utf-8") as f:
            f.write("{not json\n")
        _append_jsonl(self.tel, [_tool_end(self.sid, "a1")])

        fd = _aipf.build_feed(self.root, self.slug, 0)  # must not raise
        self.assertEqual([e["event"] for e in fd["events"]], ["start", "end"])
        self.assertEqual([e["spanId"] for e in fd["events"]],
                         ["tool-a1", "tool-a1"])
        self.assertEqual(fd["nextOffset"], self._size())

    def test_non_tool_events_are_ignored(self):
        _append_jsonl(self.tel, [
            {"ts": "2026-06-16T00:00:00Z", "session_id": self.sid,
             "event": "session.start", "summary": "startup"},
            {"ts": "2026-06-16T00:00:00Z", "session_id": self.sid,
             "event": "file.touch", "tool": "Write", "file": "x.py"},
            _tool_start(self.sid, "a1"),
        ])
        fd = _aipf.build_feed(self.root, self.slug, 0)
        # Only the tool event surfaces; session.start/file.touch are skipped.
        self.assertEqual([e["spanId"] for e in fd["events"]], ["tool-a1"])

    def test_lane_attributed_to_single_open_subagent_span(self):
        # One open sub-agent span in the session -> the tool event is grouped
        # under that span (lane == spanId) and carries the sub-agent's role.
        _append_jsonl(self.tel, [
            _subagent_start(self.sid, "s1", role="wf-coder"),
            _tool_start(self.sid, "a1"),
            _tool_end(self.sid, "a1"),
        ])
        fd = _aipf.build_feed(self.root, self.slug, 0)
        self.assertEqual(len(fd["events"]), 2)
        for ev in fd["events"]:
            self.assertEqual(ev["lane"], "span-s1")
            self.assertEqual(ev["role"], "wf-coder")

    def test_lane_falls_back_to_orchestrator_without_open_span(self):
        # No open span -> orchestrator lane, role None. After subagent.end the
        # span is closed, so a later tool event also lands on the orchestrator.
        _append_jsonl(self.tel, [
            _subagent_start(self.sid, "s1", role="wf-coder"),
            _subagent_end(self.sid, "s1", role="wf-coder"),
            _tool_start(self.sid, "a1"),
        ])
        fd = _aipf.build_feed(self.root, self.slug, 0)
        self.assertEqual(len(fd["events"]), 1)
        ev = fd["events"][0]
        self.assertEqual(ev["lane"], "orchestrator")
        self.assertIsNone(ev["role"])

    def test_lane_orchestrator_when_no_subagent_at_all(self):
        _append_jsonl(self.tel, [_tool_start(self.sid, "a1")])
        fd = _aipf.build_feed(self.root, self.slug, 0)
        ev = fd["events"][0]
        self.assertEqual(ev["lane"], "orchestrator")
        self.assertIsNone(ev["role"])


if __name__ == "__main__":
    unittest.main()
