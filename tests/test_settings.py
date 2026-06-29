#!/usr/bin/env python3
"""Offline tests for the global UI-language setting (ws1/b1) and the i18n
dictionary-completeness invariant (b2/b3). stdlib unittest, no network, no
real ~/.claude/ — tempfile + monkeypatched home only.

Two suites:

  1. Server contract (the `_CapturingHandler` pattern from
     tests/test_server_health.py): `GET /settings.json` defaults to `en` with
     no settings file and reflects what's written; `POST /settings` accepts a
     whitelisted lang (200) and rejects junk (400); and — the R1 regression —
     `POST /settings` carries NO slug yet must still pass (the `/settings`
     branch sits *before* do_POST's slug check). Plus the pure helpers
     `read_lang`/`write_lang` round-trip and normalize via `base=<tmp>`.

  2. Dictionary completeness (like the dark-palette-completeness invariant,
     ADR-0015): the inline `STR = { en:{…}, ru:{…} }` dictionaries in
     templates/dashboard.html and in server.py's HUB_PAGE must carry the
     *same* set of top-level keys in both languages — a missing pair in one
     language is a silent UI gap.

`GET`/`POST` are driven through the real `do_GET`/`do_POST` on a capturing
stand-in, so the production code path produces the response without binding a
socket. Home-dir isolation for the endpoint tests is via
`unittest.mock.patch("os.path.expanduser", …)` because do_GET/do_POST call
`read_lang()`/`write_lang()` with no `base` override — the patched
`expanduser` redirects `settings_path()` into a tempdir.

Run:
    python -m unittest tests.test_settings -v
    python -m unittest discover -s tests   # full suite
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

# Make scripts/ importable whether run from the repo root or as a module
# (defensive sys.path hack, as is customary in this project's tooling).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_REPO, "scripts")

import server  # noqa: E402

_DASHBOARD = os.path.join(_REPO, "templates", "dashboard.html")
_SERVER = os.path.join(_SCRIPTS, "server.py")


class _CapturingHandler:
    """Drives the real `do_GET`/`do_POST` and records the response, no socket.

    Mirrors the stand-in in tests/test_server_health.py: the real handler
    reaches the network only through `_send`/`_json`/`_read_body`, whose
    `send_response`/`send_header`/`end_headers`/`wfile.write` and the
    `headers`/`rfile` request surface are all stubbed here, so the
    `/settings` bodies come from the exact production code path.
    """

    def __init__(self, path, body=None):
        self.path = path
        self.status = None
        # A same-origin loopback Host so do_POST's CSRF/rebinding guard
        # (_origin_allowed) passes, as a real browser request would.
        self.headers = {"Host": "127.0.0.1"}
        self._chunks = []
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            self.headers["Content-Length"] = str(len(raw))
            self.rfile = _FakeRFile(raw)

    # --- BaseHTTPRequestHandler surface used by _send / _read_body ---
    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        pass

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

    def json_body(self):
        return json.loads(self.body.decode("utf-8"))

    # --- bound real handler methods ---
    do_GET = server.Handler.do_GET
    do_POST = server.Handler.do_POST
    _origin_allowed = server.Handler._origin_allowed
    _send = server.Handler._send
    _json = server.Handler._json
    _read_body = server.Handler._read_body

    def get(self):
        self.do_GET()
        return self.status, self.json_body()

    def post(self):
        self.do_POST()
        return self.status, self.json_body()


class _FakeRFile:
    """Minimal rfile for `_read_body`: a single `.read(n)` over a byte buffer."""

    def __init__(self, raw):
        self._raw = raw

    def read(self, n):
        return self._raw[:n]


class _HomeIsolation(unittest.TestCase):
    """Base: redirect `os.path.expanduser('~')` into a throwaway tempdir so the
    real ~/.claude/ is never touched (R5). do_GET/do_POST call the lang helpers
    without a `base` override, so patching `expanduser` is what isolates them."""

    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self._real_expanduser = os.path.expanduser

        def fake_expanduser(path):
            if path == "~" or path.startswith("~" + os.sep) or path.startswith("~/"):
                return self.home + path[1:]
            return self._real_expanduser(path)

        patcher = mock.patch("os.path.expanduser", side_effect=fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _settings_file(self):
        # Resolved under the patched home; equals settings_path() with no base.
        return server.settings_path()


class LangHelpersTest(_HomeIsolation):
    """Pure `read_lang`/`write_lang` with an explicit `base` (test isolation)."""

    def test_default_when_no_file(self):
        self.assertEqual(server.read_lang(base=self.home), "en")

    def test_write_then_read_round_trips(self):
        self.assertEqual(server.write_lang("ru", base=self.home), "ru")
        self.assertEqual(server.read_lang(base=self.home), "ru")

    def test_invalid_value_normalizes_to_default(self):
        # An unknown stored value degrades to the default on read.
        path = server.settings_path(base=self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            json.dump({"lang": "de"}, f)
        self.assertEqual(server.read_lang(base=self.home), "en")

    def test_write_invalid_returns_default_and_no_persist(self):
        self.assertEqual(server.write_lang("de", base=self.home), "en")
        # An invalid write must not create/poison the file -> still default.
        self.assertEqual(server.read_lang(base=self.home), "en")


class SettingsGetTest(_HomeIsolation):
    """`GET /settings.json` — defaults to en, reflects a written value."""

    def test_default_en_without_file(self):
        status, data = _CapturingHandler("/settings.json").get()
        self.assertEqual(status, 200)
        self.assertEqual(data, {"lang": "en"})

    def test_reflects_written_lang(self):
        server.write_lang("ru", base=self.home)  # writes under patched home
        status, data = _CapturingHandler("/settings.json").get()
        self.assertEqual(status, 200)
        self.assertEqual(data["lang"], "ru")


class SettingsPostTest(_HomeIsolation):
    """`POST /settings` — whitelist accept (200 + persisted), reject junk (400),
    and the R1 regression: NO slug yet must not 400 on the slug guard."""

    def test_valid_lang_ok_and_persisted(self):
        h = _CapturingHandler("/settings", body={"lang": "ru"})
        status, data = h.post()
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ok": True, "lang": "ru"})
        # Persisted under the patched home, readable back.
        self.assertEqual(server.read_lang(base=self.home), "ru")
        self.assertTrue(os.path.exists(self._settings_file()))

    def test_unknown_lang_rejected(self):
        status, data = _CapturingHandler("/settings", body={"lang": "de"}).post()
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_garbage_body_rejected(self):
        status, data = _CapturingHandler(
            "/settings", body={"nonsense": 1}).post()
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_post_without_slug_does_not_400_on_slug_guard(self):
        # R1 regression: /settings has no slug; the branch must run *before*
        # do_POST's "missing or invalid slug" guard. A valid lang -> 200, and
        # crucially the error is never the slug message.
        status, data = _CapturingHandler("/settings", body={"lang": "en"}).post()
        self.assertEqual(status, 200)
        self.assertNotIn("slug", json.dumps(data))

    def test_post_invalid_lang_errors_on_lang_not_slug(self):
        # Even the rejection path is the lang guard, not the slug guard.
        status, data = _CapturingHandler("/settings", body={"lang": "de"}).post()
        self.assertEqual(status, 400)
        self.assertNotIn("slug", json.dumps(data))


def _extract_str_keys(text, *, source):
    r"""Extract top-level key sets of `STR.en` and `STR.ru` from inline JS.

    Pragmatic, deliberately not a JS parser. Assumptions (true of both
    dictionaries today — templates/dashboard.html and server.py's HUB_PAGE):

      * the dictionary opens with a `const STR = {` line, then a `en: {` line,
        then a `ru: {` line, and the whole object closes with a line whose
        first non-space char is `}` (the `};` after the `ru` block);
      * each section is a flat map of `"dotted.key": "value"` entries, one key
        declaration per line — so keys are the line-leading `"([\w.]+)"\s*:`
        matches between the `en:`/`ru:` markers (en) and between `ru:` and the
        block close (ru).

    Returns `(en_keys, ru_keys)` as sets. Raises AssertionError with `source`
    in the message if the expected markers aren't found (so a structural change
    surfaces as a clear test failure, not a silent empty set).
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"\s*const STR\s*=\s*\{", ln)), None)
    assert start is not None, f"{source}: `const STR = {{` block not found"

    en_at = ru_at = end_at = None
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if en_at is None and re.match(r"\s*en:\s*\{", ln):
            en_at = i
        elif ru_at is None and re.match(r"\s*ru:\s*\{", ln):
            ru_at = i
        elif ru_at is not None and re.match(r"\s*\};", ln):
            end_at = i
            break
    assert en_at is not None, f"{source}: STR.en section not found"
    assert ru_at is not None, f"{source}: STR.ru section not found"
    assert end_at is not None, f"{source}: STR closing `}};` not found"

    key_re = re.compile(r'^\s*"([\w.]+)"\s*:')

    def keys(lo, hi):
        out = set()
        for ln in lines[lo + 1:hi]:
            m = key_re.match(ln)
            if m:
                out.add(m.group(1))
        return out

    return keys(en_at, ru_at), keys(ru_at, end_at)


def _extract_str_map(text, *, source):
    """Like `_extract_str_keys` but returns `(en_map, ru_map)` of key -> value.

    Same pragmatic assumptions (flat `"dotted.key": "value"`, one per line); the
    value is the first double-quoted string literal after the key's colon
    (escaped chars handled), so a trailing `// comment` or `,` is ignored. Lines
    whose value isn't a simple double-quoted literal are skipped (only used for
    the cross-file value comparison, which intersects on keys present in both)."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"\s*const STR\s*=\s*\{", ln)), None)
    assert start is not None, f"{source}: `const STR = {{` block not found"

    en_at = ru_at = end_at = None
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if en_at is None and re.match(r"\s*en:\s*\{", ln):
            en_at = i
        elif ru_at is None and re.match(r"\s*ru:\s*\{", ln):
            ru_at = i
        elif ru_at is not None and re.match(r"\s*\};", ln):
            end_at = i
            break
    assert en_at is not None, f"{source}: STR.en section not found"
    assert ru_at is not None, f"{source}: STR.ru section not found"
    assert end_at is not None, f"{source}: STR closing `}};` not found"

    kv_re = re.compile(r'^\s*"([\w.]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')

    def kv(lo, hi):
        out = {}
        for ln in lines[lo + 1:hi]:
            m = kv_re.match(ln)
            if m:
                out[m.group(1)] = m.group(2)
        return out

    return kv(en_at, ru_at), kv(ru_at, end_at)


class DictionaryCompletenessTest(unittest.TestCase):
    """en/ru key sets must match in both inline `STR` dictionaries (ADR-0015
    completeness invariant): a key present in one language but not the other is
    a silent localization gap."""

    def _assert_parity(self, en, ru, label):
        self.assertTrue(en, f"{label}: extracted no en keys — extractor stale?")
        self.assertTrue(ru, f"{label}: extracted no ru keys — extractor stale?")
        only_en = sorted(en - ru)
        only_ru = sorted(ru - en)
        self.assertEqual(
            (only_en, only_ru), ([], []),
            f"{label}: STR.en/STR.ru key sets diverge — "
            f"en-only={only_en} ru-only={only_ru}")

    def test_dashboard_dictionary_complete(self):
        with open(_DASHBOARD, "r", encoding="utf-8") as f:
            text = f.read()
        en, ru = _extract_str_keys(text, source="dashboard.html")
        self._assert_parity(en, ru, "dashboard.html STR")

    def test_hub_dictionary_complete(self):
        with open(_SERVER, "r", encoding="utf-8") as f:
            text = f.read()
        en, ru = _extract_str_keys(text, source="server.py HUB_PAGE")
        self._assert_parity(en, ru, "HUB_PAGE STR")


class CrossFileStrParityTest(unittest.TestCase):
    """A STR key shared by dashboard.html and HUB_PAGE must carry the SAME value
    in both (per language). The within-file completeness test catches an en/ru
    gap inside one file but never compares the two copies — so a shared key whose
    text was edited in one file and not the other (the documented drift risk in
    dashboard-i18n.md) would slip through. This closes that gap."""

    def _maps(self):
        with open(_DASHBOARD, "r", encoding="utf-8") as f:
            d_en, d_ru = _extract_str_map(f.read(), source="dashboard.html")
        with open(_SERVER, "r", encoding="utf-8") as f:
            h_en, h_ru = _extract_str_map(f.read(), source="server.py HUB_PAGE")
        return d_en, d_ru, h_en, h_ru

    def test_shared_keys_have_matching_values(self):
        d_en, d_ru, h_en, h_ru = self._maps()
        self.assertTrue(d_en and h_en,
                        "extractor stale — no values extracted")
        for lang, dmap, hmap in (("en", d_en, h_en), ("ru", d_ru, h_ru)):
            shared = set(dmap) & set(hmap)
            self.assertTrue(
                shared,
                f"{lang}: no shared STR keys between dashboard.html and "
                f"HUB_PAGE — extractor stale?")
            mismatches = {k: (dmap[k], hmap[k])
                          for k in sorted(shared) if dmap[k] != hmap[k]}
            self.assertEqual(
                mismatches, {},
                f"{lang}: shared STR keys differ between dashboard.html and "
                f"HUB_PAGE (drift): {mismatches}")


if __name__ == "__main__":
    unittest.main()
