#!/usr/bin/env python3
"""ai-pathfinder telemetry hook.

A single dispatcher wired to several Claude Code hook events. It reads the hook
JSON on stdin, derives the workflow context (task slug + phase/iteration from
`state.json`), and appends ONE line to `.workflow/tasks/<slug>/telemetry.jsonl`.

Design rules:
  * Fast hot path — only an append to a local file. No network here; the
    companion server forwards to Langfuse asynchronously.
  * Never break the workflow — any error exits 0 silently. Telemetry must not
    block tools or fail a turn.

The events captured form the trace tree: a `session.start`/`session.end` span
per `/feature` invocation, a `subagent.start`/`subagent.end` span per sub-agent
(parallel ones are siblings — the "branching" view), and `file.touch` markers.
"""

import json
import sys

try:
    import _aipf
except ImportError:  # when invoked as a path, ensure the script dir is importable
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _aipf


SUBAGENT_TOOLS = {"Task", "Agent"}
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Tools whose start/end timings feed the live trace feed. Sub-agents (Task/Agent)
# are deliberately excluded — they already get richer `subagent.*` spans, so we do
# not duplicate them as `tool.*`. Any tool not in this set is silently dropped: the
# matcher is `.*` (every tool fires the hook), and this set is the noise filter.
TRACE_TOOLS = {"Bash", "Read", "Grep", "Glob", "Edit", "Write", "MultiEdit", "NotebookEdit"}

# Per-tool "key argument" — the single field worth surfacing in the feed.
_TRACE_ARG_FIELD = {
    "Bash": "command",
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "file_path",
    "Grep": "pattern",
    "Glob": "pattern",
}


def resolve_slug(root, session_id, tool_input):
    """Prefer the slug embedded in a Task prompt; else the active task."""
    if isinstance(tool_input, dict):
        for key in ("prompt", "workspace", "file_path", "description"):
            slug = _aipf.slug_from_workspace_path(str(tool_input.get(key, "")))
            if slug:
                return slug
        slug = _aipf.slug_from_workspace_path(json.dumps(tool_input, ensure_ascii=False))
        if slug:
            return slug
    return _aipf.active_slug(root, session_id)


def context_from_state(root, slug):
    state = _aipf.read_json(_aipf.task_file(root, slug, "state.json"), {})
    return {"phase": state.get("phase"), "iteration": state.get("iteration")}


def build_event(payload):
    event = payload.get("hook_event_name") or ""
    root = payload.get("cwd") or "."
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    tool_use_id = payload.get("tool_use_id")

    base = {"ts": _aipf.now_iso_utc(), "session_id": session_id}

    if event == "SessionStart":
        slug = _aipf.active_slug(root, session_id)
        if not slug:
            return None, None
        return slug, {**base, "event": "session.start", **context_from_state(root, slug),
                      "summary": payload.get("source")}

    if event == "SessionEnd":
        slug = _aipf.active_slug(root, session_id)
        if not slug:
            return None, None
        return slug, {**base, "event": "session.end", "summary": payload.get("reason")}

    if event == "Stop":
        slug = _aipf.active_slug(root, session_id)
        if not slug:
            return None, None
        return slug, {**base, "event": "turn.stop", **context_from_state(root, slug)}

    if event in ("PreToolUse", "PostToolUse") and tool_name in SUBAGENT_TOOLS:
        slug = resolve_slug(root, session_id, tool_input)
        if not slug:
            return None, None
        ctx = context_from_state(root, slug)
        role = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
        span = "span-" + tool_use_id if tool_use_id else None
        if event == "PreToolUse":
            return slug, {**base, "event": "subagent.start", "role": role,
                          "spanId": span, "toolUseId": tool_use_id,
                          "bg": bool(tool_input.get("run_in_background")) if isinstance(tool_input, dict) else None,
                          "summary": (tool_input.get("description") if isinstance(tool_input, dict) else None),
                          **ctx}
        return slug, {**base, "event": "subagent.end", "role": role,
                      "spanId": span, "toolUseId": tool_use_id, "ok": True,
                      "summary": _summary(_tool_result(payload)), **ctx}

    if event == "PostToolUse" and tool_name in FILE_TOOLS:
        slug = resolve_slug(root, session_id, tool_input)
        if not slug:
            return None, None
        fpath = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        # Two events: `file.touch` (consumed by Langfuse — keep it FIRST so the
        # forwarded order is unchanged) and `tool.end` (closes the `tool.start`
        # emitted on PreToolUse via the TRACE_TOOLS branch, so the feed action
        # does not hang "running…" forever). No phase/iteration: `tool.*` events
        # never consume it, and it costs a state.json read on the hot path.
        file_touch = {**base, "event": "file.touch", "tool": tool_name, "file": fpath,
                      **context_from_state(root, slug)}
        span = "tool-" + tool_use_id if tool_use_id else None
        tool_end = {**base, "event": "tool.end", "tool": tool_name,
                    "toolUseId": tool_use_id, "spanId": span,
                    "ok": _result_ok(_tool_result(payload))}
        return slug, [file_touch, tool_end]

    if event in ("PreToolUse", "PostToolUse") and tool_name in TRACE_TOOLS:
        # Live trace feed: start/end timings for the significant tool set. Runs
        # ONLY for TRACE_TOOLS; every other tool (TodoWrite etc.) falls through
        # and the hook exits without writing — that is the noise filter.
        slug = resolve_slug(root, session_id, tool_input)
        if not slug:
            return None, None
        # No phase/iteration here: `tool.*` events never consume it (build_feed
        # ignores it, Langfuse does not forward `tool.*`), so we skip the
        # state.json read on this hot path.
        span = "tool-" + tool_use_id if tool_use_id else None
        if event == "PreToolUse":
            return slug, {**base, "event": "tool.start", "tool": tool_name,
                          "toolUseId": tool_use_id, "spanId": span,
                          "arg": _trace_arg(tool_name, tool_input)}
        return slug, {**base, "event": "tool.end", "tool": tool_name,
                      "toolUseId": tool_use_id, "spanId": span,
                      "ok": _result_ok(_tool_result(payload))}

    if event == "SubagentStop":
        # Backstop: only useful if the payload identifies the sub-agent.
        slug = _aipf.active_slug(root, session_id)
        role = payload.get("agent_type") or payload.get("subagent_type")
        if not slug or not role:
            return None, None
        return slug, {**base, "event": "subagent.end", "role": role,
                      "ok": payload.get("outcome", "success") == "success",
                      "summary": _summary(payload.get("summary"))}

    return None, None


def _summary(value, limit=500):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


def _trace_arg(tool_name, tool_input):
    """Cheaply pull the one key argument worth showing in the feed (trimmed)."""
    if not isinstance(tool_input, dict):
        return None
    field = _TRACE_ARG_FIELD.get(tool_name)
    if not field:
        return None
    return _summary(tool_input.get(field), limit=200)


def _tool_result(payload):
    """The PostToolUse tool result. Claude Code names this field `tool_response`;
    older payloads used `tool_result`, so accept both."""
    return payload.get("tool_response", payload.get("tool_result"))


def _result_ok(tool_result):
    """Best-effort success flag from a PostToolUse tool_result. Defaults True."""
    if isinstance(tool_result, dict):
        if tool_result.get("is_error"):
            return False
        status = tool_result.get("status")
        if isinstance(status, str) and status.lower() in ("error", "failed", "failure"):
            return False
    return True


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0
    try:
        root = payload.get("cwd") or "."
        slug, event = build_event(payload)
        if slug and event:
            # `event` may be a single dict or a list of dicts (e.g. file tools
            # emit both `file.touch` and `tool.end`). Append each in order.
            path = _aipf.task_file(root, slug, "telemetry.jsonl")
            events = event if isinstance(event, list) else [event]
            for ev in events:
                _aipf.append_jsonl(path, ev)
    except Exception:
        pass  # telemetry must never break the workflow
    return 0


if __name__ == "__main__":
    sys.exit(main())
