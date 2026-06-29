#!/usr/bin/env python3
"""Offline tests for conditional GET (ETag / 304) on the poll endpoints
(stdlib unittest, no network, tempfile only).

The 3s/5s pollers refetch /data, /replies and /chat that are unchanged most of
the time. These pin the contract:

  * `_serve_task_file` (serves /data, /replies) returns a weak (mtime,size) ETag
    + `Cache-Control: no-cache` and the body on a first GET;
  * a second GET whose `If-None-Match` matches → 304 with no body (same ETag);
  * after the file changes, the ETag differs and the full body returns;
  * the `/chat` route does the same, keyed on chat.jsonl.

Mirrors the fake-handler harness in tests/test_attach.py / test_settings.py.

Run:
    python -m unittest tests.test_conditional_get -v
    python -m unittest discover -s tests
"""
import json
import os
import sys
import shutil
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server  # noqa: E402


class _GetHandler:
    """Drives the real do_GET / _serve_task_file with a captured response."""

    def __init__(self, workspace, path="/", if_none_match=None):
        self.workspace = workspace
        self.path = path
        self.headers = {}
        if if_none_match is not None:
            self.headers["If-None-Match"] = if_none_match
        self.status = None
        self.resp_headers = {}
        self._chunks = []

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.resp_headers[key] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self._chunks.append(data)

    @property
    def body(self):
        return b"".join(self._chunks)

    # bound real handler methods
    do_GET = server.Handler.do_GET
    _send = server.Handler._send
    _json = server.Handler._json
    _json_conditional = server.Handler._json_conditional
    _file_etag = server.Handler._file_etag
    _serve_task_file = server.Handler._serve_task_file
    _chat_get = server.Handler._chat_get
    _queue = server.Handler._queue


class ConditionalGetBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ws = server.Workspace(self.root)
        self.ws.ensure_task("t")


class ServeTaskFileEtagTest(ConditionalGetBase):
    def _write_dash(self, obj):
        self.ws.write_json(self.ws.task_file("t", "dashboard.json"), obj)

    def test_first_get_has_etag_and_body(self):
        self._write_dash({"phase": "X"})
        h = _GetHandler(self.ws)
        h._serve_task_file("t", "dashboard.json")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.resp_headers.get("ETag"))
        self.assertEqual(h.resp_headers.get("Cache-Control"), "no-cache")
        self.assertTrue(h.body)

    def test_matching_if_none_match_is_304_no_body(self):
        self._write_dash({"phase": "X"})
        first = _GetHandler(self.ws)
        first._serve_task_file("t", "dashboard.json")
        etag = first.resp_headers["ETag"]

        second = _GetHandler(self.ws, if_none_match=etag)
        second._serve_task_file("t", "dashboard.json")
        self.assertEqual(second.status, 304)
        self.assertEqual(second.body, b"")
        self.assertEqual(second.resp_headers.get("ETag"), etag)

    def test_changed_file_yields_new_etag_and_body(self):
        self._write_dash({"phase": "X"})
        first = _GetHandler(self.ws)
        first._serve_task_file("t", "dashboard.json")
        etag = first.resp_headers["ETag"]

        # different content (and size) -> different validator
        self._write_dash({"phase": "XYZ-changed"})
        again = _GetHandler(self.ws, if_none_match=etag)
        again._serve_task_file("t", "dashboard.json")
        self.assertEqual(again.status, 200)
        self.assertNotEqual(again.resp_headers.get("ETag"), etag)
        self.assertTrue(again.body)

    def test_missing_json_still_empty_object(self):
        # absent dashboard.json keeps the legacy {} contract (no ETag path)
        h = _GetHandler(self.ws)
        h._serve_task_file("t", "dashboard.json")
        self.assertEqual(h.status, 200)
        self.assertEqual(json.loads(h.body.decode("utf-8")), {})


class ChatEtagTest(ConditionalGetBase):
    def _append_chat(self, obj):
        path = self.ws.task_file("t", "chat.jsonl")
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(obj) + "\n")

    def test_chat_304_on_unchanged(self):
        self._append_chat({"role": "human", "text": "hi", "ts": "t0"})
        first = _GetHandler(self.ws, path="/chat?slug=t")
        first.do_GET()
        self.assertEqual(first.status, 200)
        etag = first.resp_headers.get("ETag")
        self.assertTrue(etag)
        self.assertIn("messages", json.loads(first.body.decode("utf-8")))

        second = _GetHandler(self.ws, path="/chat?slug=t", if_none_match=etag)
        second.do_GET()
        self.assertEqual(second.status, 304)
        self.assertEqual(second.body, b"")

    def test_chat_new_message_changes_etag(self):
        self._append_chat({"role": "human", "text": "one", "ts": "t0"})
        first = _GetHandler(self.ws, path="/chat?slug=t")
        first.do_GET()
        etag = first.resp_headers["ETag"]

        self._append_chat({"role": "agent", "text": "two", "ts": "t1"})
        again = _GetHandler(self.ws, path="/chat?slug=t", if_none_match=etag)
        again.do_GET()
        self.assertEqual(again.status, 200)
        self.assertNotEqual(again.resp_headers.get("ETag"), etag)
        self.assertEqual(len(json.loads(again.body.decode("utf-8"))["messages"]), 2)


class ChatIncrementalTest(ConditionalGetBase):
    """`/chat?since=<offset>` returns only the new lines (feat-10), so a changed
    tick never reparses the whole log."""

    def _append_chat(self, obj):
        path = self.ws.task_file("t", "chat.jsonl")
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(obj) + "\n")

    def test_since_returns_only_new_lines(self):
        self._append_chat({"role": "human", "text": "one", "ts": "t0"})
        self._append_chat({"role": "agent", "text": "two", "ts": "t1"})
        full = _GetHandler(self.ws, path="/chat?slug=t&since=0")
        full.do_GET()
        self.assertEqual(full.status, 200)
        data = json.loads(full.body.decode("utf-8"))
        self.assertEqual(len(data["messages"]), 2)
        off = data["nextOffset"]
        self.assertGreater(off, 0)

        # no new bytes -> empty tail at the same offset (the cheap idle case)
        empty = _GetHandler(self.ws, path="/chat?slug=t&since=%d" % off)
        empty.do_GET()
        self.assertEqual(empty.status, 200)
        ed = json.loads(empty.body.decode("utf-8"))
        self.assertEqual(ed["messages"], [])
        self.assertEqual(ed["nextOffset"], off)

        # append one -> only the new message comes back in the tail
        self._append_chat({"role": "human", "text": "three", "ts": "t2"})
        tail = _GetHandler(self.ws, path="/chat?slug=t&since=%d" % off)
        tail.do_GET()
        td = json.loads(tail.body.decode("utf-8"))
        self.assertEqual(len(td["messages"]), 1)
        self.assertEqual(td["messages"][0]["text"], "three")
        self.assertGreater(td["nextOffset"], off)


class JsonConditionalHelperTest(unittest.TestCase):
    """`_json_conditional` (feat-7): content-hash weak ETag + 304 on a matching
    If-None-Match, fresh body + new ETag when the value changes."""

    def test_first_call_has_etag_and_body(self):
        h = _GetHandler(None)
        h._json_conditional({"a": 1, "b": "две"})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.resp_headers.get("ETag"))
        self.assertEqual(h.resp_headers.get("Cache-Control"), "no-cache")
        self.assertEqual(json.loads(h.body.decode("utf-8")), {"a": 1, "b": "две"})

    def test_matching_if_none_match_is_304_no_body(self):
        first = _GetHandler(None)
        first._json_conditional({"a": 1})
        etag = first.resp_headers["ETag"]
        second = _GetHandler(None, if_none_match=etag)
        second._json_conditional({"a": 1})
        self.assertEqual(second.status, 304)
        self.assertEqual(second.body, b"")
        self.assertEqual(second.resp_headers.get("ETag"), etag)

    def test_changed_value_yields_new_etag_and_body(self):
        first = _GetHandler(None)
        first._json_conditional({"a": 1})
        etag = first.resp_headers["ETag"]
        again = _GetHandler(None, if_none_match=etag)
        again._json_conditional({"a": 2})
        self.assertEqual(again.status, 200)
        self.assertNotEqual(again.resp_headers.get("ETag"), etag)
        self.assertTrue(again.body)


class QueueJsonConditionalGetTest(ConditionalGetBase):
    """/queue.json now revalidates (feat-7): same value -> 304, changed -> new body.
    (/hub.json shares the exact same `_json_conditional` path.)"""

    def _write_queue(self, obj):
        self.ws.write_json(os.path.join(self.ws.base, "dispatch-queue.json"), obj)

    def test_queue_304_on_unchanged(self):
        self._write_queue({"version": 1, "items": [{"n": 1, "slug": "a"}]})
        first = _GetHandler(self.ws, path="/queue.json")
        first.do_GET()
        self.assertEqual(first.status, 200)
        etag = first.resp_headers.get("ETag")
        self.assertTrue(etag)
        second = _GetHandler(self.ws, path="/queue.json", if_none_match=etag)
        second.do_GET()
        self.assertEqual(second.status, 304)
        self.assertEqual(second.body, b"")

    def test_queue_change_yields_new_etag(self):
        self._write_queue({"version": 1, "items": [{"n": 1, "slug": "a"}]})
        first = _GetHandler(self.ws, path="/queue.json")
        first.do_GET()
        etag = first.resp_headers["ETag"]
        self._write_queue({"version": 1, "items": [{"n": 1, "slug": "a"},
                                                    {"n": 2, "slug": "b"}]})
        again = _GetHandler(self.ws, path="/queue.json", if_none_match=etag)
        again.do_GET()
        self.assertEqual(again.status, 200)
        self.assertNotEqual(again.resp_headers.get("ETag"), etag)


class FileEtagHelperTest(ConditionalGetBase):
    def test_none_for_missing(self):
        h = _GetHandler(self.ws)
        self.assertIsNone(h._file_etag(os.path.join(self.root, "nope.json")))


if __name__ == "__main__":
    unittest.main()
