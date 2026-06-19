#!/usr/bin/env python3
"""Offline tests for anchored chat: `POST /chat` carries optional `anchor`/`quote`
through into chat.jsonl, stays backward-compatible without them, and still rejects
empty text. stdlib unittest, no network, tempfile only.

Run:
    python -m unittest tests.test_chat_anchor
    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server  # noqa: E402


class _ChatHandler:
    """Drives the real `_chat_post` offline: real workspace + signal/json/send,
    socket and waker stubbed (we assert on the file, not the wake plumbing)."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.status = None
        self.headers = {}
        self._chunks = []

    # BaseHTTPRequestHandler surface used by _send
    def send_response(self, code): self.status = code
    def send_header(self, k, v): self.headers[k] = v
    def end_headers(self): pass
    @property
    def wfile(self): return self
    def write(self, data): self._chunks.append(data)
    @property
    def body(self): return b"".join(self._chunks)

    # stub the wake plumbing (no /wait long-poll in a unit test)
    def _wake(self, slug): pass

    # bound real handler methods
    _send = server.Handler._send
    _json = server.Handler._json
    _append_signal = server.Handler._append_signal
    _chat_post = server.Handler._chat_post

    def post(self, slug, body):
        self._chat_post(slug, body)
        return json.loads(self.body.decode("utf-8"))


class ChatAnchorTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        self.h = _ChatHandler(self.ws)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _last_msg(self, slug):
        path = self.ws.task_file(slug, "chat.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        return json.loads(lines[-1])

    def test_anchor_and_quote_persist(self):
        r = self.h.post("t1", {"text": "поправь тут", "anchor": "b2", "quote": "эту фразу"})
        self.assertTrue(r.get("ok"))
        m = self._last_msg("t1")
        self.assertEqual(m["anchor"], "b2")
        self.assertEqual(m["quote"], "эту фразу")
        self.assertEqual(m["role"], "human")

    def test_absent_fields_not_written(self):
        self.h.post("t2", {"text": "просто сообщение"})
        m = self._last_msg("t2")
        self.assertNotIn("anchor", m)
        self.assertNotIn("quote", m)

    def test_needsanswer_from_human_is_ignored(self):
        # Only the agent may mark a turn as needing an answer.
        self.h.post("t3", {"text": "вопрос?", "needsAnswer": True})
        m = self._last_msg("t3")
        self.assertNotIn("needsAnswer", m)

    def test_empty_text_rejected(self):
        r = self.h.post("t4", {"text": "   ", "anchor": "b1"})
        self.assertEqual(self.h.status, 400)
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
