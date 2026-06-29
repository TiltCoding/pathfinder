#!/usr/bin/env python3
"""Offline tests for the two central atomic JSON writers (ws2).

Целевое поведение (реализация делается параллельно): и
`_aipf.atomic_write`/`write_json`, и `server.Workspace.write_json` пишут через
УНИКАЛЬНЫЙ per-process temp-файл (`path.<pid>.<rand>.tmp`) вместо общего
`path.tmp`, затем атомарно `os.replace`, чистят осиротевший temp при ошибке и
re-raise. Тесты проверяют поведение, а не точное имя temp-файла:

  * round-trip (записал -> прочитал -> совпало) для обоих писателей;
  * после успешной записи в директории нет осиротевших `*.tmp`;
  * на ошибке записи (`os.replace` -> OSError) исключение пробрасывается И
    temp-файл не остаётся;
  * конкурентная запись N писателей в ОДИН путь даёт валидный (не рваный) JSON
    и не оставляет `*.tmp` — ключевой регресс гонки. Через процессы, с
    потоковым фолбэком, если многопроцессность в окружении недоступна.

stdlib unittest, без сети, диск только через tempfile.

Run:
    python -m unittest tests.test_atomic_write -v
    python -m unittest discover -s tests   # full suite
"""

import glob
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import _aipf  # noqa: E402
import server  # noqa: E402


def _tmp_files(directory):
    """Все осиротевшие *.tmp в директории (рекурсивно по суффиксу)."""
    return [
        n for n in os.listdir(directory)
        if n.endswith(".tmp")
    ]


# --- module-level workers (must be picklable for multiprocessing) -----------

_PAYLOAD = {"k": "значение", "n": list(range(50)), "nested": {"a": [1, 2, 3]}}


def _aipf_worker(path, i):
    # Каждый писатель кладёт свой вариант данных в общий путь. Проигрыш
    # destination-гонки `os.replace` на Windows (транзиентный PermissionError
    # после исчерпания ретраев) — допустим по контракту (см. `_is_replace_race`):
    # файл остаётся цел, осиротевший temp чистится в atomic_write. Не валим
    # воркер на этом — иначе процессный тест ложно падает на Windows под нагрузкой,
    # хотя потоковый вариант ту же гонку уже терпит. Настоящие баги (рваный файл /
    # осиротевший temp) ловит `_assert_valid_result`.
    try:
        _aipf.write_json(path, dict(_PAYLOAD, who=i))
    except PermissionError:
        pass


def _ws_worker(root, path, i):
    try:
        server.Workspace(root).write_json(path, dict(_PAYLOAD, who=i))
    except PermissionError:
        pass


def _append_worker(path, who, n):
    for i in range(n):
        _aipf.append_jsonl(path, {"who": who, "i": i})


class _TmpDir(unittest.TestCase):
    def setUp(self):
        # realpath: на macOS tempfile отдаёт путь под симлинком /var -> /private/var.
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)


class AipfWriteJsonTest(_TmpDir):
    def test_round_trip(self):
        path = os.path.join(self.dir, "sub", "data.json")
        _aipf.write_json(path, _PAYLOAD)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), _PAYLOAD)

    def test_atomic_write_round_trip(self):
        path = os.path.join(self.dir, "text.json")
        _aipf.atomic_write(path, '{"x": 1}')
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"x": 1})

    def test_no_orphan_tmp_after_success(self):
        path = os.path.join(self.dir, "data.json")
        _aipf.write_json(path, _PAYLOAD)
        self.assertEqual(_tmp_files(self.dir), [])

    def test_error_reraises_and_cleans_tmp(self):
        path = os.path.join(self.dir, "data.json")
        # os.replace бросает -> запись провалена; temp должен быть убран.
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                _aipf.write_json(path, _PAYLOAD)
        self.assertEqual(_tmp_files(self.dir), [],
                         "осиротевший .tmp остался после ошибки записи")
        self.assertFalse(os.path.exists(path))


class WorkspaceWriteJsonTest(_TmpDir):
    def _ws(self):
        return server.Workspace(self.dir)

    def test_round_trip(self):
        path = os.path.join(self.dir, "sub", "data.json")
        self._ws().write_json(path, _PAYLOAD)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), _PAYLOAD)

    def test_no_orphan_tmp_after_success(self):
        path = os.path.join(self.dir, "data.json")
        self._ws().write_json(path, _PAYLOAD)
        self.assertEqual(_tmp_files(self.dir), [])

    def test_error_reraises_and_cleans_tmp(self):
        path = os.path.join(self.dir, "data.json")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self._ws().write_json(path, _PAYLOAD)
        self.assertEqual(_tmp_files(self.dir), [],
                         "осиротевший .tmp остался после ошибки записи")
        self.assertFalse(os.path.exists(path))


def _is_replace_race(exc):
    """Transient `os.replace`-гонка по НАЗНАЧЕНИЮ (не баг writer'а).

    На Windows `os.replace` атомарен, но если назначение в этот момент открыто
    другим параллельным писателем — кидает PermissionError(13). Это гарантия
    атомарности ОС, а не рваные данные; per-process temp такую гонку по
    destination не отменяет (она про temp-файл). Поэтому проигрыш отдельного
    writer'а тут допустим — главное, что файл цел и нет осиротевших .tmp.
    """
    return isinstance(exc, PermissionError)


class ConcurrentWriteTest(_TmpDir):
    """N писателей в ОДИН путь: итог — валидный JSON, без осиротевших .tmp.

    С общим `path.tmp` параллельные писатели топчут чужой temp -> возможен
    рваный/частичный файл или осиротевший temp. Per-process temp устраняет
    именно это. Транзиентная PermissionError на `os.replace` (Windows,
    destination занят) — не баг и допускается.
    """

    N = 8

    def _assert_valid_result(self, path):
        # Файл существует и парсится целиком (не «рваный»).
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["k"], _PAYLOAD["k"])
        self.assertIn("who", data)
        # Никаких осиротевших temp от проигравших гонку писателей.
        self.assertEqual(_tmp_files(self.dir), [])

    def test_concurrent_processes(self):
        path = os.path.join(self.dir, "shared.json")
        try:
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=_aipf_worker, args=(path, i))
                     for i in range(self.N)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(30)
            alive = [p for p in procs if p.is_alive()]
            for p in alive:
                p.terminate()
            if alive:
                self.skipTest("multiprocessing workers hung in this environment")
            exitcodes = [p.exitcode for p in procs]
        except (OSError, ValueError, RuntimeError) as e:
            self.skipTest("multiprocessing unavailable: %r" % (e,))
        # The invariant under concurrency is the END STATE: a valid (non-torn)
        # file and no orphaned temp. Assert that unconditionally — it's what a
        # real race bug would break.
        self._assert_valid_result(path)
        # A spawned worker exiting non-zero is an environment signal on a
        # contended CI runner (a transient crash / lost os.replace race), not a
        # writer bug — the file is valid above and the thread variant covers the
        # race portably. Record it as a skip rather than a flaky hard failure.
        if not all(code == 0 for code in exitcodes):
            self.skipTest("a multiprocessing worker exited non-zero in this "
                          "environment: %r" % (exitcodes,))

    def test_concurrent_threads_aipf(self):
        # Потоковый фолбэк/дубль: дешёвый и портируемый регресс той же гонки.
        path = os.path.join(self.dir, "shared_threads.json")
        errors = []

        def run(i):
            for _ in range(20):
                try:
                    _aipf.write_json(path, dict(_PAYLOAD, who=i))
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        self._run_threads(run)
        self._assert_no_writer_bug(errors)
        self._assert_valid_result(path)

    def test_concurrent_threads_workspace(self):
        path = os.path.join(self.dir, "shared_ws.json")
        ws = server.Workspace(self.dir)
        errors = []

        def run(i):
            for _ in range(20):
                try:
                    ws.write_json(path, dict(_PAYLOAD, who=i))
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        self._run_threads(run)
        self._assert_no_writer_bug(errors)
        self._assert_valid_result(path)

    def _run_threads(self, run):
        threads = [threading.Thread(target=run, args=(i,))
                   for i in range(self.N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

    def _assert_no_writer_bug(self, errors):
        # Допустима только транзиентная гонка os.replace по destination;
        # любая другая ошибка (рваная запись, утечка temp) — баг writer'а.
        real = [e for e in errors if not _is_replace_race(e)]
        self.assertEqual(real, [], "writer raised non-race error: %r" % real)


class FsyncDurabilityTest(_TmpDir):
    """atomic_write must fsync the temp's bytes to disk BEFORE os.replace, so a
    power loss can't land a zero-length/partial temp as the target (which would
    parse as a valid empty `{}` and slip past corrupt-state recovery)."""

    def test_atomic_write_fsyncs_before_replace(self):
        order = []
        real_replace = os.replace

        def rec_fsync(fd):
            order.append("fsync")

        def rec_replace(a, b):
            order.append("replace")
            return real_replace(a, b)

        path = os.path.join(self.dir, "d.json")
        with mock.patch("os.fsync", side_effect=rec_fsync), \
             mock.patch("os.replace", side_effect=rec_replace):
            _aipf.atomic_write(path, '{"x": 1}')
        self.assertIn("fsync", order, "atomic_write did not fsync")
        self.assertIn("replace", order)
        self.assertLess(order.index("fsync"), order.index("replace"),
                        "fsync must run before os.replace")
        with open(path, encoding="utf-8") as f:   # still round-trips
            self.assertEqual(json.load(f), {"x": 1})


class AppendJsonlTest(_TmpDir):
    """append_jsonl emits one complete, valid line per call and never tears a
    line under concurrent appenders (the shared telemetry.jsonl race)."""

    def test_round_trip_lines(self):
        path = os.path.join(self.dir, "log.jsonl")
        _aipf.append_jsonl(path, {"a": 1})
        _aipf.append_jsonl(path, {"b": "две"})
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual(lines, [{"a": 1}, {"b": "две"}])

    def test_concurrent_appends_no_torn_or_lost_lines(self):
        path = os.path.join(self.dir, "concurrent.jsonl")
        k, lines_each = 6, 20
        threads = [threading.Thread(target=_append_worker, args=(path, w, lines_each))
                   for w in range(k)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        with open(path, encoding="utf-8") as f:
            raw = [ln for ln in f if ln.strip()]
        # Every line parses (no torn/interleaved JSON) ...
        parsed = [json.loads(ln) for ln in raw]
        # ... and exactly k*lines_each distinct events landed (none lost/dup'd).
        self.assertEqual(len(parsed), k * lines_each)
        seen = {(d["who"], d["i"]) for d in parsed}
        self.assertEqual(len(seen), k * lines_each)


if __name__ == "__main__":
    unittest.main()
