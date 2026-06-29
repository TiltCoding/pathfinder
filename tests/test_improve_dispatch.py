#!/usr/bin/env python3
"""Offline tests for the `/improve` DISPATCH seed contract (stdlib unittest only).

DISPATCH (plan block b5) seeds, per chosen feature, a feature-task directory whose
`state.json` carries `phase:"EXPLORE"`/`checkpoint:"working"` and a fresh
`updatedAt`. The point of these tests is to pin that seed shape against the *real*
server logic that consumes it: a task seeded the way `/improve` seeds it must be
classified **active** by the hub (`server._build_hub` / `_hub_is_active`) so it
shows up as a live run, and the worktree read-modify-write
(`worktree.record_worktree_in_state`) must *preserve* the rich seeded fields and
only add `worktreePath`/`branch` (the idempotent DISPATCH invariant).

Reuses the patterns of tests/test_hub.py: the same `scripts/` sys.path hack,
`server.Workspace`, the `_make_handler` shortcut, and a tempfile-only store — no
network, no real git worktree.

Run with:
    python3 -m unittest tests.test_improve_dispatch
    python3 -m unittest discover -s tests   # full suite
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

import server     # noqa: E402
import worktree   # noqa: E402


def _now_iso_utc():
    """A fresh ISO-8601/Z timestamp — what a seeded state.json updatedAt carries."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_utc_ago(seconds):
    """An ISO-8601/Z timestamp `seconds` in the past."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _make_handler(workspace):
    """A Handler bound to `workspace` without the HTTP/socket machinery.

    The hub methods read only `self.workspace` and class-level caches, so we
    drive them directly via `__new__` — fully offline and deterministic, no port
    or background process to leak (same shortcut as tests/test_hub.py)."""
    h = server.Handler.__new__(server.Handler)
    h.workspace = workspace
    return h


def _seed_state(slug, phase="EXPLORE", updated=None):
    """A state.json exactly as DISPATCH seeds a feature run (plan b5 step 4)."""
    return {
        "slug": slug,
        "phase": phase,
        "iteration": 0,
        "checkpoint": "working",
        "createdAt": _iso_utc_ago(5),
        "updatedAt": updated if updated is not None else _now_iso_utc(),
    }


def _seed_dashboard(title, phase="EXPLORE"):
    """The minimal dashboard.json DISPATCH seeds alongside the state (b5 step 5)."""
    return {"title": title, "phase": phase, "status": "working"}


class DispatchSeedHubActiveTest(unittest.TestCase):
    """A feature task seeded by /improve DISPATCH is classified hub-active."""

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

    def _seed_task(self, slug, phase="EXPLORE", updated=None, title=None):
        _write_json(self.ws.task_file(slug, "state.json"),
                    _seed_state(slug, phase=phase, updated=updated))
        _write_json(self.ws.task_file(slug, "dashboard.json"),
                    _seed_dashboard(title or slug, phase=phase))

    def test_seeded_explore_working_task_is_hub_active(self):
        # The canonical DISPATCH seed: EXPLORE / working / fresh updatedAt.
        self._seed_task("feat-fast-csv", phase="EXPLORE", title="Быстрый экспорт CSV")
        runs = {r["slug"]: r for r in self.handler._build_hub()["runs"]}
        self.assertIn("feat-fast-csv", runs)
        run = runs["feat-fast-csv"]
        self.assertTrue(run["active"])
        # the seeded fields survive into the run card unchanged
        self.assertEqual(run["phase"], "EXPLORE")
        self.assertEqual(run["status"], "working")
        self.assertEqual(run["iteration"], 0)
        self.assertEqual(run["title"], "Быстрый экспорт CSV")

    def test_seeded_intake_task_also_active(self):
        # Sanity: any non-terminal seeded phase with a fresh stamp is active too.
        self._seed_task("feat-intake-only", phase="INTAKE")
        runs = {r["slug"]: r for r in self.handler._build_hub()["runs"]}
        self.assertTrue(runs["feat-intake-only"]["active"])

    def test_fresh_explore_seed_is_active_stale_is_not(self):
        # The seed contract hinges on a *fresh* updatedAt. Drive the unit
        # predicate directly with the real window constant.
        now = time.time()
        fresh = _seed_state("feat-x", phase="EXPLORE")
        self.assertTrue(
            self.handler._hub_is_active(fresh["phase"], fresh["updatedAt"], now))
        # An updatedAt older than the active window flips it to history.
        stale_when = _iso_utc_ago(server.HUB_ACTIVE_WINDOW_SEC + 600)
        stale = _seed_state("feat-x", phase="EXPLORE", updated=stale_when)
        self.assertFalse(
            self.handler._hub_is_active(stale["phase"], stale["updatedAt"], now))


class DispatchWorktreeReadModifyWriteTest(unittest.TestCase):
    """`record_worktree_in_state` over a richly-seeded state preserves our fields
    and only adds worktreePath/branch — the read-modify-write DISPATCH invariant
    (plan b5 step 4: minted worktree state + seeded EXPLORE fields coexist)."""

    def test_preserves_seeded_fields_adds_worktree_only(self):
        state = _seed_state("feat-rich", phase="EXPLORE")
        # extra rich fields the orchestrator layers on during DISPATCH
        state["title"] = "Улучшение отчётов"
        state["baseCommit"] = "abc1234"
        out = worktree.record_worktree_in_state(state, "/tmp/wt/feat-rich",
                                                "feat-rich")
        # worktree fields added
        self.assertEqual(out["worktreePath"], os.path.abspath("/tmp/wt/feat-rich"))
        self.assertEqual(out["branch"], "feat-rich")
        # seeded fields preserved verbatim
        self.assertEqual(out["phase"], "EXPLORE")
        self.assertEqual(out["checkpoint"], "working")
        self.assertEqual(out["iteration"], 0)
        self.assertEqual(out["title"], "Улучшение отчётов")
        self.assertEqual(out["baseCommit"], "abc1234")
        self.assertEqual(out["slug"], "feat-rich")
        # mutates and returns the same dict (no copy)
        self.assertIs(out, state)


if __name__ == "__main__":
    unittest.main()
