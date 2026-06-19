#!/usr/bin/env python3
"""Offline tests for the agent-trace-details backend (stdlib unittest only).

No network and no disk outside a tempfile.

Run with:
    python3 -m unittest tests.test_telemetry_actions
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
import telemetry_hook  # noqa: E402


# build_event resolves the task slug via active_slug(cwd, session_id); a tool/MCP
# trace event is only emitted once a slug is known (it needs a telemetry.jsonl to
# write to). These tests exercise the event-shaping logic, not slug resolution, so
# we anchor every payload to an isolated `.workflow` root with one active task.
# Without it the tests would silently depend on the developer's ambient
# `.workflow/tasks/` and fail on a clean checkout (e.g. CI).
_WF_ROOT = None


def setUpModule():
    global _WF_ROOT
    _WF_ROOT = tempfile.mkdtemp()
    active = os.path.join(_aipf.workflow_base(_WF_ROOT), "active.json")
    os.makedirs(os.path.dirname(active), exist_ok=True)
    with open(active, "w", encoding="utf-8") as f:
        json.dump({"slug": "the-task"}, f)


def tearDownModule():
    if _WF_ROOT:
        shutil.rmtree(_WF_ROOT, ignore_errors=True)


def _start_event(payload):
    """Run build_event and return the single tool.start dict (or raise)."""
    payload = {"cwd": _WF_ROOT, **payload}
    slug, ev = telemetry_hook.build_event(payload)
    events = ev if isinstance(ev, list) else [ev]
    for e in events:
        if isinstance(e, dict) and e.get("event") == "tool.start":
            return e
    raise AssertionError("no tool.start event produced: %r" % (ev,))


class BuildEventMcpTest(unittest.TestCase):
    def test_mcp_captured_with_new_fields(self):
        ev = _start_event({
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__context7__resolve-library-id",
            "tool_use_id": "toolu_x",
            "tool_input": {"libraryName": "react"},
            "session_id": "s1",
        })
        self.assertEqual(ev["kind"], "mcp")
        self.assertEqual(ev["server"], "context7")
        self.assertEqual(ev["mcpTool"], "resolve-library-id")
        self.assertIn("react", ev["arg"])
        # old fields preserved with the original names
        self.assertEqual(ev["tool"], "mcp__context7__resolve-library-id")
        self.assertEqual(ev["spanId"], "tool-toolu_x")

    def test_mcp_server_name_with_underscores(self):
        ev = _start_event({
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__plugin_ai-pathfinder_context7__resolve-library-id",
            "tool_use_id": "toolu_y",
            "tool_input": {"libraryName": "vue"},
            "session_id": "s1",
        })
        self.assertEqual(ev["server"], "plugin_ai-pathfinder_context7")
        self.assertEqual(ev["mcpTool"], "resolve-library-id")

    def test_non_mcp_tool_gets_kind(self):
        ev = _start_event({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "toolu_b",
            "tool_input": {"command": "ls -la"},
            "session_id": "s1",
        })
        self.assertEqual(ev["kind"], "bash")
        self.assertNotIn("server", ev)
        ev2 = _start_event({
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_use_id": "toolu_r",
            "tool_input": {"file_path": "/tmp/x"},
            "session_id": "s1",
        })
        self.assertEqual(ev2["kind"], "tool")

    def test_mcp_non_dict_input_is_defensive(self):
        # Non-dict tool_input must not crash; MCP fields stay empty/absent of arg.
        ev = _start_event({
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__context7__resolve-library-id",
            "tool_use_id": "toolu_z",
            "tool_input": "not-a-dict",
            "session_id": "s1",
        })
        self.assertEqual(ev["kind"], "mcp")
        self.assertEqual(ev["server"], "context7")
        self.assertEqual(ev["arg"], "")


class ParseTranscriptActionsTest(unittest.TestCase):
    def _write_jsonl(self, records):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_bash_ok_and_running(self):
        path = self._write_jsonl([
            {"type": "assistant", "timestamp": "2026-06-13T08:00:00Z",
             "message": {"content": [
                 {"type": "tool_use", "id": "tu1", "name": "Bash",
                  "input": {"command": "echo hi"}}]}},
            {"type": "user", "timestamp": "2026-06-13T08:00:01Z",
             "message": {"content": [
                 {"type": "tool_result", "tool_use_id": "tu1",
                  "content": "hi"}]}},
            {"type": "assistant", "timestamp": "2026-06-13T08:00:02Z",
             "message": {"content": [
                 {"type": "tool_use", "id": "tu2", "name": "Read",
                  "input": {"file_path": "/tmp/y"}}]}},
        ])
        result = _aipf.parse_transcript_actions(path)
        actions = result["actions"]
        self.assertEqual(len(actions), 2)

        bash = next(a for a in actions if a["type"] == "bash")
        self.assertEqual(bash["status"], "ok")
        self.assertIn("echo hi", bash["arg"])
        self.assertEqual(bash["relMs"], 0)

        read = next(a for a in actions if a["type"] == "tool")
        self.assertEqual(read["status"], "running")
        self.assertEqual(read["arg"], "/tmp/y")

        self.assertEqual(result["counts"]["bash"], 1)
        self.assertEqual(result["counts"]["tool"], 1)

    def test_mcp_and_error_status(self):
        path = self._write_jsonl([
            {"type": "assistant", "timestamp": "2026-06-13T08:00:00Z",
             "message": {"content": [
                 {"type": "tool_use", "id": "m1",
                  "name": "mcp__context7__resolve-library-id",
                  "input": {"libraryName": "react"}}]}},
            {"type": "user", "timestamp": "2026-06-13T08:00:01Z",
             "message": {"content": [
                 {"type": "tool_result", "tool_use_id": "m1",
                  "is_error": True, "content": "boom"}]}},
        ])
        result = _aipf.parse_transcript_actions(path)
        self.assertEqual(len(result["actions"]), 1)
        a = result["actions"][0]
        self.assertEqual(a["type"], "mcp")
        self.assertEqual(a["name"], "context7 · resolve-library-id")
        self.assertEqual(a["status"], "error")
        self.assertIn("react", a["arg"])
        self.assertEqual(result["counts"]["mcp"], 1)

    def test_robust_to_broken_line(self):
        path = self._write_jsonl([
            {"type": "assistant", "timestamp": "2026-06-13T08:00:00Z",
             "message": {"content": [
                 {"type": "tool_use", "id": "tu1", "name": "Bash",
                  "input": {"command": "true"}}]}},
        ])
        with open(path, "a", encoding="utf-8") as f:
            f.write("{ this is not valid json\n")
        result = _aipf.parse_transcript_actions(path)
        self.assertEqual(len(result["actions"]), 1)


class McpNameParseTest(unittest.TestCase):
    def test_split_helpers_agree(self):
        # Hook-side and server-side parsers must agree on the delimiter rule.
        for full, server, tool in [
            ("mcp__context7__resolve-library-id", "context7", "resolve-library-id"),
            ("mcp__plugin_ai-pathfinder_context7__get-library-docs",
             "plugin_ai-pathfinder_context7", "get-library-docs"),
            ("mcp__noseparator", "", "noseparator"),
        ]:
            self.assertEqual(telemetry_hook._parse_mcp_name(full), (server, tool))
            self.assertEqual(_aipf._split_mcp_name(full), (server, tool))


if __name__ == "__main__":
    unittest.main()
