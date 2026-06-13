#!/usr/bin/env python3
"""Offline tests for the parallel-runs hub backend (stdlib unittest only).

Covers the contracts of the cross-task hub aggregate (`/hub.json` via
`server._build_hub`), per-session slug resolution (`_aipf.active_slug`), the pure
worktree state writer (`worktree.record_worktree_in_state`), and the per-task
working-tree resolver (`server.Handler._task_root`).

No network and no disk outside a tempfile; no real git worktree is required —
the `_task_root` test exercises only the fallback path. Run with:
    python3 tests/test_hub.py
    python3 -m unittest tests.test_hub
"""

import json
import os
import sys
import tempfile
import time
import unittest

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import _aipf      # noqa: E402
import server     # noqa: E402
import worktree   # noqa: E402


def _now_iso_utc():
    """A fresh ISO-8601/Z timestamp — what state.json updatedAt carries."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_utc_ago(seconds):
    """An ISO-8601/Z timestamp `seconds` in the past."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class ActiveSlugTest(unittest.TestCase):
    """`_aipf.active_slug`: per-session pointer -> active.json -> newest state."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.base = _aipf.workflow_base(self.root)
        # Two tasks so the newest-state fallback has something to pick from.
        for slug in ("task-a", "task-b"):
            _write_json(_aipf.task_file(self.root, slug, "state.json"),
                        {"slug": slug})

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_per_session_pointer_wins(self):
        sid = "sess-1234"
        _write_json(os.path.join(self.base, "active", sid + ".json"),
                    {"slug": "task-a"})
        _write_json(os.path.join(self.base, "active.json"), {"slug": "task-b"})
        self.assertEqual(_aipf.active_slug(self.root, sid), "task-a")

    def test_fallback_to_active_json(self):
        # No per-session file for this session -> use active.json.
        _write_json(os.path.join(self.base, "active.json"), {"slug": "task-b"})
        self.assertEqual(_aipf.active_slug(self.root, "no-such-session"), "task-b")

    def test_invalid_session_id_no_traversal(self):
        # A traversal-y session_id must not be used to read outside active/ and
        # must fall through to active.json (here -> task-b).
        _write_json(os.path.join(self.base, "active.json"), {"slug": "task-b"})
        # A real per-session file under a *valid* name exists, but the bogus id
        # below must never resolve to it (anti-traversal SESSION_ID_RE guard).
        _write_json(os.path.join(self.base, "active", "real.json"),
                    {"slug": "task-a"})
        self.assertIsNone(_aipf.SESSION_ID_RE.match("../../etc"))
        self.assertEqual(_aipf.active_slug(self.root, "../../etc"), "task-b")

    def test_fallback_to_newest_state(self):
        # Nothing pointer-ish at all -> newest state.json by mtime wins.
        a = _aipf.task_file(self.root, "task-a", "state.json")
        b = _aipf.task_file(self.root, "task-b", "state.json")
        old = time.time() - 100
        os.utime(a, (old, old))
        os.utime(b, (time.time(), time.time()))
        self.assertEqual(_aipf.active_slug(self.root, "sess-x"), "task-b")
        # bump task-a so it becomes the newest and the answer flips.
        now = time.time()
        os.utime(a, (now, now))
        self.assertEqual(_aipf.active_slug(self.root, "sess-x"), "task-a")


class RecordWorktreeInStateTest(unittest.TestCase):
    """`worktree.record_worktree_in_state`: pure, idempotent, defensive."""

    def test_records_fields(self):
        state = {"slug": "task-a"}
        out = worktree.record_worktree_in_state(state, "/tmp/wt/task-a", "task-a")
        self.assertEqual(out["worktreePath"], os.path.abspath("/tmp/wt/task-a"))
        self.assertEqual(out["branch"], "task-a")
        self.assertIn("updatedAt", out)
        self.assertEqual(out["slug"], "task-a")  # untouched fields preserved
        # mutates and returns the same dict
        self.assertIs(out, state)

    def test_idempotent_overwrite_no_dupes(self):
        state = {}
        worktree.record_worktree_in_state(state, "/tmp/wt/a", "branch-1")
        worktree.record_worktree_in_state(state, "/tmp/wt/a", "branch-2")
        # second call overwrites, never accumulates extra path/branch keys
        self.assertEqual(state["worktreePath"], os.path.abspath("/tmp/wt/a"))
        self.assertEqual(state["branch"], "branch-2")
        keys = [k for k in state if k in ("worktreePath", "branch")]
        self.assertEqual(sorted(keys), ["branch", "worktreePath"])

    def test_tolerates_non_dict(self):
        # None / non-dict must not raise; a fresh dict is minted.
        out = worktree.record_worktree_in_state(None, "/tmp/wt/x", "b")
        self.assertIsInstance(out, dict)
        self.assertEqual(out["branch"], "b")
        out2 = worktree.record_worktree_in_state("not-a-dict", "/tmp/wt/y", "c")
        self.assertIsInstance(out2, dict)
        self.assertEqual(out2["worktreePath"], os.path.abspath("/tmp/wt/y"))


def _make_handler(workspace):
    """A Handler bound to `workspace` without running the HTTP/socket machinery.

    The hub methods (`_build_hub`, `_hub_run`, `_hub_is_active`, `_task_root`,
    `_git`) read only `self.workspace` and class-level caches, so we drive them
    directly via `__new__` — fully offline, deterministic, and with no port or
    background process to leak. (HTTP would also work; direct is leaner here.)
    """
    h = server.Handler.__new__(server.Handler)
    h.workspace = workspace
    return h


class BuildHubContractTest(unittest.TestCase):
    """`/hub.json` aggregate: runs/analytics keys, active/history split,
    analytics counters, and graceful degradation on bad/missing telemetry."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        # reset the singleton hub cache so each test sees fresh data
        server.Handler._hub_cache.clear()
        self.handler = _make_handler(self.ws)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        server.Handler._hub_cache.clear()

    def _task(self, slug, state=None, dash=None, telemetry=None):
        if state is not None:
            _write_json(self.ws.task_file(slug, "state.json"), state)
        if dash is not None:
            _write_json(self.ws.task_file(slug, "dashboard.json"), dash)
        if telemetry is not None:
            tpath = self.ws.task_file(slug, "telemetry.jsonl")
            os.makedirs(os.path.dirname(tpath), exist_ok=True)
            with open(tpath, "w", encoding="utf-8") as f:
                f.write(telemetry)

    def _telemetry_lines(self, events):
        return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)

    def _seed_three_tasks(self):
        # active: IMPLEMENT + fresh updatedAt, two sessions, one subagent + activity
        self._task(
            "task-active",
            state={"slug": "task-active", "phase": "IMPLEMENT", "iteration": 2,
                   "createdAt": _iso_utc_ago(600), "updatedAt": _now_iso_utc()},
            dash={"title": "Active one", "phase": "IMPLEMENT",
                  "progress": {"done": 1, "total": 3}},
            telemetry=self._telemetry_lines([
                {"event": "session.start", "session_id": "s1", "ts": _iso_utc_ago(600)},
                {"event": "subagent.start", "session_id": "s1", "ts": _iso_utc_ago(500),
                 "spanId": "span-1", "role": "coder"},
                {"event": "tool.start", "session_id": "s1", "ts": _iso_utc_ago(480),
                 "tool": "Bash"},
                {"event": "file.touch", "session_id": "s2", "ts": _iso_utc_ago(400),
                 "file": "x.py"},
                {"event": "session.start", "session_id": "s2", "ts": _iso_utc_ago(450)},
            ]),
        )
        # history (terminal): DONE phase, fresh ts but terminal -> not active
        self._task(
            "task-done",
            state={"slug": "task-done", "phase": "DONE", "iteration": 4,
                   "createdAt": _iso_utc_ago(7200), "updatedAt": _now_iso_utc()},
            dash={"title": "Done one", "phase": "DONE",
                  "progress": {"done": 5, "total": 5}},
            telemetry=self._telemetry_lines([
                {"event": "session.start", "session_id": "sd", "ts": _iso_utc_ago(7000)},
                {"event": "subagent.start", "session_id": "sd", "ts": _iso_utc_ago(6900)},
            ]),
        )
        # history (stale): non-terminal phase but updatedAt past the window
        stale = server.HUB_ACTIVE_WINDOW_SEC + 3600
        self._task(
            "task-stale",
            state={"slug": "task-stale", "phase": "IMPLEMENT", "iteration": 1,
                   "createdAt": _iso_utc_ago(stale + 600),
                   "updatedAt": _iso_utc_ago(stale)},
            dash={"title": "Stale one", "phase": "IMPLEMENT"},
            telemetry=self._telemetry_lines([
                {"event": "session.start", "session_id": "ss", "ts": _iso_utc_ago(stale)},
            ]),
        )

    def test_top_level_keys(self):
        self._seed_three_tasks()
        hub = self.handler._build_hub()
        self.assertIn("runs", hub)
        self.assertIn("analytics", hub)
        self.assertEqual(len(hub["runs"]), 3)

    def test_active_history_classification(self):
        self._seed_three_tasks()
        runs = {r["slug"]: r for r in self.handler._build_hub()["runs"]}
        self.assertTrue(runs["task-active"]["active"])      # IMPLEMENT + fresh
        self.assertFalse(runs["task-done"]["active"])       # DONE (terminal)
        self.assertFalse(runs["task-stale"]["active"])      # stale updatedAt

    def test_is_active_unit(self):
        now = time.time()
        # terminal phase is never active even with a fresh timestamp
        self.assertFalse(self.handler._hub_is_active("DONE", _now_iso_utc(), now))
        self.assertFalse(self.handler._hub_is_active("ABORTED", _now_iso_utc(), now))
        # non-terminal + fresh -> active
        self.assertTrue(self.handler._hub_is_active("IMPLEMENT", _now_iso_utc(), now))
        # non-terminal + stale -> history
        stale = _iso_utc_ago(server.HUB_ACTIVE_WINDOW_SEC + 60)
        self.assertFalse(self.handler._hub_is_active("IMPLEMENT", stale, now))
        # no timestamp -> treat as active (don't hide live runs)
        self.assertTrue(self.handler._hub_is_active("IMPLEMENT", None, now))

    def test_analytics_counters(self):
        self._seed_three_tasks()
        a = self.handler._build_hub()["analytics"]
        self.assertEqual(a["total"], 3)
        self.assertEqual(a["active"], 1)
        self.assertEqual(a["done"], 1)
        # phase distribution: two IMPLEMENT + one DONE
        self.assertEqual(a["phases"].get("IMPLEMENT"), 2)
        self.assertEqual(a["phases"].get("DONE"), 1)
        # subagents: 1 (active) + 1 (done) + 0 (stale) = 2
        self.assertEqual(a["subagents"], 2)
        # distinct sessions per task summed: 2 + 1 + 1 = 4
        self.assertEqual(a["sessions"], 4)
        # iterations summed: 2 + 4 + 1 = 7
        self.assertEqual(a["iterations"], 7)
        # activity (tool.* + file.touch): only the active task has 2
        self.assertEqual(a["activity"], 2)

    def test_per_task_telemetry_counters(self):
        self._seed_three_tasks()
        runs = {r["slug"]: r for r in self.handler._build_hub()["runs"]}
        act = runs["task-active"]
        self.assertEqual(act["subagents"], 1)
        self.assertEqual(act["sessions"], 2)        # s1 + s2
        self.assertEqual(act["activity"], 2)        # tool.start + file.touch
        self.assertEqual(act["events"], 5)

    def test_graceful_on_broken_telemetry_line(self):
        # A garbage JSON line must not raise; the run is still produced and the
        # valid events before/after it are still counted.
        self._task(
            "task-broken",
            state={"slug": "task-broken", "phase": "IMPLEMENT",
                   "updatedAt": _now_iso_utc()},
            dash={"title": "Broken telemetry"},
            telemetry=(
                json.dumps({"event": "session.start", "session_id": "s1",
                            "ts": _now_iso_utc()}) + "\n"
                + "{ this is not valid json\n"
                + json.dumps({"event": "subagent.start", "session_id": "s1",
                              "ts": _now_iso_utc()}) + "\n"
            ),
        )
        hub = self.handler._build_hub()
        run = next(r for r in hub["runs"] if r["slug"] == "task-broken")
        self.assertEqual(run["subagents"], 1)       # the good lines counted
        self.assertEqual(run["sessions"], 1)
        self.assertNotIn("error", hub)

    def test_graceful_on_missing_telemetry(self):
        # No telemetry.jsonl at all -> zeros, no exception, still a 1-run hub.
        self._task(
            "task-nofile",
            state={"slug": "task-nofile", "phase": "PLAN",
                   "updatedAt": _now_iso_utc()},
            dash={"title": "No telemetry file"},
        )
        hub = self.handler._build_hub()
        run = next(r for r in hub["runs"] if r["slug"] == "task-nofile")
        self.assertEqual(run["events"], 0)
        self.assertEqual(run["subagents"], 0)
        self.assertEqual(run["sessions"], 0)
        self.assertEqual(run["activity"], 0)

    def test_empty_store_is_well_formed(self):
        # No tasks at all -> empty runs, zeroed analytics, no error.
        hub = self.handler._build_hub()
        self.assertEqual(hub["runs"], [])
        self.assertEqual(hub["analytics"]["total"], 0)
        self.assertEqual(hub["analytics"]["active"], 0)
        self.assertIsNone(hub["analytics"]["medianDurationMs"])


class TaskRootFallbackTest(unittest.TestCase):
    """`Handler._task_root`: a missing/bogus worktreePath falls back to root."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        self.handler = _make_handler(self.ws)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_worktree_path_falls_back_to_root(self):
        _write_json(self.ws.task_file("plain", "state.json"),
                    {"slug": "plain", "phase": "IMPLEMENT"})
        self.assertEqual(self.handler._task_root("plain"), self.ws.root)

    def test_bogus_worktree_path_falls_back_to_root(self):
        bogus = os.path.join(self.root, "does", "not", "exist")
        _write_json(self.ws.task_file("ghost", "state.json"),
                    {"slug": "ghost", "worktreePath": bogus})
        self.assertEqual(self.handler._task_root("ghost"), self.ws.root)

    def test_missing_state_falls_back_to_root(self):
        # No state.json for the slug at all -> root (never raises).
        self.assertEqual(self.handler._task_root("absent"), self.ws.root)


if __name__ == "__main__":
    unittest.main()
