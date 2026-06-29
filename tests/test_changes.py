#!/usr/bin/env python3
"""Offline regression tests for the Changes-tab backend (stdlib unittest only).

Covers `server.Handler._build_changes(slug)` — the model behind the Changes tab:
`{base, files:[{path, added, removed, status, untracked}], notGit}`. Each test
spins up a throwaway git repo in a tempdir, writes the task's state.json, and
calls `_build_changes` directly (bypassing the 2s `_changes` cache).

No network and no disk outside a tempfile. Tests that need git are gated on
`git_available()`; everything is cross-platform (forward-slash paths in the
model, explicit LF writes so CRLF never skews `_count_lines`, git user.* set
locally so no global config is required).

Run with:
    python -m unittest tests.test_changes
    python -m unittest discover -s tests   # full suite
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts")

import server     # noqa: E402


def git_available():
    """Probe whether `git` is on PATH and runnable — gate for git-only tests."""
    try:
        p = subprocess.run(["git", "--version"], capture_output=True)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _make_handler(workspace):
    """A Handler bound to `workspace` without the HTTP/socket machinery.

    `_build_changes` and its helpers read only `self.workspace` and class-level
    caches, so we drive them directly via `__new__` — fully offline. (Mirrors
    `tests/test_hub.py:_make_handler`.)
    """
    h = server.Handler.__new__(server.Handler)
    h.workspace = workspace
    return h


def _write(path, text):
    # Явный LF: иначе на Windows запись в text-mode даст CRLF и собьёт _count_lines.
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


@unittest.skipUnless(git_available(), "git недоступен")
class BuildChangesGitTest(unittest.TestCase):
    """`_build_changes` over a real throwaway repo: classification, counts,
    untracked expansion, noise filtering, ordering, and base-commit resolution.
    The repo IS the workspace root, so `_task_root` returns it (no worktreePath).
    """

    def setUp(self):
        # realpath: на macOS/Windows tempdir может быть симлинком, а git печатает
        # каноничный путь — иначе commonpath-проверки и cwd разъезжаются.
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        # git-репо прямо в корне workspace; state.json без worktreePath →
        # _task_root вернёт self.root.
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "test@example.com")
        _git(self.root, "config", "user.name", "Test")
        _git(self.root, "config", "commit.gpgsign", "false")
        self.ws = server.Workspace(self.root)
        self.h = _make_handler(self.ws)
        # Сброс классового кеша на всякий случай (мы зовём _build_changes напрямую,
        # но _changes мог быть прогрет другим тестом).
        server.Handler._changes_cache.clear()

    def _cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        server.Handler._changes_cache.clear()

    def _commit(self, msg="c"):
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", msg)

    def _file(self, relpath, text):
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(relpath) else None
        _write(full, text)

    def _set_state(self, slug, **state):
        self.ws.write_json(self.ws.task_file(slug, "state.json"), state)

    def _files_by_path(self, model):
        return {f["path"]: f for f in model["files"]}

    # ---- классификация при baseCommit=HEAD -----------------------------

    def test_modified_exact_counts(self):
        # 3 строки в базе, заменяем одну → +1/-1.
        self._file("a.txt", "one\ntwo\nthree\n")
        self._commit("base")
        self._file("a.txt", "one\nTWO\nthree\n")
        self._set_state("t")  # baseCommit отсутствует → HEAD

        model = self.h._build_changes("t")
        self.assertFalse(model["notGit"])
        by = self._files_by_path(model)
        self.assertIn("a.txt", by)
        self.assertEqual(by["a.txt"]["status"], "modified")
        self.assertEqual(by["a.txt"]["added"], 1)
        self.assertEqual(by["a.txt"]["removed"], 1)
        self.assertFalse(by["a.txt"]["untracked"])

    def test_added_untracked(self):
        self._file("a.txt", "x\n")
        self._commit("base")
        self._file("new.txt", "l1\nl2\n")  # untracked, 2 строки
        self._set_state("t")

        model = self.h._build_changes("t")
        by = self._files_by_path(model)
        self.assertIn("new.txt", by)
        self.assertEqual(by["new.txt"]["status"], "added")
        self.assertTrue(by["new.txt"]["untracked"])
        self.assertEqual(by["new.txt"]["added"], 2)
        self.assertEqual(by["new.txt"]["removed"], 0)

    def test_deleted(self):
        self._file("a.txt", "x\n")
        self._file("gone.txt", "y\n")
        self._commit("base")
        os.remove(os.path.join(self.root, "gone.txt"))
        self._set_state("t")

        model = self.h._build_changes("t")
        by = self._files_by_path(model)
        self.assertIn("gone.txt", by)
        self.assertEqual(by["gone.txt"]["status"], "deleted")
        self.assertFalse(by["gone.txt"]["untracked"])

    def test_untracked_dir_expanded(self):
        # -uall разворачивает untracked-каталог в отдельные файлы, а не "docs/".
        self._file("a.txt", "x\n")
        self._commit("base")
        self._file("docs/one.md", "a\n")
        self._file("docs/two.md", "b\n")
        self._set_state("t")

        model = self.h._build_changes("t")
        by = self._files_by_path(model)
        self.assertIn("docs/one.md", by)
        self.assertIn("docs/two.md", by)
        self.assertNotIn("docs/", by)
        # forward-slash в путях вне зависимости от ОС
        for p in by:
            self.assertNotIn("\\", p)

    def test_noise_empty_untracked_hidden(self):
        self._file("a.txt", "x\n")
        self._commit("base")
        # пустой (0 байт) untracked-файл — скрывается _is_noise
        open(os.path.join(self.root, "empty.txt"), "w").close()
        self._file("real.txt", "data\n")  # непустой untracked остаётся
        self._set_state("t")

        model = self.h._build_changes("t")
        by = self._files_by_path(model)
        self.assertNotIn("empty.txt", by)
        self.assertIn("real.txt", by)

    def test_sorted_by_path(self):
        self._file("a.txt", "x\n")
        self._commit("base")
        self._file("zeta.txt", "z\n")
        self._file("alpha.txt", "a\n")
        self._file("mid.txt", "m\n")
        self._set_state("t")

        model = self.h._build_changes("t")
        paths = [f["path"] for f in model["files"]]
        self.assertEqual(paths, sorted(paths))

    # ---- rename(R) -----------------------------------------------------

    def test_rename_staged(self):
        # staged git mv → git status может выдать R (зависит от detection).
        self._file("old.txt", "line1\nline2\nline3\n")
        self._commit("base")
        _git(self.root, "mv", "old.txt", "new.txt")
        self._set_state("t")

        # Проверяем фактический вывод status; если R нет — осознанный skip.
        st = _git(self.root, "status", "--porcelain", "--untracked-files=all").stdout
        if " -> " not in st and not any(
                ln[:2].strip() == "R" for ln in st.splitlines()):
            self.skipTest("git не классифицировал staged mv как rename(R) "
                          "в этой конфигурации")
        model = self.h._build_changes("t")
        by = self._files_by_path(model)
        self.assertIn("new.txt", by)
        self.assertEqual(by["new.txt"]["status"], "renamed")
        # rest берётся как часть после ' -> ' — старое имя в путях не торчит
        self.assertNotIn("old.txt -> new.txt", by)

    # ---- _base_commit: выбор базы --------------------------------------

    def test_base_commit_older(self):
        # diff против старого коммита: видим изменения обоих последующих.
        self._file("a.txt", "v1\n")
        self._commit("c1")
        old = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        self._file("a.txt", "v2\n")
        self._commit("c2")
        self._file("a.txt", "v3\n")  # рабочее дерево
        self._set_state("t", baseCommit=old)

        model = self.h._build_changes("t")
        self.assertEqual(model["base"], old)
        by = self._files_by_path(model)
        self.assertIn("a.txt", by)
        self.assertEqual(by["a.txt"]["status"], "modified")

    def test_base_commit_missing_falls_back_to_head(self):
        self._file("a.txt", "x\n")
        self._commit("base")
        self._file("a.txt", "y\n")
        # битый/несуществующий base → fallback HEAD
        self._set_state("t", baseCommit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

        model = self.h._build_changes("t")
        self.assertEqual(model["base"], "HEAD")
        by = self._files_by_path(model)
        self.assertIn("a.txt", by)

    def test_base_commit_leading_dash_rejected(self):
        # base с ведущим '-' читался бы git как опция → reject к HEAD, без падения.
        self._file("a.txt", "x\n")
        self._commit("base")
        self._file("a.txt", "y\n")
        self._set_state("t", baseCommit="--output=/tmp/pwn")

        model = self.h._build_changes("t")
        self.assertEqual(model["base"], "HEAD")
        self.assertFalse(model["notGit"])

    def test_base_commit_absent_defaults_head(self):
        self._file("a.txt", "x\n")
        self._commit("base")
        self._file("a.txt", "y\n")
        self._set_state("t")  # нет baseCommit

        model = self.h._build_changes("t")
        self.assertEqual(model["base"], "HEAD")


@unittest.skipUnless(git_available(), "git недоступен")
class BuildChangesWorktreeTest(unittest.TestCase):
    """worktreePath в state.json направляет diff в чужое рабочее дерево."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.addCleanup(server.Handler._changes_cache.clear)
        self.wt = os.path.join(self.root, "wt")
        os.makedirs(self.wt)
        _git(self.wt, "init", "-q")
        _git(self.wt, "config", "user.email", "test@example.com")
        _git(self.wt, "config", "user.name", "Test")
        _git(self.wt, "config", "commit.gpgsign", "false")
        _write(os.path.join(self.wt, "a.txt"), "x\n")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "base")
        # workspace root — пустой каталог рядом (не git); diff должен идти в wt
        self.proj = os.path.join(self.root, "proj")
        os.makedirs(self.proj)
        self.ws = server.Workspace(self.proj)
        self.h = _make_handler(self.ws)

    def test_diff_runs_in_worktree(self):
        _write(os.path.join(self.wt, "a.txt"), "y\n")
        self.ws.write_json(self.ws.task_file("t", "state.json"),
                           {"worktreePath": self.wt})
        model = self.h._build_changes("t")
        self.assertFalse(model["notGit"])
        by = {f["path"]: f for f in model["files"]}
        self.assertIn("a.txt", by)
        self.assertEqual(by["a.txt"]["status"], "modified")


class BuildChangesNonGitTest(unittest.TestCase):
    """Деградация без git-репо и graceful-обработка ошибок (без git на PATH)."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.addCleanup(server.Handler._changes_cache.clear)
        self.ws = server.Workspace(self.root)
        self.h = _make_handler(self.ws)

    @unittest.skipUnless(git_available(), "git недоступен")
    def test_not_git_dir(self):
        # обычный каталог без git → notGit=True, пустой список
        self.ws.write_json(self.ws.task_file("t", "state.json"), {})
        model = self.h._build_changes("t")
        self.assertTrue(model["notGit"])
        self.assertEqual(model["files"], [])
        self.assertIsNone(model["base"])

    def test_changes_wrapper_graceful_on_failure(self):
        # _changes ловит любую ошибку _build_changes и возвращает error-модель,
        # не кидая наружу. Мокаем _build_changes на исключение.
        self.ws.write_json(self.ws.task_file("t", "state.json"), {})

        def boom(slug):
            raise RuntimeError("git blew up")

        self.h._build_changes = boom
        server.Handler._changes_cache.clear()
        model = self.h._changes("t")
        self.assertEqual(model["files"], [])
        self.assertIsNone(model["base"])
        self.assertEqual(model["error"], "git blew up")


if __name__ == "__main__":
    unittest.main()
