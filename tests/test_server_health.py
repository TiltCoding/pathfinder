#!/usr/bin/env python3
"""Offline tests for server liveness / stale-server.json detection (stdlib unittest).

ws1 added a self-reporting `/health` (now `{ok, ts, pid, port}`) plus three pure
helpers next to `write_server_info`:

  * `process_alive(pid)`     — signal-0 liveness probe, conservative on errors;
  * `read_server_info(ws)`   — parse `<base>/server.json` or return None;
  * `server_info_is_stale(info, current_port)` — should this server.json be replaced?

These pin the data behind the stale-detect on startup: a dead pid (or a mismatched
port) in an existing `server.json` is treated as stale and overwritten.

No real socket, no network, no disk outside a tempfile. `/health` is driven
through the real `do_GET` on a capturing stand-in (the `_CapturingHandler`
pattern from tests/test_ask.py), so no port is ever bound.

Run with:
    python3 -m unittest tests.test_server_health
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
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import server     # noqa: E402

# A pid no live process can hold (max 32-bit positive int) — a stable "dead" pid.
_DEAD_PID = 2 ** 31 - 1


class _CapturingHandler:
    """Drives the real `Handler.do_GET` and records the response without a socket.

    `do_GET` reaches the network only via `_json`/`_send`, which call
    `send_response`/`send_header`/`end_headers`/`self.wfile.write` — all stubbed
    here. We reuse the real (unbound) `do_GET`/`_send`/`_json` methods, so the
    `/health` body is produced by the exact production code path. `server_port`
    is mirrored from `Handler.server_port` onto the stand-in (production reads it
    via `getattr(self, "server_port", None)`; in `main()` it lives on the class).
    """

    def __init__(self, path):
        self.path = path
        self.status = None
        self.headers = {}
        self._chunks = []
        # Mirror the class-attribute the real Handler exposes (set in main()).
        self.server_port = server.Handler.server_port

    # --- BaseHTTPRequestHandler surface used by _send ---
    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers[key] = value

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

    # --- bound real handler methods ---
    do_GET = server.Handler.do_GET
    _send = server.Handler._send
    _json = server.Handler._json

    def get(self):
        self.do_GET()
        return self.status, self.body


class HealthEndpointTest(unittest.TestCase):
    """`GET /health` self-reports `pid` and `port` alongside `ok`/`ts`."""

    def setUp(self):
        # Pin a known port on the class; restore afterwards (precedent:
        # `_hub_cache.clear()` cleanup in tests/test_ask.py).
        self._prev_port = server.Handler.server_port
        self.addCleanup(self._restore_port)

    def _restore_port(self):
        server.Handler.server_port = self._prev_port

    def test_health_reports_pid_and_port(self):
        server.Handler.server_port = 8517
        status, body = _CapturingHandler("/health").get()
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["pid"], os.getpid())
        self.assertEqual(data["port"], 8517)
        self.assertIs(data["ok"], True)
        self.assertIn("ts", data)
        self.assertTrue(data["ts"])  # non-empty timestamp string

    def test_health_port_none_without_bound_server(self):
        # Offline call with no real server: server_port unset -> port is null,
        # and the endpoint must still answer rather than crash.
        server.Handler.server_port = None
        status, body = _CapturingHandler("/health").get()
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIsNone(data["port"])
        self.assertEqual(data["pid"], os.getpid())


class ProcessAliveTest(unittest.TestCase):
    """`process_alive` — signal-0 probe, conservative on bad/unknown input."""

    def test_self_is_alive(self):
        self.assertIs(server.process_alive(os.getpid()), True)

    def test_dead_pid_is_not_alive(self):
        self.assertIs(server.process_alive(_DEAD_PID), False)

    def test_zero_pid_is_not_alive(self):
        self.assertIs(server.process_alive(0), False)

    def test_none_pid_is_not_alive(self):
        self.assertIs(server.process_alive(None), False)

    def test_non_numeric_pid_is_not_alive(self):
        self.assertIs(server.process_alive("not-a-pid"), False)


class ServerInfoStaleTest(unittest.TestCase):
    """`server_info_is_stale` — replace info whose pid is dead, whose port
    mismatches, or which carries no pid at all."""

    def test_dead_pid_is_stale(self):
        self.assertIs(
            server.server_info_is_stale({"pid": _DEAD_PID, "port": 8517}),
            True)

    def test_mismatched_port_is_stale(self):
        # Live pid, but a different current_port -> the recorded server is stale.
        info = {"pid": os.getpid(), "port": 8517}
        self.assertIs(server.server_info_is_stale(info, current_port=8518), True)

    def test_live_pid_and_matching_port_is_fresh(self):
        info = {"pid": os.getpid(), "port": 8517}
        self.assertIs(server.server_info_is_stale(info, current_port=8517), False)

    def test_live_pid_without_port_check_is_fresh(self):
        # No current_port given -> only liveness matters.
        info = {"pid": os.getpid(), "port": 8517}
        self.assertIs(server.server_info_is_stale(info), False)

    def test_empty_info_is_stale(self):
        self.assertIs(server.server_info_is_stale({}), True)
        self.assertIs(server.server_info_is_stale(None), True)

    def test_info_without_pid_is_stale(self):
        self.assertIs(server.server_info_is_stale({"port": 8517}), True)


class ReadServerInfoTest(unittest.TestCase):
    """`read_server_info` — parse `<base>/server.json` or return None, never raise."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ws = server.Workspace(self.root)
        self.path = os.path.join(self.ws.base, "server.json")

    def _write_raw(self, text):
        os.makedirs(self.ws.base, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_missing_file_is_none(self):
        self.assertIsNone(server.read_server_info(self.ws))

    def test_corrupt_json_is_none(self):
        self._write_raw("{not valid json")
        self.assertIsNone(server.read_server_info(self.ws))

    def test_non_dict_json_is_none(self):
        # A valid-JSON-but-not-a-dict file must not be mistaken for server info.
        self._write_raw("[1, 2, 3]")
        self.assertIsNone(server.read_server_info(self.ws))

    def test_valid_dict_round_trips(self):
        # Write through the same helper the server uses, then read it back.
        info = server.write_server_info(self.ws, 8517, os.getpid())
        got = server.read_server_info(self.ws)
        self.assertIsInstance(got, dict)
        self.assertEqual(got["port"], 8517)
        self.assertEqual(got["pid"], os.getpid())
        self.assertEqual(got, info)


if __name__ == "__main__":
    unittest.main()
