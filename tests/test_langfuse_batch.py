#!/usr/bin/env python3
"""Offline tests for the Langfuse event->batch mapping (stdlib unittest only).

Covers the three pure functions in scripts/_aipf.py that turn telemetry events
into Langfuse ingestion items: langfuse_config_from_env, _envelope, and
events_to_langfuse_batch. No network and no disk outside a tempfile (none is
needed here -- every function under test is pure over dicts).

Run with:
    python3 -m unittest tests.test_langfuse_batch
    python3 -m unittest discover -s tests   # full suite
"""

import json
import os
import sys
import unittest

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")

import _aipf  # noqa: E402


class LangfuseConfigFromEnvTest(unittest.TestCase):
    def test_full_env_returns_triple(self):
        env = {
            "LANGFUSE_PUBLIC_KEY": "pub-1",
            "LANGFUSE_SECRET_KEY": "sec-1",
            "LANGFUSE_HOST": "https://my-host.example",
        }
        cfg = _aipf.langfuse_config_from_env(env)
        self.assertEqual(cfg, ("pub-1", "sec-1", "https://my-host.example"))

    def test_missing_public_key_is_none(self):
        env = {"LANGFUSE_SECRET_KEY": "sec-1"}
        self.assertIsNone(_aipf.langfuse_config_from_env(env))

    def test_missing_secret_key_is_none(self):
        env = {"LANGFUSE_PUBLIC_KEY": "pub-1"}
        self.assertIsNone(_aipf.langfuse_config_from_env(env))

    def test_host_defaults_to_cloud(self):
        env = {"LANGFUSE_PUBLIC_KEY": "pub-1", "LANGFUSE_SECRET_KEY": "sec-1"}
        cfg = _aipf.langfuse_config_from_env(env)
        self.assertEqual(cfg[2], "https://cloud.langfuse.com")

    def test_trailing_slash_stripped_from_host(self):
        env = {
            "LANGFUSE_PUBLIC_KEY": "pub-1",
            "LANGFUSE_SECRET_KEY": "sec-1",
            "LANGFUSE_HOST": "https://x.io/",
        }
        cfg = _aipf.langfuse_config_from_env(env)
        self.assertEqual(cfg[2], "https://x.io")


class EnvelopeTest(unittest.TestCase):
    def test_envelope_structure(self):
        body = {"id": "abc", "name": "n"}
        env = _aipf._envelope("trace-create", body)
        self.assertEqual(env["type"], "trace-create")
        # body is passed through unchanged (same object).
        self.assertIs(env["body"], body)
        # id/timestamp keys exist; their values are non-deterministic so we do
        # not assert on them.
        self.assertIn("id", env)
        self.assertIn("timestamp", env)


class EventsToLangfuseBatchTest(unittest.TestCase):
    @staticmethod
    def _items_of_type(batch, item_type):
        return [it for it in batch if it["type"] == item_type]

    @staticmethod
    def _one_of_type(batch, item_type):
        items = [it for it in batch if it["type"] == item_type]
        assert len(items) == 1, "expected exactly one %s, got %d" % (
            item_type, len(items))
        return items[0]

    def test_empty_input_yields_trace_create_only(self):
        batch = _aipf.events_to_langfuse_batch([], "my-slug")
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["type"], "trace-create")
        self.assertEqual(batch[0]["body"]["id"], "my-slug")
        self.assertEqual(batch[0]["body"]["name"], "my-slug")

    def test_session_start_becomes_span_create(self):
        events = [{
            "event": "session.start", "session_id": "s1", "ts": "2026-06-16T10:00:00Z",
            "summary": "kick off",
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        span = self._one_of_type(batch, "span-create")
        self.assertEqual(span["body"]["id"], "sess-s1")
        self.assertEqual(span["body"]["traceId"], "slug")
        self.assertEqual(span["body"]["name"], "session")
        self.assertEqual(span["body"]["startTime"], "2026-06-16T10:00:00Z")
        self.assertEqual(span["body"]["metadata"]["source"], "kick off")

    def test_session_end_becomes_span_update(self):
        events = [{
            "event": "session.end", "session_id": "s1", "ts": "2026-06-16T11:00:00Z",
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        span = self._one_of_type(batch, "span-update")
        self.assertEqual(span["body"]["id"], "sess-s1")
        self.assertEqual(span["body"]["traceId"], "slug")
        self.assertEqual(span["body"]["endTime"], "2026-06-16T11:00:00Z")

    def test_session_end_without_session_id_adds_nothing(self):
        # sess_span is None -> the `if sess_span` guard drops the update; only
        # trace-create remains.
        events = [{"event": "session.end", "ts": "2026-06-16T11:00:00Z"}]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["type"], "trace-create")

    def test_subagent_start_becomes_generation_create(self):
        events = [{
            "event": "subagent.start", "session_id": "s1", "spanId": "span-aaa",
            "role": "wf-coder", "summary": "do the thing", "ts": "2026-06-16T10:05:00Z",
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        gen = self._one_of_type(batch, "generation-create")
        self.assertEqual(gen["body"]["id"], "span-aaa")
        self.assertEqual(gen["body"]["parentObservationId"], "sess-s1")
        self.assertEqual(gen["body"]["name"], "wf-coder")
        self.assertEqual(gen["body"]["input"], "do the thing")

    def test_subagent_end_ok_is_default_level(self):
        events = [{
            "event": "subagent.end", "spanId": "span-aaa", "ok": True,
            "summary": "result text", "ts": "2026-06-16T10:09:00Z",
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        gen = self._one_of_type(batch, "generation-update")
        self.assertEqual(gen["body"]["id"], "span-aaa")
        self.assertEqual(gen["body"]["output"], "result text")
        self.assertEqual(gen["body"]["level"], "DEFAULT")

    def test_subagent_end_failure_is_error_level(self):
        events = [{
            "event": "subagent.end", "spanId": "span-aaa", "ok": False,
            "summary": "it broke",
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        gen = self._one_of_type(batch, "generation-update")
        self.assertEqual(gen["body"]["level"], "ERROR")

    def test_subagent_end_missing_ok_defaults_to_default_level(self):
        events = [{"event": "subagent.end", "spanId": "span-aaa"}]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        gen = self._one_of_type(batch, "generation-update")
        self.assertEqual(gen["body"]["level"], "DEFAULT")

    def test_subagent_end_without_span_is_dropped(self):
        # No spanId and no toolUseId -> span == "span-" -> guard `span != "span-"`
        # drops the update; only trace-create remains.
        events = [{"event": "subagent.end", "summary": "no span here"}]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["type"], "trace-create")

    def test_file_touch_becomes_event_create(self):
        events = [{
            "event": "file.touch", "tool": "Edit", "file": "src/app.py",
            "ts": "2026-06-16T10:07:00Z",
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        ev = self._one_of_type(batch, "event-create")
        self.assertEqual(ev["body"]["name"], "Edit")
        self.assertEqual(ev["body"]["metadata"]["file"], "src/app.py")

    def test_phase_becomes_event_create_named_kind_summary(self):
        events = [{"event": "phase", "summary": "IMPLEMENT"}]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        ev = self._one_of_type(batch, "event-create")
        self.assertEqual(ev["body"]["name"], "phase:IMPLEMENT")

    def test_gate_becomes_event_create_named_kind_summary(self):
        events = [{"event": "gate", "summary": "approved"}]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        ev = self._one_of_type(batch, "event-create")
        self.assertEqual(ev["body"]["name"], "gate:approved")

    def test_non_none_tags_land_in_metadata(self):
        events = [{
            "event": "session.start", "session_id": "s1",
            "phase": "IMPLEMENT", "iteration": 2, "workstream": "ws1", "bg": True,
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        md = self._one_of_type(batch, "span-create")["body"]["metadata"]
        self.assertEqual(md["phase"], "IMPLEMENT")
        self.assertEqual(md["iteration"], 2)
        self.assertEqual(md["workstream"], "ws1")
        self.assertEqual(md["bg"], True)

    def test_none_tags_are_excluded_from_metadata(self):
        events = [{
            "event": "session.start", "session_id": "s1",
            "phase": None, "iteration": None, "workstream": None, "bg": None,
        }]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        md = self._one_of_type(batch, "span-create")["body"]["metadata"]
        for key in ("phase", "iteration", "workstream", "bg"):
            self.assertNotIn(key, md)

    def test_unknown_and_noise_events_are_skipped(self):
        # Key brief invariant: turn.stop, tool.* and any unknown event type are
        # intentionally not forwarded -> only the trace-create item remains.
        events = [
            {"event": "turn.stop"},
            {"event": "tool.start"},
            {"event": "tool.end"},
            {"event": "something-new"},
        ]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["type"], "trace-create")

    def test_batch_is_json_serialisable(self):
        # The batch is POSTed as JSON; a quick guard that nothing exotic leaks in.
        events = [
            {"event": "session.start", "session_id": "s1", "summary": "x"},
            {"event": "subagent.start", "session_id": "s1", "spanId": "span-a",
             "role": "r", "summary": "in"},
            {"event": "subagent.end", "spanId": "span-a", "summary": "out", "ok": True},
            {"event": "session.end", "session_id": "s1"},
        ]
        batch = _aipf.events_to_langfuse_batch(events, "slug")
        json.dumps(batch)  # must not raise
        self.assertEqual(batch[0]["type"], "trace-create")


if __name__ == "__main__":
    unittest.main()
