#!/usr/bin/env python3
"""Offline tests for the parallel-runs hub backend (stdlib unittest only).

Covers the contracts of the cross-task hub aggregate (`/hub.json` via
`server._build_hub`), per-session slug resolution (`_aipf.active_slug`), the pure
worktree state writer (`worktree.record_worktree_in_state`), and the per-task
working-tree resolver (`server.Handler._task_root`).

No network and no disk outside a tempfile; no real git worktree is required —
the `_task_root` test exercises only the fallback path.

Run with:
    python3 -m unittest tests.test_hub
    python3 -m unittest discover -s tests   # full suite
"""

import json
import os
import shutil
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


class AwaitingFlagTest(unittest.TestCase):
    """`_hub_run` append-only `awaiting` flag (b1, OR-formula): True when the
    task is parked at a batch gate — `state.checkpoint == "awaiting-batch"` OR
    `dashboard.json.status == "awaiting-batch"`. The flag is purely cosmetic and
    must NOT leak into the active/history split (`_hub_is_active` is untouched)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        # reset the singleton hub cache so each test sees fresh data
        server.Handler._hub_cache.clear()
        self.handler = _make_handler(self.ws)

    def _cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        server.Handler._hub_cache.clear()

    def _task(self, slug, state=None, dash=None):
        if state is not None:
            _write_json(self.ws.task_file(slug, "state.json"), state)
        if dash is not None:
            _write_json(self.ws.task_file(slug, "dashboard.json"), dash)

    def _run(self, slug):
        """The single run card for `slug` straight out of `_build_hub`."""
        return next(r for r in self.handler._build_hub()["runs"]
                    if r["slug"] == slug)

    def test_checkpoint_awaiting_sets_flag_and_keeps_active(self):
        # state.checkpoint == awaiting-batch on a non-terminal phase with a fresh
        # updatedAt -> awaiting True AND active True (flag does not break active).
        self._task(
            "task-gate",
            state={"slug": "task-gate", "phase": "IMPLEMENT",
                   "checkpoint": "awaiting-batch",
                   "updatedAt": _now_iso_utc()},
            dash={"title": "Ждёт ответа"},
        )
        run = self._run("task-gate")
        self.assertTrue(run["awaiting"])
        self.assertTrue(run["active"])     # invariant: flag not mixed into active

    def test_working_checkpoint_no_flag(self):
        # checkpoint == working and no awaiting status in the dashboard -> False.
        self._task(
            "task-working",
            state={"slug": "task-working", "phase": "IMPLEMENT",
                   "checkpoint": "working",
                   "updatedAt": _now_iso_utc()},
            dash={"title": "В работе", "status": "working"},
        )
        run = self._run("task-working")
        self.assertFalse(run["awaiting"])
        self.assertTrue(run["active"])

    def test_dashboard_status_awaiting_sets_flag(self):
        # OR-branch (q1=A): dashboard.json.status == awaiting-batch with no
        # awaiting checkpoint in state still raises the flag.
        self._task(
            "task-dash-gate",
            state={"slug": "task-dash-gate", "phase": "PLAN",
                   "updatedAt": _now_iso_utc()},   # no `checkpoint` key at all
            dash={"title": "Гейт из дашборда", "status": "awaiting-batch"},
        )
        run = self._run("task-dash-gate")
        self.assertTrue(run["awaiting"])
        self.assertTrue(run["active"])

    def test_terminal_awaiting_is_cosmetic_not_active(self):
        # A terminal (DONE) task that still carries awaiting-batch -> the flag is
        # True (cosmetic) but the run is NOT active (it lives in history). This
        # nails the invariant that awaiting never sneaks a finished task back
        # into the active list.
        self._task(
            "task-done-gate",
            state={"slug": "task-done-gate", "phase": "DONE",
                   "checkpoint": "awaiting-batch",
                   "updatedAt": _now_iso_utc()},
            dash={"title": "Завершённая с флагом", "status": "awaiting-batch"},
        )
        run = self._run("task-done-gate")
        self.assertTrue(run["awaiting"])
        self.assertFalse(run["active"])    # terminal -> history despite the flag


class QueueEndpointTest(unittest.TestCase):
    """`Handler._queue`: passthrough read of the project-level `/improve`
    dispatch queue (`<workspace.base>/dispatch-queue.json`, contract:
    skills/improve/dispatch-queue.md) with a graceful empty default — the
    endpoint must never 500 on a missing or corrupt file (ADR-0014 / b4)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ws = server.Workspace(self.root)
        os.makedirs(self.ws.tasks, exist_ok=True)
        self.handler = _make_handler(self.ws)

    def _queue_path(self):
        # The shared store lives at <workspace.base> (= <root>/.workflow), not a
        # worktree copy (ADR-0010): that is where _queue reads from.
        return os.path.join(self.ws.base, "dispatch-queue.json")

    def test_populated_queue_passthrough(self):
        # Fixture mirrors the real .workflow/dispatch-queue.json contract:
        # top-level metadata + items[] spanning every status, with `failed` and
        # `skipped` explicitly present (they are the core value of the feature).
        fixture = {
            "version": 1,
            "source": "improve-overall",
            "mode": "sequential-feature",
            "autonomous": True,
            "createdAt": "2026-06-15T15:13:00",
            "updatedAt": "2026-06-16T10:51:17",
            "baseCommit": "67d305ca1c750a38ce4f4b364b5c9b076aa0c5dc",
            "items": [
                {"n": 1, "featId": "feat-1", "slug": "task-done",
                 "title": "Готовая фича", "candId": "cand-1", "prism": "DX",
                 "status": "done"},
                {"n": 2, "featId": "feat-2", "slug": "task-progress",
                 "title": "В работе", "candId": "cand-2", "prism": "UX",
                 "status": "in-progress"},
                {"n": 3, "featId": "feat-3", "slug": "task-pending",
                 "title": "В очереди", "candId": "cand-3", "prism": "надёжность",
                 "status": "pending"},
                {"n": 4, "featId": "feat-4", "slug": "task-failed",
                 "title": "Упавшая фича", "candId": "cand-4", "prism": "перф",
                 "status": "failed"},
                {"n": 5, "featId": "feat-5", "slug": "task-skipped",
                 "title": "Пропущенная фича", "candId": "cand-5", "prism": "DX",
                 "status": "skipped"},
            ],
        }
        _write_json(self._queue_path(), fixture)

        out = self.handler._queue()

        # passthrough: top-level keys come through verbatim.
        self.assertIn("version", out)
        self.assertEqual(out["version"], 1)
        self.assertIn("items", out)
        self.assertEqual(len(out["items"]), 5)

        # the opt-in top-level autonomous-drain flag (b4/D9) survives the
        # verbatim passthrough -- contract insurance, _queue is server-agnostic.
        self.assertIn("autonomous", out)
        self.assertEqual(out["autonomous"], True)

        # every status is preserved, including failed/skipped.
        statuses = [i["status"] for i in out["items"]]
        self.assertEqual(
            statuses,
            ["done", "in-progress", "pending", "failed", "skipped"],
        )
        self.assertIn("failed", statuses)
        self.assertIn("skipped", statuses)

        # the per-item render fields the UI relies on survive the passthrough.
        first = out["items"][0]
        for key in ("n", "slug", "title", "status"):
            self.assertIn(key, first)
        self.assertEqual(first["n"], 1)
        self.assertEqual(first["slug"], "task-done")
        self.assertEqual(first["title"], "Готовая фича")
        self.assertEqual(first["status"], "done")

    def test_missing_file_is_graceful(self):
        # Nothing written to <workspace.base> -> {"items": []}, never raises.
        out = self.handler._queue()
        self.assertEqual(out, {"items": []})

    def test_corrupt_json_is_graceful(self):
        # A malformed file must degrade to the empty default, not 500
        # (read_json swallows JSONDecodeError).
        path = self._queue_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json\n")
        out = self.handler._queue()
        self.assertEqual(out, {"items": []})


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
