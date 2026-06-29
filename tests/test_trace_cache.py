#!/usr/bin/env python3
"""parse_transcript_usage memo by (path, mtime, size) (feat-18, stdlib unittest).

Re-parsing a multi-megabyte transcript on every /trace poll was the dominant cost
of build_trace. The memo returns an unchanged transcript's usage without re-reading
it, and re-parses when the file changes (new mtime/size). Pins:
  * a repeat parse of an unchanged file hits the cache and returns an equal record;
  * the returned record is a COPY — a caller mutating it (build_trace adds `_fe`)
    must not corrupt the cache;
  * appending to the file (new size) misses the cache and re-parses (msg count grows).

No network, disk only in a tempfile.
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")

import _aipf  # noqa: E402


def _assistant(out, fresh, ts):
    return {"type": "assistant", "timestamp": ts,
            "message": {"model": "claude-opus", "usage": {
                "input_tokens": fresh, "output_tokens": out}}}


class TranscriptUsageMemoTest(unittest.TestCase):
    def setUp(self):
        _aipf._USAGE_CACHE.clear()
        self.addCleanup(_aipf._USAGE_CACHE.clear)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(self._rm)
        self.path = os.path.join(self.dir, "agent.jsonl")

    def _rm(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, entries):
        with io.open(self.path, "w", encoding="utf-8", newline="") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def _append(self, entry):
        with io.open(self.path, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(entry) + "\n")

    def test_unchanged_file_is_cached(self):
        self._write([_assistant(10, 100, "2026-06-28T10:00:00Z")])
        r1 = _aipf.parse_transcript_usage(self.path)
        self.assertEqual(r1["msgs"], 1)
        self.assertEqual(r1["out"], 10)
        # the key is now memoized
        self.assertEqual(len(_aipf._USAGE_CACHE), 1)
        r2 = _aipf.parse_transcript_usage(self.path)
        self.assertEqual(r1, r2)

    def test_returned_record_is_a_copy(self):
        self._write([_assistant(5, 50, "2026-06-28T10:00:00Z")])
        r = _aipf.parse_transcript_usage(self.path)
        r["_fe"] = 12345              # what build_trace does to the record
        again = _aipf.parse_transcript_usage(self.path)
        self.assertNotIn("_fe", again, "cache must not be polluted by a caller's mutation")

    def test_changed_file_reparses(self):
        self._write([_assistant(10, 100, "2026-06-28T10:00:00Z")])
        r1 = _aipf.parse_transcript_usage(self.path)
        self.assertEqual(r1["msgs"], 1)
        # ensure a distinct mtime even on coarse clocks, then append
        os.utime(self.path, (time.time() - 5, time.time() - 5))
        _aipf.parse_transcript_usage(self.path)   # cache under the old (mtime,size)
        self._append(_assistant(20, 200, "2026-06-28T10:01:00Z"))
        r2 = _aipf.parse_transcript_usage(self.path)
        self.assertEqual(r2["msgs"], 2)           # re-parsed the grown file
        self.assertEqual(r2["out"], 30)


if __name__ == "__main__":
    unittest.main()
