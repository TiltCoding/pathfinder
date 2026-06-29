#!/usr/bin/env python3
"""Companion feedback server for the ai-pathfinder plugin.

Stdlib only (no third-party deps). One server per project root. It serves the
per-task HTML dashboard and provides a tiny JSON API so a human can:

  - accumulate comments on plan blocks and answers to questions (a *draft batch*),
  - send the whole batch to the agent at once ("Send to agent for revision"),
  - approve the plan ("Approve plan").

The agent never polls during active work. When it reaches a checkpoint it parks
on the long-poll /wait endpoint (a background curl that blocks until a submission
or signal lands and returns instantly, so the harness re-invokes the agent the
moment the human clicks). It then reads the batch, revises, writes replies.json,
and continues. A long ScheduleWakeup remains only as a fallback.

Workspace layout (under <root>/.workflow/):

    server.json                      # {port, pid, url} written on startup
    tasks/<slug>/
        index.html                   # copy of the dashboard template
        dashboard.json               # render model (written by the agent)
        state.json                   # workflow state (written by the agent)
        draft.json                   # current accumulating batch (written by server)
        submissions/<n>.json         # finalized batches (written by server)
        submit.flag                  # {"latest": <n>, "ts": ...} (written by server)
        replies.json                 # agent answers to the human (written by agent)
        signals.json                 # append-only log of signals (written by server)

Usage:
    python3 server.py [--root PATH] [--port N] [--open SLUG] [--no-browser]
"""

import argparse
import atexit
import base64
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _aipf  # noqa: E402  (shared helpers: layout, Langfuse forwarding)

SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DEFAULT_PORT = 8473
PORT_SCAN = 25
HEARTBEAT_SECS = 5        # how often a live server refreshes server.json's ts
SERVER_STALE_SECS = 30    # server.json whose heartbeat stopped this long ago = corpse

# A task is "active" on the hub when its phase is non-terminal AND it was
# updated within this window; otherwise it falls into history (see _build_hub).
HUB_ACTIVE_WINDOW_SEC = 24 * 3600
HUB_TERMINAL_PHASES = {"DONE", "ABORTED"}

# Files inside a task dir that the browser is allowed to GET.
READABLE_FILES = {"index.html", "dashboard.json", "state.json", "replies.json",
                  "reviews.json"}

# Visual-demo mockup files (self-contained HTML/SVG) served from <task>/mockups/.
MOCKUP_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\.(html|svg)$")

# Security headers for /mockup only — the one path that runs untrusted active
# content (agent mockups in a sandbox="allow-scripts" iframe). Defence-in-depth
# on top of the iframe sandbox + traversal guards. The CSP allows *inline*
# (script/style + data: images/fonts) so the existing inline-<script> mockups
# keep working, but blocks the *network/external* (default-src 'none' ⇒ no
# connect/fetch/ws/external src), base and form-action.
MOCKUP_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
              "img-src data:; font-src data:; base-uri 'none'; form-action 'none'; "
              "frame-ancestors 'self'")
MOCKUP_SEC_HEADERS = {"X-Content-Type-Options": "nosniff",
                      "Content-Security-Policy": MOCKUP_CSP}

# Artifacts panel (feat-17): browsable, read-only agent deliverables served from
# <task>/mockups/ (demos) and <task>/artifacts/ (other outputs). Same name
# allowlist + realpath/commonpath confinement as _serve_mockup. ACTIVE content
# (html/svg) is served with the mockup sandbox CSP and rendered ONLY inside a
# sandbox="allow-scripts" iframe (never innerHTML) — the cand-6 security invariant.
ARTIFACT_DIRS = ("mockups", "artifacts")
ARTIFACT_RE = re.compile(
    r"^[A-Za-z0-9._-]{1,96}\.(html|svg|md|json|txt|csv|patch|diff|png|jpe?g|gif|webp)$")
# extension -> (kind, mime, active?). `active` files run script in the sandbox iframe.
ARTIFACT_KIND = {
    "html": ("html", "text/html; charset=utf-8", True),
    "svg":  ("svg",  "image/svg+xml; charset=utf-8", True),
    "md":   ("doc",  "text/markdown; charset=utf-8", False),
    "txt":  ("doc",  "text/plain; charset=utf-8", False),
    "json": ("doc",  "application/json; charset=utf-8", False),
    "csv":  ("doc",  "text/csv; charset=utf-8", False),
    "patch": ("diff", "text/plain; charset=utf-8", False),
    "diff": ("diff", "text/plain; charset=utf-8", False),
    "png":  ("image", "image/png", False),
    "jpg":  ("image", "image/jpeg", False),
    "jpeg": ("image", "image/jpeg", False),
    "gif":  ("image", "image/gif", False),
    "webp": ("image", "image/webp", False),
}
_ARTIFACT_VER_RE = re.compile(r"^(.*)\.v(\d+)\.[A-Za-z0-9]+$")


def _artifact_base_version(name):
    """Split `<base>.v<N>.<ext>` into (base, N) for version grouping; otherwise
    (name-without-extension, None). Pairs with the versioning convention (feat-20)."""
    m = _ARTIFACT_VER_RE.match(name)
    if m:
        return m.group(1), int(m.group(2))
    return (name.rsplit(".", 1)[0] if "." in name else name), None

# Image attachments served from <task>/attachments/. Server-generated safe name
# (att-<8 hex>.<ext>); the original client filename is carried as metadata only.
# SVG is excluded on purpose (active content — would need the /mockup CSP path).
ATTACH_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}\.(png|jpe?g|gif|webp)$")
ATTACH_MAX_BYTES = 5 * 1024 * 1024     # 5 MB per image
# Global upper bound on ANY POST body, enforced in _read_body before the body is
# read into memory (a forged/huge Content-Length must not OOM the process). Set
# above the base64-inflated attach max (~6.7 MB for a 5 MB image) so legitimate
# uploads still pass; /attach keeps its own tighter decoded-size cap.
MAX_BODY_BYTES = 8 * 1024 * 1024
ATTACH_MAX_PER_MSG = 6                  # max images carried on one chat message
# Allow-listed MIME -> saved extension. jpg/jpeg both map to image/jpeg; the
# saved extension for jpeg is "jpg" (matches ATTACH_RE's jpe?g alternative).
ATTACH_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg",
                   "image/gif": "gif", "image/webp": "webp"}
# Reverse map (extension -> content-type) for the serve route.
ATTACH_EXT_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                   "gif": "image/gif", "webp": "image/webp"}


def image_magic_ok(ext, data):
    """True if `data` begins with a signature matching the saved `ext`.

    Defence in depth: /attach trusts the client-declared mime, but the bytes are
    client-controlled. Verify they actually are the claimed image type before
    writing them under att-*.<ext> (the orchestrator later Reads them as images),
    so arbitrary content can't be stashed under an image name."""
    if ext == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == "jpg":
        return data[:3] == b"\xff\xd8\xff"
    if ext == "gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def safe_slug(slug):
    if not slug or not SLUG_RE.match(slug):
        return None
    if slug in (".", ".."):
        return None
    return slug


class Workspace:
    """Filesystem helper rooted at <root>/.workflow."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.base = os.path.join(self.root, ".workflow")
        self.tasks = os.path.join(self.base, "tasks")
        self._locks = {}
        self._locks_guard = threading.Lock()

    def lock(self, slug):
        with self._locks_guard:
            if slug not in self._locks:
                self._locks[slug] = threading.Lock()
            return self._locks[slug]

    def task_dir(self, slug):
        return os.path.join(self.tasks, slug)

    def task_file(self, slug, name):
        return os.path.join(self.task_dir(slug), name)

    def ensure_task(self, slug):
        d = self.task_dir(slug)
        os.makedirs(os.path.join(d, "submissions"), exist_ok=True)
        return d

    def read_json(self, path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Per-process temp + retrying replace, shared with _aipf so all store
        # writers never drift (see _aipf.atomic_temp_name/atomic_replace; write_lang
        # and _attach use the same helpers): parallel runs share one store, so a
        # fixed ".tmp" would let concurrent writers collide on the same target.
        tmp = _aipf.atomic_temp_name(path)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())   # durable before replace (see _aipf.atomic_write)
            _aipf.atomic_replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


# ---- global language setting (~/.claude/ai-pathfinder/settings.json) -----
LANGS = ("en", "ru")
DEFAULT_LANG = "en"


def settings_path(base=None):
    """Path to the global plugin settings file.

    Mirrors _aipf._projects_dir: ~/.claude/... by convention; `base`
    overrides the home root for test isolation (so tests never touch the
    real ~/.claude/).
    """
    return os.path.join(base or os.path.expanduser("~"),
                        ".claude", "ai-pathfinder", "settings.json")


def read_lang(base=None):
    """Read the global UI language, normalized to the whitelist.

    Graceful: any error / missing file / unknown value -> DEFAULT_LANG.
    """
    try:
        with open(settings_path(base), "r", encoding="utf-8") as f:
            data = json.load(f)
        lang = data.get("lang")
        if lang in LANGS:
            return lang
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_LANG


def write_lang(lang, base=None):
    """Persist the global UI language (atomic, best-effort).

    Validates against the whitelist; returns the written value or
    DEFAULT_LANG if `lang` is invalid / the write fails.
    """
    if lang not in LANGS:
        return DEFAULT_LANG
    path = settings_path(base)
    # Shared per-process temp + retrying replace (the same helpers as
    # Workspace.write_json): the settings file is global (one per machine), so
    # servers of different project roots may write it concurrently. A pid+uuid
    # temp keeps each writer isolated, and atomic_replace rides out the
    # Windows-only transient PermissionError instead of silently losing the write.
    tmp = _aipf.atomic_temp_name(path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"lang": lang, "ts": now_iso()}, f,
                      ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())   # durable before replace (see _aipf.atomic_write)
        _aipf.atomic_replace(tmp, path)
        return lang
    except OSError:
        # Persist failed (read-only home, disk full, lock). Don't claim success:
        # report the value actually on disk. Clean up any orphaned temp.
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return read_lang(base)


class Handler(BaseHTTPRequestHandler):
    workspace = None  # set on the server instance class
    server_port = None            # real bound port, set in main() after bind()
    _trace_cache = {}             # slug -> {mt, exp, data}
    _trace_lock = threading.Lock()
    _wakers = {}                  # slug -> threading.Condition (long-poll /wait)
    _wakers_guard = threading.Lock()

    @classmethod
    def _waker(cls, slug):
        """Per-slug Condition used by /wait to block until a submit/signal lands."""
        with cls._wakers_guard:
            c = cls._wakers.get(slug)
            if c is None:
                c = threading.Condition()
                cls._wakers[slug] = c
            return c

    # ---- helpers -------------------------------------------------------
    def _send(self, code, body=b"", content_type="application/json; charset=utf-8",
              extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        eh = extra_headers or {}
        if "Cache-Control" not in eh:   # callers may opt into no-cache for ETag revalidation
            self.send_header("Cache-Control", "no-store")
        # security headers (feat-11). Referrer-Policy on every response so a local
        # slug/path never leaks to an external host; anti-clickjacking on HTML only
        # (frame-ancestors 'self' + legacy X-Frame-Options) so no external site can
        # frame the dashboard/hub and click submit/approve. A caller that set its own
        # CSP (the mockup's sandbox CSP) keeps it — it carries its own frame-ancestors.
        if "Referrer-Policy" not in eh:
            self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            if "X-Frame-Options" not in eh:
                self.send_header("X-Frame-Options", "SAMEORIGIN")
            if "Content-Security-Policy" not in eh:
                self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        for k, v in eh.items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        """Read+parse a POST body, or send an error and return None.

        Returns a dict on success ({} for an empty or genuinely malformed body, so
        callers stay graceful). Returns None when it has already sent an error
        response — an oversized body (413) or one that arrived truncated (400) —
        so the caller must stop. This is the single body cap for every POST route.
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        if length > MAX_BODY_BYTES:
            # Reject before allocating: don't let a forged/huge Content-Length read
            # gigabytes into RAM. Close the connection so the unread body can't
            # corrupt a reused socket (defensive — default is HTTP/1.0 close anyway).
            self.close_connection = True
            self._json(413, {"ok": False, "error": "too large"})
            return None
        # rfile.read(length) can return short on a slow/aborted client; loop until
        # the full body is in (bounded by MAX_BODY_BYTES above) so a partial read is
        # not silently passed off as a valid {} body.
        chunks, remaining = [], length
        while remaining > 0:
            chunk = self.rfile.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) < length:
            self._json(400, {"ok": False, "error": "incomplete body"})
            return None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}   # genuinely malformed JSON stays graceful (callers see {})
        # All POST routes treat the body as an object (body.get(...)); a non-object
        # JSON body (list/str/number) would raise AttributeError → 500. Normalize.
        return parsed if isinstance(parsed, dict) else {}

    def log_message(self, *args):
        pass  # keep the agent's terminal clean

    # ---- routing -------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        slug = safe_slug((qs.get("slug") or [""])[0])

        if path in ("/", "/index.html"):
            if not slug:
                tasks = self._list_tasks()
                if len(tasks) == 1:  # convenience: open the only task directly
                    return self._redirect(f"/?slug={tasks[0]}")
                return self._send(200, self._landing(tasks).encode("utf-8"),
                                  "text/html; charset=utf-8")
            return self._serve_task_file(slug, "index.html",
                                         "text/html; charset=utf-8")
        if path == "/health":
            ws = getattr(self, "workspace", None)
            return self._json(200, {"ok": True, "ts": now_iso(),
                                    "pid": os.getpid(),
                                    "port": getattr(self, "server_port", None),
                                    "root": os.path.realpath(ws.root) if ws else None,
                                    # source = path of THIS running server.py, so a
                                    # reader (e.g. preview.py) can detect a server
                                    # bound from a stale plugin-cache copy vs the repo.
                                    "source": os.path.realpath(__file__)})
        if path == "/data":
            return self._serve_task_file(slug, "dashboard.json")
        if path == "/mockup":
            return self._serve_mockup(slug, (qs.get("file") or [""])[0])
        if path == "/image":
            return self._serve_image(slug, (qs.get("file") or [""])[0])
        if path == "/artifacts":                       # browsable artifact listing (feat-17)
            return self._json(200, self._artifacts_list(slug))
        if path == "/artifact":                        # one artifact (confined serve / download)
            return self._serve_artifact(
                slug, (qs.get("file") or [""])[0],
                download=((qs.get("download") or [""])[0] == "1"))
        if path == "/trace":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            return self._json(200, self._trace(slug))
        if path == "/trace/feed":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            try:
                since = int((qs.get("since") or ["0"])[0])
            except (ValueError, TypeError):
                since = 0
            return self._json(200, self._trace_feed(slug, since))
        if path == "/trace/messages":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            agent = (qs.get("agent") or [""])[0]
            session = (qs.get("session") or [""])[0]
            return self._json(200, self._trace_messages(slug, agent, session))
        if path == "/trace/actions":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            agent = (qs.get("agent") or [""])[0]
            session = (qs.get("session") or [""])[0]
            return self._json(200, self._trace_actions(slug, agent, session))
        if path == "/changes":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            f = (qs.get("file") or [""])[0]
            if f:
                return self._json(200, self._changes_file(slug, f))
            return self._json(200, self._changes(slug))
        if path == "/reviews":
            return self._serve_task_file(slug, "reviews.json")
        if path == "/chat":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            # Incremental tail (feat-10): with ?since=<offset> return only the
            # lines past it (O(new bytes)) — no full reparse on a changed tick,
            # and an empty-but-cheap response when nothing new. No ETag needed
            # (the tail read is already cheap even when empty).
            try:
                since = int((qs.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            if since > 0:
                body = json.dumps(self._chat_get(slug, since),
                                  ensure_ascii=False).encode("utf-8")
                return self._send(200, body,
                                  extra_headers={"Cache-Control": "no-cache"})
            # Full read (since=0, back-compat): conditional GET keyed on
            # chat.jsonl (mtime,size) — an unchanged log returns a bodiless 304.
            cpath = self.workspace.task_file(slug, "chat.jsonl")
            etag = self._file_etag(cpath)
            headers = {"Cache-Control": "no-cache"}
            if etag:
                headers["ETag"] = etag
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304, b"", extra_headers=headers)
            body = json.dumps(self._chat_get(slug),
                              ensure_ascii=False).encode("utf-8")
            return self._send(200, body, extra_headers=headers)
        if path == "/knowledge":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            f = (qs.get("file") or [""])[0]
            if f:
                return self._json(200, self._knowledge_file(f))
            return self._json(200, self._knowledge(slug))
        if path == "/wait":
            return self._wait(slug, qs)
        if path == "/state":
            return self._serve_task_file(slug, "state.json")
        if path == "/replies":
            return self._serve_task_file(slug, "replies.json")
        if path == "/draft":
            if not slug:
                return self._json(400, {"error": "missing slug"})
            ws = self.workspace
            data = ws.read_json(ws.task_file(slug, "draft.json"), {"items": []})
            return self._json(200, data)
        if path == "/hub.json":           # cross-task aggregate (no slug)
            return self._json(200, self._hub())
        if path == "/hub":                # the hub page (no slug)
            return self._send(200, HUB_PAGE.encode("utf-8"),
                              "text/html; charset=utf-8")
        if path == "/queue.json":         # /improve dispatch queue (no slug)
            return self._json(200, self._queue())
        if path == "/settings.json":      # global UI language (no slug)
            # Value-based conditional GET (feat-10): the body is fully determined
            # by `lang`, so a weak ETag lets the 3s poll take a bodiless 304
            # instead of re-shipping {"lang":...} every time.
            lang = read_lang()
            etag = 'W/"lang-%s"' % lang
            headers = {"Cache-Control": "no-cache", "ETag": etag}
            if self.headers.get("If-None-Match") == etag:
                return self._send(304, b"", extra_headers=headers)
            return self._send(200, json.dumps({"lang": lang}).encode("utf-8"),
                              extra_headers=headers)
        return self._json(404, {"error": "not found"})

    def _origin_allowed(self):
        """CSRF / DNS-rebinding defence for state-changing POSTs. The server
        listens only on 127.0.0.1, so a request whose Host names anything other
        than our loopback address reached us via a DNS-rebinding trick (a
        foreign domain re-pointed at 127.0.0.1) and is refused; likewise a
        cross-site request carries a foreign Origin. We require the Host to be
        loopback (and our port, when the header carries one) and any Origin to
        resolve to the same loopback origin. Without this, a page the user is
        merely browsing could POST to http://127.0.0.1:<port>/submit (approve a
        plan), /chat (inject a message) or /attach (write a file) — the port is
        derived from sha1(root), so it is guessable."""
        try:
            port = self.server.server_address[1]
        except AttributeError:
            port = None
        host = self.headers.get("Host") or ""
        hname, _sep, hport = host.partition(":")
        if hname not in ("127.0.0.1", "localhost"):
            return False
        if hport and port is not None and hport != str(port):
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = urlparse(origin)
            if o.hostname not in ("127.0.0.1", "localhost"):
                return False
            if o.port is not None and port is not None and o.port != port:
                return False
        return True

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._origin_allowed():
            return self._json(403, {"error": "forbidden"})
        body = self._read_body()
        if body is None:
            return   # oversized/incomplete body — _read_body already sent 413/400
        if path == "/settings":           # global UI language (no slug)
            lang = body.get("lang")
            if lang not in LANGS:
                return self._json(400, {"error": "invalid lang"})
            return self._json(200, {"ok": True, "lang": write_lang(lang)})
        slug = safe_slug(body.get("slug", ""))
        if not slug:
            return self._json(400, {"error": "missing or invalid slug"})

        if path == "/draft":
            return self._draft_add(slug, body)
        if path == "/draft/remove":
            return self._draft_remove(slug, body)
        if path == "/submit":
            return self._submit(slug)
        if path == "/signal":
            return self._signal(slug, body)
        if path == "/chat":
            return self._chat_post(slug, body)
        if path == "/attach":
            return self._attach(slug, body)
        if path == "/telemetry":
            return self._telemetry(slug, body)
        return self._json(404, {"error": "not found"})

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _list_tasks(self):
        try:
            return sorted(d for d in os.listdir(self.workspace.tasks)
                          if os.path.isdir(self.workspace.task_dir(d)) and safe_slug(d))
        except FileNotFoundError:
            return []

    def _landing(self, tasks):
        items = "".join(
            f'<li><a href="/?slug={t}">{t}</a></li>' for t in tasks) or "<li>no tasks yet</li>"
        return INDEX_LANDING.replace("<!--TASKS-->", f"<ul>{items}</ul>")

    # ---- file serving --------------------------------------------------
    def _file_etag(self, path):
        """Weak validator for a file's current content: W/"<mtime_ns>-<size>".
        None when the file can't be stat'd (treated as having no ETag)."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        return 'W/"%d-%d"' % (st.st_mtime_ns, st.st_size)

    def _serve_task_file(self, slug, name, content_type="application/json; charset=utf-8"):
        if not slug or name not in READABLE_FILES:
            return self._json(404, {"error": "not found"})
        path = self.workspace.task_file(slug, name)
        if not os.path.isfile(path):
            if name.endswith(".json"):
                return self._json(200, {})
            return self._json(404, {"error": "not found"})
        # Conditional GET: the 3s/5s pollers re-fetch /data, /replies unchanged most
        # of the time. A weak (mtime,size) ETag + no-cache lets the browser
        # revalidate and take a bodiless 304 instead of re-reading + re-shipping the
        # file. The browser transparently serves the cached body to JS, so the
        # front-end sees an unchanged 200 — no client change needed.
        etag = self._file_etag(path)
        headers = {"Cache-Control": "no-cache"}
        if etag:
            headers["ETag"] = etag
            if self.headers.get("If-None-Match") == etag:
                return self._send(304, b"", content_type, extra_headers=headers)
        with open(path, "rb") as f:
            data = f.read()
        return self._send(200, data, content_type, extra_headers=headers)

    def _serve_mockup(self, slug, name):
        """Serve a self-contained visual-demo file from <task>/mockups/.
        Read-only; rendered inside a sandboxed iframe by the dashboard."""
        if not slug or not name or not MOCKUP_RE.match(name):
            return self._json(404, {"error": "not found"})
        mockups = os.path.join(self.workspace.task_dir(slug), "mockups")
        # confine to the mockups dir (defence in depth against traversal)
        path = _aipf.confined_path(mockups, name)
        if path is None:
            return self._json(404, {"error": "not found"})
        if not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        ctype = ("image/svg+xml; charset=utf-8" if name.endswith(".svg")
                 else "text/html; charset=utf-8")
        with open(path, "rb") as f:
            data = f.read()
        return self._send(200, data, ctype, extra_headers=MOCKUP_SEC_HEADERS)

    def _serve_image(self, slug, name):
        """Serve a saved image attachment from <task>/attachments/.
        Read-only; mirrors _serve_mockup's traversal guard. Plain images are not
        active content, so no CSP — just nosniff (and SVG is excluded by the regex)."""
        if not slug or not name or not ATTACH_RE.match(name):
            return self._json(404, {"error": "not found"})
        attach_dir = os.path.join(self.workspace.task_dir(slug), "attachments")
        # confine to the attachments dir (defence in depth against traversal)
        path = _aipf.confined_path(attach_dir, name)
        if path is None:
            return self._json(404, {"error": "not found"})
        if not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        ext = name.rsplit(".", 1)[1].lower()
        ctype = ATTACH_EXT_MIME.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        return self._send(200, data, ctype,
                          extra_headers={"X-Content-Type-Options": "nosniff"})

    def _artifacts_list(self, slug):
        """List browsable artifacts (read-only) from <task>/mockups/ + /artifacts/.
        Metadata only — bytes come from /artifact. No traversal: only files matching
        ARTIFACT_RE are listed; grouped client-side by `base` (feat-17)."""
        out = []
        if not slug:
            return {"artifacts": []}
        for d in ARTIFACT_DIRS:
            adir = os.path.join(self.workspace.task_dir(slug), d)
            try:
                names = sorted(os.listdir(adir))
            except OSError:
                continue
            for name in names:
                if not ARTIFACT_RE.match(name):
                    continue
                fp = os.path.join(adir, name)
                if not os.path.isfile(fp):
                    continue
                ext = name.rsplit(".", 1)[1].lower()
                kind, _mime, active = ARTIFACT_KIND.get(
                    ext, ("file", "application/octet-stream", False))
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                base, ver = _artifact_base_version(name)
                out.append({"name": name, "dir": d, "kind": kind, "active": active,
                            "size": st.st_size, "mtime": int(st.st_mtime),
                            "base": base, "version": ver})
        # group by base, newest version first inside a group
        out.sort(key=lambda a: (a["base"], -(a["version"] or 0), a["name"]))
        return {"artifacts": out}

    def _serve_artifact(self, slug, name, download=False):
        """Serve one artifact confined to <task>/mockups/ or /artifacts/, mirroring
        _serve_mockup's realpath/commonpath traversal guard. Active content (html/svg)
        carries the mockup sandbox CSP (rendered only inside a sandbox iframe).
        download=True forces a Content-Disposition attachment (browser saves it)."""
        if not slug or not name or not ARTIFACT_RE.match(name):
            return self._json(404, {"error": "not found"})
        ext = name.rsplit(".", 1)[1].lower()
        _kind, mime, active = ARTIFACT_KIND.get(
            ext, ("file", "application/octet-stream", False))
        for d in ARTIFACT_DIRS:
            path = _aipf.confined_path(os.path.join(self.workspace.task_dir(slug), d), name)
            if path is None:
                continue
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as f:
                data = f.read()
            if download:
                return self._send(200, data, "application/octet-stream", extra_headers={
                    "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": 'attachment; filename="%s"' % name})
            if active:   # html/svg — confined sandbox CSP, iframe-only on the client
                return self._send(200, data, mime, extra_headers=MOCKUP_SEC_HEADERS)
            return self._send(200, data, mime,
                              extra_headers={"X-Content-Type-Options": "nosniff"})
        return self._json(404, {"error": "not found"})

    # ---- trace (computed, not a file) ----------------------------------
    def _trace(self, slug):
        """Build the trace render model with a short mtime-keyed cache so the
        dashboard can poll without re-parsing megabyte transcripts each time."""
        ws = self.workspace
        tpath = ws.task_file(slug, "telemetry.jsonl")
        try:
            st = os.stat(tpath)
            mt = (int(st.st_mtime), st.st_size)   # (mtime,size) signature (feat-18)
        except OSError:
            mt = (0, 0)
        now = time.time()
        with Handler._trace_lock:
            cached = Handler._trace_cache.get(slug)
            if cached and cached["mt"] == mt and now < cached["exp"]:
                return cached["data"]
        try:
            data = _aipf.build_trace(ws.root, slug)
        except Exception as e:  # never break the page
            data = {"slug": slug, "agents": [], "sessions": [], "totals": {},
                    "error": str(e)}
        with Handler._trace_lock:
            Handler._trace_cache[slug] = {"mt": mt, "exp": now + 3, "data": data}
        return data

    # ---- live action feed (incremental, offset cursor) -----------------
    _feed_cache = {}              # slug -> {since, exp, data}
    _feed_lock = threading.Lock()

    # ---- lazy per-agent actions (transcript-derived) -------------------
    _actions_cache = {}           # (slug, agent) -> {mt, exp, data}
    _actions_lock = threading.Lock()

    def _trace_feed(self, slug, since):
        """Incremental action feed for the trace tab. Reads only the tail of
        telemetry.jsonl past `since` (byte offset) and returns delta tool.*
        events the client stitches into spans. Short (<=1s) cache keyed on the
        (slug, since) pair so a burst of polls doesn't re-stat the file; the
        feed must stay fresh, so it is far shorter than /trace's 3s cache."""
        now = time.time()
        with Handler._feed_lock:
            cached = Handler._feed_cache.get(slug)
            if cached and cached["since"] == since and now < cached["exp"]:
                return cached["data"]
        try:
            data = _aipf.build_feed(self.workspace.root, slug, since)
        except Exception as e:  # never break the page
            data = {"events": [], "nextOffset": since, "error": str(e)}
        with Handler._feed_lock:
            Handler._feed_cache[slug] = {"since": since, "exp": now + 1,
                                         "data": data}
        return data

    def _trace_messages(self, slug, agent, session=""):
        """Lazily load one agent's prose from its transcript (on explicit
        expand). `agent` is a sub-agent spanId ("span-<toolUseId>") or an
        attributionAgent role; `session` optionally scopes the search. Returns
        {messages:[{ts, relMs, text}], pending}. `pending` is true when the
        transcript does not exist yet (graceful degrade). `relMs` is ms from the
        agent's first message — the relative timing the UI renders."""
        ws = self.workspace
        try:
            path, kind = self._resolve_transcript(slug, agent, session)
        except Exception:
            path, kind = None, None
        if not path or not os.path.isfile(path):
            return {"messages": [], "pending": True}
        try:
            raw = _aipf.parse_transcript_messages(path)
        except Exception as e:  # never break the page
            return {"messages": [], "pending": False, "error": str(e)}
        # Base on the *minimum* recognizable epoch (not file order): compacted
        # or resumed transcripts may not be chronological, which would make
        # relMs go negative against a first-message base. Clamp to >= 0 too.
        epochs = [e for e in (_aipf._ts_to_epoch(m.get("ts")) for m in raw)
                  if e is not None]
        base = min(epochs) if epochs else None
        messages = []
        for m in raw:
            e0 = _aipf._ts_to_epoch(m.get("ts"))
            rel = (max(0, int(round((e0 - base) * 1000)))
                   if (e0 is not None and base is not None) else None)
            messages.append({"ts": m.get("ts"), "relMs": rel, "text": m.get("text")})
        return {"messages": messages, "pending": False}

    def _trace_actions(self, slug, agent, session=""):
        """Lazily load one agent's tool actions from its transcript (on explicit
        expand). `agent` is a sub-agent spanId ("span-<toolUseId>") or an
        attributionAgent role; `session` optionally scopes the search. Returns
        the fixed contract:
            {description, actions:[{type,name,arg,status,ts,relMs}],
             counts:{tool,bash,mcp,subtask,hook}, pending}
        `pending` is true when the transcript does not exist yet (graceful
        degrade). `description` is the agent's task description (sub-agent
        meta.json, matched by toolUseId) or null. Read-only — never touches the
        Langfuse cursor. Short mtime-keyed cache per (slug, agent), like /trace,
        because a transcript can be large."""
        empty_counts = {"tool": 0, "bash": 0, "mcp": 0, "subtask": 0, "hook": 0}
        try:
            path, kind = self._resolve_transcript(slug, agent, session)
        except Exception:
            path, kind = None, None
        if not path or not os.path.isfile(path):
            return {"description": None, "actions": [], "counts": dict(empty_counts),
                    "pending": True}
        key = (slug, agent)
        try:
            mt = os.path.getmtime(path)
        except OSError:
            mt = 0
        now = time.time()
        with Handler._actions_lock:
            cached = Handler._actions_cache.get(key)
            if cached and cached["mt"] == mt and now < cached["exp"]:
                return cached["data"]
        try:
            parsed = _aipf.parse_transcript_actions(path)
            actions = parsed.get("actions", [])
            counts = parsed.get("counts", dict(empty_counts))
        except Exception as e:  # never break the page
            return {"description": None, "actions": [], "counts": dict(empty_counts),
                    "pending": False, "error": str(e)}
        description = self._agent_description(slug, agent, session)
        data = {"description": description, "actions": actions,
                "counts": counts, "pending": False}
        with Handler._actions_lock:
            Handler._actions_cache[key] = {"mt": mt, "exp": now + 3, "data": data}
        return data

    def _agent_description(self, slug, agent, session=""):
        """Resolve an agent's task description from the sub-agent meta.json
        sidecars (matched by toolUseId == spanId without the `span-` prefix).
        Best-effort: returns None if no sidecar matches (the description also
        flows through /trace via subagent.start.summary)."""
        if not agent or not agent.startswith("span-"):
            return None
        want = agent[len("span-"):]
        ws = self.workspace
        tpath = ws.task_file(slug, "telemetry.jsonl")
        session_ids = []
        for line in _aipf._iter_lines(tpath):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            sid = ev.get("session_id")
            if sid and sid not in session_ids:
                session_ids.append(sid)
        if session:
            session_ids = [s for s in session_ids if s == session] or session_ids
        for sid in session_ids:
            for meta in _aipf.find_subagent_meta(sid):
                if meta.get("toolUseId") == want:
                    return meta.get("description")
        return None

    def _resolve_transcript(self, slug, agent, session=""):
        """Locate the transcript file for `agent` within a task's sessions.

        Sub-agents live at ~/.claude/projects/<proj>/<session>/subagents/
        agent-*.jsonl; the orchestrator at <proj>/<session>.jsonl. We learn the
        task's session ids and each span's role from telemetry.jsonl, then match
        the requested agent (spanId or attributionAgent role) to a sub-agent
        file by its attributionAgent role. Returns (path, kind)."""
        ws = self.workspace
        tpath = ws.task_file(slug, "telemetry.jsonl")
        events = []
        for line in _aipf._iter_lines(tpath):
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        session_ids = []
        for ev in events:
            sid = ev.get("session_id")
            if sid and sid not in session_ids:
                session_ids.append(sid)
        if session:
            session_ids = [s for s in session_ids if s == session] or session_ids

        # spanId -> role (for spanId-keyed requests)
        want_role = None
        if agent and agent.startswith(("span-", "tool-", "orch-")):
            if agent.startswith("orch-"):
                sid = agent[len("orch-"):]
                # Harden against path traversal: `sid` is built into a glob over
                # ~/.claude/projects/*/<sid>.jsonl, so reject anything that is not
                # a plain session id (no separators / .. ) before touching disk.
                if not sid or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
                    return (None, None)
                return (_aipf.find_main_transcript(sid), "orchestrator")
            for ev in events:
                if ev.get("event") == "subagent.start" and ev.get("spanId") == agent:
                    want_role = ev.get("role")
                    break
            if want_role is None:
                # unknown / stale spanId — don't silently fall back to another
                # transcript; let the caller report it as pending.
                return (None, None)
        else:
            want_role = agent or None

        for sid in session_ids:
            for fp in _aipf.find_subagent_files(sid):
                if want_role is None:
                    return (fp, "subagent")
                u = _aipf.parse_transcript_usage(fp)
                if u.get("role") and _aipf._role_match(want_role, u["role"]):
                    return (fp, "subagent")
        # orchestrator fallback: explicit role match or no sub-agent found
        if (not want_role or want_role in ("orchestrator", "оркестратор")) and session_ids:
            return (_aipf.find_main_transcript(session_ids[0]), "orchestrator")
        return (None, None)

    # ---- changed files (computed from git) -----------------------------
    _changes_cache = {}           # slug -> {exp, data}
    _changes_lock = threading.Lock()

    def _git(self, *args, cwd=None, timeout=10):
        """Run a git command in `cwd` (default: the project root). Returns
        (rc, stdout, stderr). A task running in a git worktree records its own
        working tree in state.worktreePath; passing it as `cwd` lets the
        Changes tab diff that tree instead of main (see _task_root)."""
        try:
            p = subprocess.run(["git", "-C", cwd or self.workspace.root, *args],
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            return p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.SubprocessError) as e:
            return 1, "", str(e)

    def _task_root(self, slug):
        """The working tree to diff for a task: state.worktreePath if it is set,
        exists and is a git work tree, else the project root (fallback for
        non-worktree tasks). Validated so a bogus path never breaks the page."""
        try:
            state = self.workspace.read_json(
                self.workspace.task_file(slug, "state.json"), {})
            wt = state.get("worktreePath")
            if wt and isinstance(wt, str):
                wt = os.path.abspath(wt)
                if (os.path.isdir(wt)
                        and self._git("rev-parse", "--is-inside-work-tree",
                                      cwd=wt)[0] == 0):
                    return wt
        except Exception:
            pass
        return self.workspace.root

    def _base_commit(self, slug, cwd=None):
        """The ref the task's diff is measured from (state.baseCommit or HEAD).
        `cwd` is the task's working tree (_task_root); a worktree may not have
        the recorded base commit, so the cat-file check runs in it too."""
        state = self.workspace.read_json(
            self.workspace.task_file(slug, "state.json"), {})
        base = state.get("baseCommit") or "HEAD"
        # defense-in-depth: a base starting with '-' would be read by git as an
        # option, not a commit-ish (argument injection) — reject it to HEAD
        # before it reaches cat-file / diff. baseCommit is agent-written, so this
        # is belt-and-suspenders, but the diff sinks below take it positionally.
        if base.startswith("-"):
            base = "HEAD"
        # if the recorded base is no longer a valid commit, fall back to HEAD
        if base != "HEAD" and self._git("cat-file", "-e", base + "^{commit}",
                                        cwd=cwd)[0] != 0:
            base = "HEAD"
        return base

    def _changes(self, slug):
        """Files changed since the task's base commit, with +/- counts.
        Computed from git with a short cache so the dashboard can poll."""
        now = time.time()
        with Handler._changes_lock:
            cached = Handler._changes_cache.get(slug)
            if cached and now < cached["exp"]:
                return cached["data"]
        try:
            data = self._build_changes(slug)
        except Exception as e:  # never break the page
            data = {"base": None, "files": [], "error": str(e)}
        with Handler._changes_lock:
            Handler._changes_cache[slug] = {"exp": now + 2, "data": data}
        return data

    def _build_changes(self, slug):
        # diff the task's own working tree (its worktree if recorded, else main)
        root = self._task_root(slug)
        if self._git("rev-parse", "--is-inside-work-tree", cwd=root)[0] != 0:
            return {"base": None, "files": [], "notGit": True}
        base = self._base_commit(slug, cwd=root)
        files = {}
        # tracked changes with line counts (working tree vs base)
        # quotePath=false gives raw UTF-8 paths (no C-quoting of cyrillic)
        rc, out, _ = self._git("-c", "core.quotePath=false",
                               "diff", "--numstat", base, cwd=root)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                added, removed, path = parts
                # renames in --numstat come as "old => new" / "pre{a => b}post";
                # skip here — the status branch below handles renames cleanly
                if " => " in path:
                    continue
                files[path] = {
                    "path": path,
                    "added": None if added == "-" else int(added),
                    "removed": None if removed == "-" else int(removed),
                    "status": "modified",
                    "untracked": False,
                }
        # status letters catch untracked files, deletions and renames.
        # quotePath=false → raw UTF-8 paths; -uall expands untracked dirs
        # (docs/) into real files instead of a single "?? docs/" line.
        rc, out, _ = self._git("-c", "core.quotePath=false",
                               "status", "--porcelain", "--untracked-files=all",
                               cwd=root)
        if rc == 0:
            for line in out.splitlines():
                if len(line) < 4:
                    continue
                x, y, rest = line[0], line[1], line[3:]
                if "?" in (x, y):
                    status = "added"
                elif "D" in (x, y):
                    status = "deleted"
                elif "R" in (x, y):
                    status = "renamed"
                    rest = rest.split(" -> ")[-1]
                elif "A" in (x, y):
                    status = "added"
                else:
                    status = "modified"
                entry = files.setdefault(
                    rest, {"path": rest, "added": None, "removed": None,
                           "status": status, "untracked": False})
                entry["status"] = status
                # untracked (new, never-staged) files surface here as "added";
                # the frontend uses this for the "tracked only / all" toggle.
                entry["untracked"] = status == "added"
                if status == "added" and entry["added"] is None:
                    entry["added"] = self._count_lines(rest, root)
                    entry["removed"] = 0
        # drop stray noise: empty (0-byte) untracked files like "-" or accidental
        # fragments. Tracked changes and non-empty untracked files are kept.
        kept = [f for f in files.values()
                if not self._is_noise(f["path"], f["status"], root)]
        return {"base": base, "files": sorted(kept, key=lambda f: f["path"]),
                "notGit": False}

    def _is_noise(self, relpath, status, root=None):
        """A stray untracked file worth hiding: an empty (0-byte) new file.
        Conservative — if we cannot stat it, we do NOT treat it as noise.
        `root` is the task's working tree (defaults to the project root)."""
        if status != "added":
            return False
        try:
            return os.path.getsize(
                os.path.join(root or self.workspace.root, relpath)) == 0
        except OSError:
            return False

    def _count_lines(self, relpath, root=None):
        try:
            with open(os.path.join(root or self.workspace.root, relpath),
                      "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except OSError:
            return None

    def _changes_file(self, slug, relpath):
        """Unified diff of one file vs the task base. Read-only, traversal-guarded.
        Diffs inside the task's own working tree (worktree if recorded, else main)."""
        if not relpath:
            return {"error": "missing file"}
        root = os.path.realpath(self._task_root(slug))
        if _aipf.confined_path(root, relpath) is None:
            return {"error": "not found"}
        base = self._base_commit(slug, cwd=root)
        rc, out, _ = self._git("diff", base, "--", relpath, cwd=root)
        if rc == 0 and out.strip():
            return {"file": relpath, "diff": out}
        # untracked / new file: diff against an empty blob
        _, out, _ = self._git("diff", "--no-index", "--", os.devnull, relpath,
                              cwd=root)
        return {"file": relpath, "diff": out}

    # ---- hub aggregate (all runs across the shared store) ---------------
    _hub_cache = {}               # singleton -> {exp, data}
    _hub_lock = threading.Lock()
    _hub_card_cache = {}          # slug -> {sig, card}  (per-task memo, see _hub_run)
    _hub_card_lock = threading.Lock()

    def _hub(self):
        """Aggregate every task in the shared store into a single render model
        for the hub page. Cached behind a lock with a short TTL like _changes,
        since it walks all tasks; degrades to an empty model on any error so the
        page never 500s. Read-only — never touches telemetry.cursor / Langfuse."""
        now = time.time()
        with Handler._hub_lock:
            cached = Handler._hub_cache.get("hub")
            if cached and now < cached["exp"]:
                return cached["data"]
        try:
            data = self._build_hub()  # now=time.time() inside
        except Exception as e:  # never break the page
            data = {"runs": [], "analytics": {}, "error": str(e)}
        with Handler._hub_lock:
            # TTL >= the hub page's 3s poll interval, so each poll lands in the
            # cache instead of re-walking every task's telemetry.jsonl.
            Handler._hub_cache["hub"] = {"exp": now + 3.0, "data": data}
        return data

    def _queue(self):
        """Read the project-level `/improve` dispatch queue (contract:
        skills/improve/dispatch-queue.md) and return it verbatim (passthrough).
        The file lives in the shared store (workspace.base, not a worktree copy —
        ADR-0010), so the server always reaches the canonical queue. Graceful: a
        missing or corrupt file yields {"items": []} (read_json catches
        FileNotFoundError / JSONDecodeError), so the endpoint never 500s. Kept
        out of the _hub cache on purpose — the file is tiny and the brief asks
        not to weigh /hub.json down. done/total are computed client-side."""
        path = os.path.join(self.workspace.base, "dispatch-queue.json")
        return self.workspace.read_json(path, {"items": []})

    def _build_hub(self, now=None):
        """Walk _list_tasks(), build a run card per task from state.json /
        dashboard.json plus one light pass over telemetry.jsonl (event counters
        only — no transcripts, no build_trace), classify active vs history, and
        compute cheap cross-task analytics. Everything per-task is wrapped so one
        bad task cannot sink the whole aggregate. `now` defaults to time.time();
        tests pass it explicitly to check the active/history split deterministically."""
        if now is None:
            now = time.time()
        runs = []
        tasks = self._list_tasks()
        for slug in tasks:
            try:
                runs.append(self._hub_run(slug, now))
            except Exception:
                continue  # skip a broken task, keep the rest
        # Prune per-task memo so it can't grow with vanished slugs (cheap, under lock).
        live = set(tasks)
        with Handler._hub_card_lock:
            for slug in [s for s in Handler._hub_card_cache if s not in live]:
                Handler._hub_card_cache.pop(slug, None)
        return {"runs": runs, "analytics": self._hub_analytics(runs)}

    def _stat_sig(self, path):
        """Cheap file signature (st_mtime, st_size) without reading the body;
        None when the file is missing / unreadable (OSError, like _trace's fallback).
        size complements mtime to catch an append in the same second on coarse-
        granularity filesystems (telemetry.jsonl is append-only)."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def _hub_signature(self, slug):
        """Per-task memo signature: a tuple of _stat_sig for the three inputs a
        card depends on, in a fixed order — telemetry.jsonl (counters),
        state.json (phase/iteration/timestamps), dashboard.json (title/status/
        progress). Any change to any of them flips the signature -> rebuild."""
        ws = self.workspace
        return (
            self._stat_sig(ws.task_file(slug, "telemetry.jsonl")),
            self._stat_sig(ws.task_file(slug, "state.json")),
            self._stat_sig(ws.task_file(slug, "dashboard.json")),
        )

    def _hub_run(self, slug, now):
        """Build one run card, memoized per-task by the mtime/size signature of
        its three input files (telemetry.jsonl / state.json / dashboard.json).
        On a signature hit the raw card is reused verbatim (no read_json, no
        telemetry pass); `active` is ALWAYS recomputed from `now` since it depends
        on wall-clock (HUB_ACTIVE_WINDOW_SEC) and must never be served stale from
        the cache. The cached dict is never mutated — we return a copy."""
        sig = self._hub_signature(slug)  # os.stat outside the lock
        with Handler._hub_card_lock:
            cached = Handler._hub_card_cache.get(slug)
            if cached and cached["sig"] == sig:
                card = cached["card"]
            else:
                card = None
        if card is None:
            card = self._hub_build_card(slug)  # read_json + telemetry pass outside the lock
            with Handler._hub_card_lock:
                Handler._hub_card_cache[slug] = {"sig": sig, "card": card}
        # Recompute the only now-dependent field; everything else is file-derived.
        return dict(card, active=self._hub_is_active(card["phase"],
                                                     card["updatedAt"], now))

    def _hub_build_card(self, slug):
        """Raw run card WITHOUT the now-dependent `active` field — depends only on
        the three input files, so it is safe to memoize. Fields are sourced from
        state.json (authoritative for phase/iteration/timestamps) and
        dashboard.json (render model: title, status, progress), with a light
        telemetry pass for activity counters (the expensive part)."""
        ws = self.workspace
        state = ws.read_json(ws.task_file(slug, "state.json"), {})
        dash = ws.read_json(ws.task_file(slug, "dashboard.json"), {})

        # phase: state.json is authoritative (ADR-0010 / areas/parallel-runs-hub);
        # dashboard.json may lag and leave a finished task looking "active".
        phase = state.get("phase") or dash.get("phase")
        # status: dashboard.json `status` first, else map the state checkpoint
        status = dash.get("status") or state.get("checkpoint")
        progress = dash.get("progress") or {}
        done = progress.get("done")
        total = progress.get("total")

        created = state.get("createdAt") or dash.get("createdAt")
        updated = state.get("updatedAt") or dash.get("updatedAt")

        tele = self._hub_telemetry(slug)

        return {
            "slug": slug,
            "title": dash.get("title") or state.get("title") or slug,
            "kind": state.get("kind"),  # e.g. "ask"; None for /feature, /improve
            "phase": phase,
            "status": status,
            "awaiting": (state.get("checkpoint") == "awaiting-batch")
            or (dash.get("status") == "awaiting-batch"),
            # live "what's happening now" for the hub command center (feat-12);
            # sourced from dashboard.json (already in _hub_signature, so the card
            # memo invalidates when `now` changes).
            "now": dash.get("now"),
            "nowAt": dash.get("nowAt"),
            "iteration": state.get("iteration"),
            "progress": {"done": done, "total": total},
            "createdAt": created,
            "updatedAt": updated,
            "worktreePath": state.get("worktreePath"),
            "branch": state.get("branch"),
            "subagents": tele["subagents"],
            "sessions": tele["sessions"],
            "events": tele["events"],
            "activity": tele["activity"],
            "firstTs": tele["firstTs"],
            "lastTs": tele["lastTs"],
            "durationMs": self._hub_duration_ms(created, updated),
        }

    def _hub_is_active(self, phase, updated, now):
        """A task is active when its phase is not terminal AND it was updated
        within HUB_ACTIVE_WINDOW_SEC; otherwise it belongs to history (q7)."""
        if phase in HUB_TERMINAL_PHASES:
            return False
        e = _aipf._ts_to_epoch(updated)
        if e is None:
            return True  # no timestamp → treat as active (don't hide live runs)
        return (now - e) < HUB_ACTIVE_WINDOW_SEC

    def _hub_duration_ms(self, created, updated):
        """Wall-clock run length from createdAt to updatedAt, in ms (or None)."""
        c = _aipf._ts_to_epoch(created)
        u = _aipf._ts_to_epoch(updated)
        if c is None or u is None or u < c:
            return None
        return int(round((u - c) * 1000))

    def _hub_telemetry(self, slug):
        """One cheap pass over a task's telemetry.jsonl: count events, distinct
        sessions, subagent.start markers, and activity (tool.* + file.touch), and
        record first/last ts. No transcripts, no build_trace — graceful on a
        missing or corrupt file (returns zeros)."""
        tpath = self.workspace.task_file(slug, "telemetry.jsonl")
        events = 0
        activity = 0
        subagents = 0
        sessions = set()
        first_ts = last_ts = None
        for line in _aipf._iter_lines(tpath):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            events += 1
            name = ev.get("event") or ""
            if name == "subagent.start":
                subagents += 1
            if name.startswith("tool.") or name == "file.touch":
                activity += 1
            sid = ev.get("session_id")
            if sid:
                sessions.add(sid)
            ts = ev.get("ts")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
        return {"events": events, "activity": activity, "subagents": subagents,
                "sessions": len(sessions), "firstTs": first_ts, "lastTs": last_ts}

    @staticmethod
    def _as_int(x, default=0):
        """Coerce a run-card field to int, tolerating str/None/garbage. Keeps a
        single bad state.json field (e.g. iteration:"two") from sinking the whole
        /hub.json aggregate when the arithmetic below sums across all tasks."""
        if isinstance(x, bool):
            return default
        if isinstance(x, (int, float)):
            return int(x)
        try:
            return int(str(x).strip())
        except (TypeError, ValueError):
            return default

    def _hub_analytics(self, runs):
        """Cross-task analytics over the assembled run cards (event-derived only,
        no tokens/cost). Counts, phase distribution, summed/median wall-clock
        duration, summed iterations, sub-agents, sessions, and activity.

        Defensive: every arithmetic aggregate coerces its field through _as_int,
        and the whole body is wrapped so one corrupt run card degrades the
        analytics block to {} instead of failing the entire hub."""
        try:
            total = len(runs)
            active = sum(1 for r in runs if r.get("active"))
            done = sum(1 for r in runs if r.get("phase") == "DONE")
            phases = {}
            for r in runs:
                p = r.get("phase") or "—"
                phases[p] = phases.get(p, 0) + 1
            durations = [self._as_int(r.get("durationMs")) for r in runs
                         if r.get("durationMs") is not None]
            return {
                "total": total,
                "active": active,
                "done": done,
                "phases": phases,
                "totalDurationMs": sum(durations) if durations else 0,
                "medianDurationMs": self._median(durations),
                "iterations": sum(self._as_int(r.get("iteration")) for r in runs),
                "subagents": sum(self._as_int(r.get("subagents")) for r in runs),
                "sessions": sum(self._as_int(r.get("sessions")) for r in runs),
                "activity": sum(self._as_int(r.get("activity")) for r in runs),
            }
        except Exception:  # never break the page
            return {}

    @staticmethod
    def _median(values):
        if not values:
            return None
        s = sorted(values)
        n = len(s)
        mid = n // 2
        if n % 2:
            return s[mid]
        return int(round((s[mid - 1] + s[mid]) / 2))

    # ---- knowledge base (tree + link graph) ----------------------------
    _knowledge_cache = {}         # slug -> {exp, data}
    _knowledge_lock = threading.Lock()
    _MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    _SKIP_DIRS = {".git", "node_modules", ".workflow", "venv", ".venv",
                  "__pycache__", "dist", "build", ".next"}

    def _knowledge_dir(self):
        """Locate the project's knowledge base (docs/knowledge or any
        `knowledge/` dir holding an INDEX.md, shallow search)."""
        root = self.workspace.root
        cand = os.path.join(root, "docs", "knowledge")
        if os.path.isdir(cand):
            return cand
        for base, dirs, files in os.walk(root):
            depth = base[len(root):].count(os.sep)
            if depth > 3:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in Handler._SKIP_DIRS
                       and not d.startswith(".")]
            if os.path.basename(base) == "knowledge" and "INDEX.md" in files:
                return base
        return None

    def _knowledge(self, slug):
        now = time.time()
        with Handler._knowledge_lock:
            cached = Handler._knowledge_cache.get(slug)
            if cached and now < cached["exp"]:
                return cached["data"]
        try:
            kdir = self._knowledge_dir()
            data = (self._build_knowledge(slug, kdir) if kdir
                    else {"exists": False, "tree": None,
                          "graph": {"nodes": [], "edges": []}})
        except Exception as e:  # never break the page
            data = {"exists": False, "tree": None,
                    "graph": {"nodes": [], "edges": []}, "error": str(e)}
        with Handler._knowledge_lock:
            Handler._knowledge_cache[slug] = {"exp": now + 4, "data": data}
        return data

    def _build_knowledge(self, slug, kdir):
        root = self.workspace.root
        relroot = lambda p: os.path.relpath(p, root)
        # which paths did this task touch (highlight them in the graph)
        changed = set()
        try:
            for f in self._build_changes(slug).get("files", []):
                changed.add(f["path"])
        except Exception:
            pass
        md_files = []
        for base, dirs, files in os.walk(kdir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if fn.endswith(".md"):
                    md_files.append(os.path.join(base, fn))
        md_files = sorted(md_files)[:500]

        nodes = {}
        edges = []

        def add_node(rid, ntype, exists=True):
            if rid not in nodes:
                nodes[rid] = {"id": rid, "label": os.path.basename(rid),
                              "type": ntype, "touched": rid in changed,
                              "exists": exists}
            return rid

        for fpath in md_files:
            did = add_node(relroot(fpath), "doc")
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(200_000)
            except OSError:
                continue
            for m in Handler._MD_LINK.finditer(text):
                target = m.group(1).strip().split("#")[0].strip()
                if (not target or target.startswith(
                        ("http://", "https://", "mailto:", "#"))):
                    continue
                tpath = re.sub(r":\d+(-\d+)?$", "", target)   # drop :line
                absdst = os.path.normpath(
                    os.path.join(os.path.dirname(fpath), tpath))
                rid = relroot(absdst)
                if rid.startswith(".."):     # outside the project root
                    continue
                is_doc = absdst.endswith(".md")
                exists = os.path.exists(absdst)
                if not is_doc and not exists:
                    continue  # skip dead code refs; keep pending doc links
                add_node(rid, "doc" if is_doc else "code", exists)
                edges.append({"from": did, "to": rid})

        return {"exists": True, "root": relroot(kdir),
                "tree": self._knowledge_tree(kdir),
                "graph": {"nodes": list(nodes.values()), "edges": edges}}

    def _knowledge_tree(self, kdir):
        root = self.workspace.root

        def node(path):
            rel = os.path.relpath(path, root)
            if os.path.isdir(path):
                children = []
                for name in sorted(os.listdir(path)):
                    if name.startswith("."):
                        continue
                    child = os.path.join(path, name)
                    if os.path.isdir(child) or name.endswith(".md"):
                        children.append(node(child))
                return {"name": os.path.basename(path), "path": rel,
                        "type": "dir", "children": children}
            return {"name": os.path.basename(path), "path": rel, "type": "file"}

        return node(kdir)

    def _knowledge_file(self, rel):
        """Return one knowledge doc's content. Read-only, traversal-guarded."""
        if not rel:
            return {"error": "missing file"}
        target = _aipf.confined_path(self.workspace.root, rel)
        if target is None or not target.endswith(".md"):
            return {"error": "not found"}
        if not os.path.isfile(target):
            return {"error": "not found"}
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            return {"file": rel, "content": f.read(400_000)}

    # ---- mutations -----------------------------------------------------
    def _draft_add(self, slug, body):
        ws = self.workspace
        ws.ensure_task(slug)
        with ws.lock(slug):
            draft = ws.read_json(ws.task_file(slug, "draft.json"), {"items": []})
            items = draft.setdefault("items", [])
            item = {
                "id": body.get("id") or f"c{int(time.time()*1000)}",
                "kind": body.get("kind", "comment"),          # comment | answer
                "blockId": body.get("blockId"),
                "questionId": body.get("questionId"),
                "selectedText": body.get("selectedText", ""),
                "text": (body.get("text") or "").strip(),
                "ts": now_iso(),
            }
            # If an answer for the same question already exists, replace it.
            if item["kind"] == "answer" and item["questionId"]:
                items[:] = [i for i in items
                            if not (i.get("kind") == "answer"
                                    and i.get("questionId") == item["questionId"])]
            items.append(item)
            ws.write_json(ws.task_file(slug, "draft.json"), draft)
        return self._json(200, {"ok": True, "count": len(items), "item": item})

    def _draft_remove(self, slug, body):
        ws = self.workspace
        item_id = body.get("id")
        with ws.lock(slug):
            draft = ws.read_json(ws.task_file(slug, "draft.json"), {"items": []})
            draft["items"] = [i for i in draft.get("items", []) if i.get("id") != item_id]
            ws.write_json(ws.task_file(slug, "draft.json"), draft)
        return self._json(200, {"ok": True, "count": len(draft["items"])})

    def _submit(self, slug):
        ws = self.workspace
        with ws.lock(slug):
            draft = ws.read_json(ws.task_file(slug, "draft.json"), {"items": []})
            items = draft.get("items", [])
            if not items:
                return self._json(200, {"ok": False, "reason": "empty draft"})
            flag = ws.read_json(ws.task_file(slug, "submit.flag"), {"latest": 0})
            n = int(flag.get("latest", 0)) + 1
            submission = {"n": n, "ts": now_iso(), "items": items}
            ws.write_json(ws.task_file(slug, f"submissions/{n}.json"), submission)
            ws.write_json(ws.task_file(slug, "submit.flag"),
                          {"latest": n, "ts": submission["ts"], "consumed": 0})
            ws.write_json(ws.task_file(slug, "draft.json"), {"items": []})
            self._append_signal(slug, "submit", {"n": n})
        self._wake(slug)
        return self._json(200, {"ok": True, "submission": n, "count": len(items)})

    def _signal(self, slug, body):
        signal = body.get("signal", "")
        if not signal:
            return self._json(400, {"error": "missing signal"})
        self.workspace.ensure_task(slug)
        with self.workspace.lock(slug):
            self._append_signal(slug, signal, body.get("payload"))
        self._wake(slug)
        return self._json(200, {"ok": True, "signal": signal})

    # ---- chat (checkpoint steering channel) ----------------------------
    def _chat_get(self, slug, since=0):
        """Return the task's chat transcript (human + agent turns).

        Incremental by byte offset (feat-10), mirroring `/trace/feed`: reads only
        the lines past `since` via `_aipf._iter_lines_from` (O(new bytes)) and
        reports `nextOffset` for the client's next poll. `since=0` reads the whole
        log (back-compat). Only complete lines are returned; a half-written
        trailing line is left for the next tick (so a message is never split)."""
        path = self.workspace.task_file(slug, "chat.jsonl")
        lines, next_offset = _aipf._iter_lines_from(path, max(0, since or 0))
        msgs = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except ValueError:
                pass
        return {"messages": msgs, "nextOffset": next_offset}

    def _chat_post(self, slug, body):
        """Append a human chat message and wake the agent (a `chat` signal).
        The agent reads new messages at its next checkpoint and appends its own
        `role:"agent"` turns to the same chat.jsonl."""
        text = (body.get("text") or "").strip()
        # Sanitize image attachments FIRST so the empty-text guard can accept an
        # image-only message. References returned by /attach; defence in depth:
        # only a list, capped at ATTACH_MAX_PER_MSG, each a dict whose `file` is a
        # safe server name — malformed entries are dropped so a forged `images`
        # can't smuggle a bad filename onto the line the /image route trusts.
        # `name`/`mime` are free-form client strings: coerce to str and cap length
        # so a forged body can't store an arbitrarily large blob on the line.
        _clean = []
        _imgs = body.get("images")
        if isinstance(_imgs, list):
            for _it in _imgs[:ATTACH_MAX_PER_MSG]:
                if not isinstance(_it, dict):
                    continue
                _file = _it.get("file")
                if not isinstance(_file, str) or not ATTACH_RE.match(_file):
                    continue
                _clean.append({"file": _file,
                               "name": str(_it.get("name"))[:200],
                               "mime": str(_it.get("mime"))[:200]})
        # Reject only a truly empty turn: no text AND no valid images. An
        # image-only paste (empty text + at least one valid image) is allowed and
        # stores text:"".
        if not text and not _clean:
            return self._json(400, {"error": "empty message"})
        ws = self.workspace
        ws.ensure_task(slug)
        msg = {"role": "human", "text": text, "ts": now_iso(),
               "phase": body.get("phase")}
        # Optional anchoring: which block/region/variant this turn discusses, and
        # the quoted fragment for select-to-comment. Copied verbatim (the backend
        # stays agnostic to content). `needsAnswer` is intentionally NOT accepted
        # from the human — only the agent sets it on its own appended turns.
        for _k in ("anchor", "quote"):
            _v = body.get(_k)
            if _v:
                msg[_k] = _v
        if _clean:
            msg["images"] = _clean
        with ws.lock(slug):
            _aipf.append_jsonl(ws.task_file(slug, "chat.jsonl"), msg)
            self._append_signal(slug, "chat", {"ts": msg["ts"]})
        self._wake(slug)
        return self._json(200, {"ok": True, "message": msg})

    def _attach(self, slug, body):
        """Decode a base64 image from {name, mime, dataB64}, write the bytes to
        <task>/attachments/ under a safe server-generated name, and return a
        reference the client then carries on its /chat message (`images:[...]`).

        Never 500: every rejection is a clean 400 {"ok":false,"error":<code>}."""
        mime = body.get("mime")
        # Coerce/validate client-supplied types *before* any use: a non-string
        # (e.g. list/dict) mime would make ATTACH_MIME_EXT.get(mime) raise
        # TypeError: unhashable type, and a non-string dataB64 would break
        # b64decode — both must surface as a clean 400, never a 500.
        if not isinstance(mime, str):
            return self._json(400, {"ok": False, "error": "type"})
        data_b64 = body.get("dataB64")
        if not isinstance(data_b64, str):
            return self._json(400, {"ok": False, "error": "decode"})
        name = str(body.get("name"))
        ext = ATTACH_MIME_EXT.get(mime)
        if not ext:
            return self._json(400, {"ok": False, "error": "type"})
        # Size guard *before* decode: base64 length is ~4/3 of the raw bytes, so
        # len(b64)*3//4 is an upper bound on the decoded size — reject early.
        if len(data_b64) * 3 // 4 > ATTACH_MAX_BYTES:
            return self._json(400, {"ok": False, "error": "size"})
        try:
            data = base64.b64decode(data_b64, validate=True)
        except Exception:  # binascii.Error et al — never 500 on bad input
            return self._json(400, {"ok": False, "error": "decode"})
        if len(data) > ATTACH_MAX_BYTES:
            return self._json(400, {"ok": False, "error": "size"})
        # Verify the decoded bytes really are the claimed image type, not just a
        # trusted client mime (defence in depth — never write non-image content).
        if not image_magic_ok(ext, data):
            return self._json(400, {"ok": False, "error": "format"})
        # Safe, unique, server-generated name (no client-controlled separators).
        servername = "att-%s.%s" % (os.urandom(4).hex(), ext)
        if not ATTACH_RE.match(servername):
            return self._json(400, {"ok": False, "error": "name"})
        ws = self.workspace
        ws.ensure_task(slug)
        attach_dir = os.path.join(ws.task_dir(slug), "attachments")
        os.makedirs(attach_dir, exist_ok=True)
        # confine to the attachments dir (defence in depth against traversal)
        path = _aipf.confined_path(attach_dir, servername)
        if path is None:
            return self._json(400, {"ok": False, "error": "name"})
        # Atomic write under the per-slug lock: per-process temp (no fixed
        # ".tmp" that two concurrent uploads to one task would collide on) +
        # retrying replace for the Windows transient — same helpers as the
        # other store writers.
        tmp = _aipf.atomic_temp_name(path)
        try:
            with ws.lock(slug):
                with open(tmp, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())   # durable before replace (see _aipf.atomic_write)
                _aipf.atomic_replace(tmp, path)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return self._json(400, {"ok": False, "error": "write"})
        return self._json(200, {"ok": True, "file": servername,
                                "name": str(name)[:200], "mime": mime,
                                "bytes": len(data)})

    def _append_signal(self, slug, signal, payload=None):
        ws = self.workspace
        log = ws.read_json(ws.task_file(slug, "signals.json"), {"signals": []})
        log.setdefault("signals", []).append(
            {"signal": signal, "payload": payload, "ts": now_iso()})
        ws.write_json(ws.task_file(slug, "signals.json"), log)

    # ---- long-poll (instant agent wake-up) -----------------------------
    def _wake(self, slug):
        """Wake any /wait long-poll parked on this slug (called after a write)."""
        c = self._waker(slug)
        with c:
            c.notify_all()

    def _wait(self, slug, qs):
        """Block until a new submission or signal appears for this slug, then
        return immediately. Lets the parked agent get re-invoked the instant the
        human clicks, instead of polling on a timer.

        Query: sinceSubmission (baseline submit.flag.latest), sinceSignal
        (baseline len(signals)), timeout seconds (clamped to [1, 3600]).
        """
        if not slug:
            return self._json(400, {"error": "missing slug"})
        ws = self.workspace

        def _qint(name, default):
            try:
                return int((qs.get(name) or [str(default)])[0])
            except (ValueError, TypeError):
                return default

        since_sub = _qint("sinceSubmission", 0)
        since_sig = _qint("sinceSignal", 0)
        try:
            timeout = float((qs.get("timeout") or ["600"])[0])
        except (ValueError, TypeError):
            timeout = 600.0
        timeout = max(1.0, min(timeout, 3600.0))

        def changed():
            flag = ws.read_json(ws.task_file(slug, "submit.flag"), {"latest": 0})
            latest = int(flag.get("latest", 0) or 0)
            sigs = ws.read_json(ws.task_file(slug, "signals.json"),
                                {"signals": []}).get("signals", [])
            if latest > since_sub or len(sigs) > since_sig:
                return {"changed": True, "submission": latest,
                        "signalCount": len(sigs), "newSignals": sigs[since_sig:]}
            return None

        c = self._waker(slug)
        end = time.monotonic() + timeout
        with c:
            while True:
                res = changed()
                if res is not None:
                    return self._json(200, res)
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return self._json(200, {"changed": False, "timeout": True,
                                            "submission": since_sub,
                                            "signalCount": since_sig})
                c.wait(remaining)

    def _telemetry(self, slug, body):
        """Append an explicit telemetry event (orchestrator phase/gate markers).

        The hook writes most events directly to telemetry.jsonl; this endpoint
        lets the orchestrator add domain markers it alone knows (phase enter,
        gate iteration/approve) through the same pipeline.
        """
        event = body.get("event")
        if not event:
            return self._json(400, {"error": "missing event"})
        self.workspace.ensure_task(slug)
        line = {"ts": _aipf.now_iso_utc(), "event": event,
                "session_id": body.get("session_id"),
                "phase": body.get("phase"), "iteration": body.get("iteration"),
                "summary": body.get("summary")}
        with self.workspace.lock(slug):
            _aipf.append_jsonl(self.workspace.task_file(slug, "telemetry.jsonl"), line)
        return self._json(200, {"ok": True, "event": event})


# Brand logo (transparent PNG, downscaled from the source asset) inlined as a
# data-URI so every page stays self-contained (ADR-0004: no CDN). Reused for the
# header mark and the favicon on the task, hub and landing pages.
LOGO_DATA_URI = "data:image/png;base64,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"


INDEX_LANDING = """<!doctype html><meta charset=utf-8>
<link rel="icon" type="image/png" href="__LOGO_SRC__">
<title>ai-pathfinder</title>
<body style="font-family:system-ui;max-width:640px;margin:48px auto;color:#222">
<h1>ai-pathfinder companion</h1>
<p>Server is running. Open a task dashboard via a link like
<code>/?slug=&lt;task-slug&gt;</code> — the agent prints it when a task starts.</p>
<p style="font-size:16px"><a href="/hub" style="font-weight:600">Open the runs hub → /hub</a></p>
<!--TASKS-->
</body>"""


# The hub page: self-contained (inline <style>+<script>, no CDN). It is an
# evolution of the landing page that fetches /hub.json and renders three
# sections per the approved layout (variant A): Active runs cards, History table,
# Aggregate analytics counters+bars. It polls every 3s, diffs on a serialized snapshot
# (like the dashboard's tick()), and swallows errors silently. Visual language
# mirrors templates/dashboard.html (root tokens, phase/status/run-status badges).
HUB_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="__LOGO_SRC__">
<title>pathfinder</title>
<script>
  // theme + language bootstrap — runs before <style>/paint so there is no flash (FOUC).
  // Theme: same contract as the dashboard, two modes. localStorage['theme'] = 'light'|'dark'
  // (explicit choice); absent (or legacy 'system') = follow the OS. documentElement
  // carries the resolved data-theme ('light'|'dark').
  // Language: english-first. localStorage['lang'] = 'en'|'ru' (shared with the dashboard);
  // absent/unknown = hard 'en'. Resolve <html lang> early; render strings via the dictionary.
  (function(){
    try {
      var s = localStorage.getItem("theme");
      var resolved = (s === "light" || s === "dark")
        ? s
        : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      document.documentElement.setAttribute("data-theme", resolved);
    } catch (e) {
      document.documentElement.setAttribute("data-theme", "light");
    }
    try {
      var l = localStorage.getItem("lang");
      document.documentElement.setAttribute("lang", l === "ru" ? "ru" : "en");
    } catch (e) {
      document.documentElement.setAttribute("lang", "en");
    }
  })();
</script>
<style>
  /* Font stacks — no CDN (ADR-0004): the design's Hanken Grotesk / JetBrains Mono
     are named first so they render when locally installed, falling back to the
     platform's UI sans / monospace otherwise. */
  :root {
    --font-sans:'Hanken Grotesk', system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --font-mono:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  :root[data-theme="light"] {
    --bg:#fafafa; --panel:#ffffff; --ink:#0a0a0a; --ink-soft:#3f3f46; --head:#52525b;
    --muted:#71717a; --muted2:#a1a1aa; --faint:#c4c4c8;
    --line:#e4e4e7; --line2:#ededed; --line3:#f4f4f5;
    --accent:#4f46e5; --accent2:#a5b4fc; --accent-soft:#eef2ff;
    --ok:#16a34a; --ok-soft:#ecfdf5; --warn:#d97706; --chip:#f4f4f5;
    --sel-bg:#0a0a0a; --sel-fg:#ffffff; --topbar:rgba(255,255,255,.85);
    --reply-bg:#f0fdf4; --reply-line:#bbf7d0;
    --shadow:none;
    --err:#ef4444; --err-soft:#fef2f2; --awaiting-soft:#fff7ed;
  }
  :root[data-theme="dark"] {
    --bg:#0f1115; --panel:#181b21; --ink:#e6e8eb; --ink-soft:#c3c8d0; --head:#aab2bd;
    --muted:#9aa3af; --muted2:#6b7480; --faint:#4b5360;
    --line:#2a2f37; --line2:#242931; --line3:#20242b;
    --accent:#818cf8; --accent2:#4f46e5; --accent-soft:#1e2230;
    --ok:#22c55e; --ok-soft:#10231a; --warn:#d97706; --chip:#232830;
    --sel-bg:#e6e8eb; --sel-fg:#0f1115; --topbar:rgba(24,27,33,.85);
    --reply-bg:#132019; --reply-line:#1f4d31;
    --shadow:none;
    --err:#f87171; --err-soft:#2a1414; --awaiting-soft:#2a2113;
  }
  * { box-sizing:border-box; }
  html, body { margin:0; }
  body { background:var(--bg); color:var(--ink);
    font:15px/1.55 var(--font-sans); padding-bottom:64px; -webkit-font-smoothing:antialiased; }
  a { color:var(--accent); text-decoration:none; }
  .wrap { max-width:1180px; margin:0 auto; padding:0 32px; }

  @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:.3;} }
  @keyframes pf-pulse { 0%,100%{ transform:scale(1); opacity:1; } 50%{ transform:scale(1.45); opacity:.55; } }
  @keyframes pf-ring { 0%{ box-shadow:0 0 0 0 var(--ring); } 70%{ box-shadow:0 0 0 7px transparent; } 100%{ box-shadow:0 0 0 0 transparent; } }
  @keyframes pf-shim { 0%{ background-position:220% 0; } 100%{ background-position:-120% 0; } }
  @keyframes pf-think { 0%,100%{ transform:scaleY(.28); } 50%{ transform:scaleY(1); } }
  .shim { background:linear-gradient(90deg,var(--accent),var(--accent2),var(--accent)); background-size:220% 100%; animation:pf-shim 1.6s linear infinite; }

  /* topbar — sticky, translucent, blurred (design language). The bar spans the
     full width (so the blur underlay + border run edge-to-edge); its content is
     centred in the 1180px column by .top-inner, matching the task page's header. */
  header.top { position:sticky; top:0; z-index:20; padding:0;
    border-bottom:1px solid var(--line2); background:var(--topbar); backdrop-filter:blur(8px); }
  header.top .top-inner { max-width:1180px; margin:0 auto; padding:14px 32px; display:flex;
    align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
  .brand { display:flex; align-items:center; gap:12px; min-width:0; }
  .brand .logo { width:42px; height:42px; border-radius:9px; flex:none; display:block; object-fit:contain; }
  /* тёмный логотип-PNG на прозрачном фоне сливается с тёмной темой —
     подкладываем белую подложку, чтобы он оставался виден */
  :root[data-theme="dark"] .brand .logo { background:#fff; padding:3px; box-sizing:border-box; }
  .brand .name { font-weight:700; font-size:17px; letter-spacing:-.02em; }
  .brand .tag { font-size:12px; color:var(--muted2); font-family:var(--font-mono); }
  .topright { display:flex; align-items:center; gap:14px; }
  .refresh { display:inline-flex; align-items:center; gap:7px; font-size:12px; color:var(--muted); }
  .refresh .dot { width:7px; height:7px; --ring:rgba(22,163,74,.5); background:var(--ok); animation:pf-ring 2s ease-out infinite; }
  .sub { color:var(--muted2); font-size:12px; font-family:var(--font-mono); }

  /* theme + language toggles — compact icon buttons (shared localStorage with the dashboard) */
  .theme-btn, .lang-btn { display:inline-flex; align-items:center; justify-content:center;
    background:var(--panel); color:var(--ink); border:1px solid var(--line); border-radius:8px;
    padding:6px 9px; line-height:1; cursor:pointer; }
  .theme-btn { font-size:14px; min-width:34px; }
  .lang-btn { font:700 12px/1 var(--font-mono); letter-spacing:.03em; min-width:34px; }
  .theme-btn:hover, .lang-btn:hover { border-color:var(--accent); }

  /* badges + flat status text (design uses flat coloured mono, not filled pills) */
  .badge { font:700 10px var(--font-mono); letter-spacing:.05em; padding:2px 7px; border-radius:5px;
    background:var(--chip); color:var(--muted); white-space:nowrap; }
  .badge.phase { background:var(--accent-soft); color:var(--accent); }
  .badge.kind { background:var(--chip); color:var(--muted); text-transform:uppercase; }
  .status { display:inline-flex; align-items:center; gap:6px; font-size:11px; font-weight:600; }
  .status.working { color:var(--ok); }
  .status.awaiting { color:var(--warn); }
  .dot { width:6px; height:6px; border-radius:50%; background:currentColor; }
  .status.working .dot { animation:pf-pulse 1.4s ease-in-out infinite; }

  .progress { height:4px; background:var(--line3); border-radius:4px; overflow:hidden; }
  .progress > div { height:100%; background:var(--accent); border-radius:4px; transition:width .4s; }
  /* status text in tables (history/queue) — flat coloured mono */
  .run-status { font:700 10px var(--font-mono); }
  .run-status.running { color:var(--accent); }
  .run-status.done { color:var(--ok); }
  .run-status.failed { color:var(--err); }
  .run-status.pending { color:var(--muted2); }
  .run-status.skipped { color:var(--warn); }
  .empty { color:var(--muted2); font-size:13px; background:var(--panel); border:1px solid var(--line);
    border-radius:12px; padding:22px; }

  /* section header (bare label + count pill, sits above its content) */
  .sec { display:flex; align-items:center; gap:9px; flex-wrap:wrap; margin:44px 0 14px; }
  .sec:first-child { margin-top:32px; }
  .sec .title { font-size:12px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--head); }
  .sec .submeta { font-size:11px; color:var(--muted2); }
  .sec .submeta code { font-family:var(--font-mono); color:var(--muted); }
  .pill { font:600 11px var(--font-mono); padding:1px 7px; border-radius:20px; background:var(--chip); color:var(--head); }
  .pill.live { background:var(--ok-soft); color:var(--ok); }
  .pill.awaiting { background:var(--awaiting-soft); color:var(--warn); }
  /* live "Now: …" line on a run card (feat-12) */
  .runcard .nowline { font:12px var(--font-mono); color:var(--ink-soft); margin:0 0 14px; overflow-wrap:anywhere; }
  .runcard .nowline.stale { opacity:.5; }
  .runcard .nowline b { color:var(--head); font-weight:600; }
  .sec .grow { margin-left:auto; }

  /* search + filter toolbar (static node #filter-bar — never rewritten by render(),
     so input focus survives the 3s poll). */
  #filter-bar { max-width:1180px; margin:28px auto 0; padding:0 32px; }
  .fpanel { display:flex; flex-direction:column; gap:12px; }
  .searchbox { display:flex; align-items:center; gap:9px; height:42px; padding:0 14px;
    background:var(--panel); border:1px solid var(--line); border-radius:10px; }
  .searchbox svg { flex:none; stroke:var(--muted2); }
  #f-search { flex:1; min-width:0; border:none; outline:none; background:transparent;
    color:var(--ink); font:13px var(--font-sans); }
  #f-search::placeholder { color:var(--muted2); }
  .searchbox .slash { font:11px var(--font-mono); color:var(--faint); border:1px solid var(--line2);
    border-radius:4px; padding:1px 6px; }
  .frow { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .frow .glabel { color:var(--muted2); font-size:10px; font-weight:700; text-transform:uppercase;
    letter-spacing:.07em; flex:none; }
  .fsep { width:1px; height:20px; background:var(--line2); margin:0 4px; }
  .chip { font:600 12px var(--font-mono); padding:7px 11px; border-radius:8px; cursor:pointer;
    background:var(--panel); color:var(--head); border:1px solid var(--line); user-select:none;
    transition:all .12s; display:inline-flex; align-items:center; gap:6px; }
  .chip:hover { border-color:var(--accent); }
  .chip.on { background:var(--sel-bg); color:var(--sel-fg); border-color:var(--sel-bg); }
  .chip .num { font:700 10px var(--font-mono); color:var(--faint); }
  .chip.on .num { color:rgba(255,255,255,.55); }
  #f-count { display:none; align-items:center; gap:10px; font-size:11px; color:var(--muted2);
    margin-left:auto; }
  #f-count.on { display:inline-flex; }
  #f-count b { font-weight:700; color:var(--head); }
  #f-reset { color:var(--head); cursor:pointer; font-weight:600; }
  #f-reset:hover { color:var(--accent); }

  /* active runs — one full-width column, every card the detailed (hero) view */
  .cards { display:grid; grid-template-columns:1fr; gap:14px; }
  .runcard { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:22px; display:flex; flex-direction:column; }
  .runcard.awaiting { border-color:var(--warn); }
  .runcard .head { display:flex; align-items:center; justify-content:space-between; gap:9px;
    flex-wrap:wrap; margin-bottom:12px; }
  .runcard .headl { display:flex; align-items:center; gap:9px; flex-wrap:wrap; min-width:0; }
  .runcard .slug { font:700 14px var(--font-mono); }
  .runcard .iter { font:11px var(--font-mono); color:var(--muted2); }
  .runcard .desc { font-size:14px; font-weight:500; line-height:1.45; color:var(--ink-soft); margin-bottom:18px; }
  .runcard .scratch { font:12px var(--font-mono); color:var(--muted2); margin-bottom:auto; }
  .runcard .meta { display:flex; gap:8px; flex-wrap:wrap; font:11px var(--font-mono); color:var(--muted2); margin:10px 0 0; }
  .runcard .meta .git { background:var(--chip); border-radius:6px; padding:1px 7px; color:var(--head); }
  .open-link { color:var(--ink); cursor:pointer; font-size:12px; font-weight:600; }
  .open-link:hover { color:var(--accent); }

  /* phase tracker (hero) */
  .track { display:flex; gap:5px; margin-bottom:7px; }
  .track .seg { flex:1; height:4px; border-radius:4px; background:var(--line); }
  .track .seg.done { background:var(--ok); }
  .tracklabels { display:flex; justify-content:space-between; font:9.5px var(--font-mono);
    letter-spacing:.04em; color:var(--muted2); margin-bottom:18px; }
  .tracklabels .cur { color:var(--accent); font-weight:700; }
  .heronow { display:flex; align-items:flex-start; gap:9px; padding:11px 13px; background:var(--bg);
    border:1px solid var(--line3); border-radius:8px; margin-bottom:18px; }
  .heronow .think { display:flex; align-items:flex-end; gap:2px; height:14px; padding-top:1px; flex:none; }
  .heronow .think span { width:2.5px; height:100%; background:var(--accent); border-radius:2px;
    transform-origin:bottom; animation:pf-think 1s ease-in-out infinite; }
  .heronow .think span:nth-child(2){ animation-delay:.2s; }
  .heronow .think span:nth-child(3){ animation-delay:.4s; }
  .heronow .txt { font-size:12px; color:var(--head); line-height:1.4; }
  .herostats { display:flex; gap:24px; align-items:center; padding-top:16px; border-top:1px solid var(--line3); }
  .herostat .n { font:700 17px var(--font-mono); }
  .herostat .n small { color:var(--faint); font-size:inherit; }
  .herostat .l { font-size:10px; color:var(--muted2); letter-spacing:.04em; text-transform:uppercase; }
  /* slim card progress */
  .slimbar { height:4px; border-radius:4px; background:var(--line3); margin:20px 0 14px; overflow:hidden; }
  .slimbar > div { height:100%; border-radius:4px; }

  /* /improve queue card */
  .listcard { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  .listcard .topbar { height:3px; background:var(--ok); }
  .qrow { display:flex; align-items:center; gap:12px; padding:10px 16px; border-bottom:1px solid var(--line3); }
  .qrow:last-child { border-bottom:none; }
  .qrow:hover { background:var(--bg); }
  .qrow .n { font:11px var(--font-mono); color:var(--faint); width:16px; flex:none; }
  .qrow .ttl { flex:1; min-width:0; font-size:13px; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .qrow .prism { font:11px var(--font-mono); color:var(--muted2); width:116px; flex:none;
    text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .qrow .run-status { width:50px; flex:none; text-align:right; }
  .qrow.failed { background:var(--err-soft); }
  .qrow.skipped { background:var(--awaiting-soft); }
  button.copy { font:600 11px var(--font-sans); padding:7px 11px; border-radius:7px; cursor:pointer;
    background:var(--panel); color:var(--ink); border:1px solid var(--line); }
  button.copy:hover { border-color:var(--accent); }
  button.copy.primary { background:var(--sel-bg); color:var(--sel-fg); border-color:var(--sel-bg); }
  code.cmd { font:12px var(--font-mono); background:var(--chip); border-radius:6px; padding:1px 7px; color:var(--head); }

  /* aggregate analytics */
  .anrow { display:grid; grid-template-columns:1.35fr 1fr; gap:28px; align-items:start; }
  .statgrid { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin-bottom:12px; }
  .stat { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }
  .stat .n { font:700 22px var(--font-mono); }
  .stat .n.ok { color:var(--ok); }
  .stat .l { font-size:10px; color:var(--muted2); letter-spacing:.03em; text-transform:uppercase; margin-top:2px; }
  .barcard { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:15px 16px; }
  .phasebar { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
  .phasebar:last-of-type { margin-bottom:0; }
  .phasebar .name { width:88px; font:11px var(--font-mono); color:var(--muted); flex:none; }
  .phasebar .bar { flex:1; height:7px; background:var(--line3); border-radius:4px; overflow:hidden; }
  .phasebar .bar > div { height:100%; border-radius:4px; background:var(--accent); }
  .phasebar .v { width:16px; text-align:right; font:11px var(--font-mono); color:var(--muted); flex:none; }
  .note { font-size:11px; color:var(--muted2); line-height:1.4; margin-top:13px; padding-top:13px; border-top:1px solid var(--line3); }

  /* history table */
  .histcard { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  .onboard { padding:18px 20px; }
  .onboard p { margin:0 0 12px; color:var(--ink); font-size:14px; }
  .onboard .cmdlist { margin:0; padding:0; list-style:none; display:grid; gap:9px; }
  .onboard .cmdlist li { font-size:13px; color:var(--muted); }
  .onboard .cmdlist code { background:var(--accent-soft); color:var(--accent); padding:2px 8px; border-radius:6px; font-weight:600; margin-right:7px; font-family:var(--font-mono); }
  table.hist { width:100%; border-collapse:collapse; font-size:12px; }
  table.hist th { text-align:left; color:var(--muted2); font:10px var(--font-mono); font-weight:400;
    letter-spacing:.05em; text-transform:uppercase; padding:11px 18px; border-bottom:1px solid var(--line2); }
  table.hist td { padding:12px 18px; border-bottom:1px solid var(--line3); }
  table.hist tr:last-child td { border-bottom:none; }
  table.hist tbody tr:hover td { background:var(--bg); }
  table.hist .mono { font:12px var(--font-mono); color:var(--muted); }
  table.hist .task { font:12px var(--font-mono); color:var(--ink); }

  /* toast — ported from templates/dashboard.html */
  .toast { position:fixed; bottom:84px; left:50%; transform:translateX(-50%); background:var(--ink); color:var(--bg); padding:10px 18px; border-radius:10px; font-size:13px; opacity:0; transition:opacity .25s; pointer-events:none; z-index:30; }
  .toast.show { opacity:1; }

  @media (max-width:860px){
    .cards { grid-template-columns:1fr; }
    .runcard.hero { grid-column:span 1; }
    .anrow { grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<header class="top">
  <div class="top-inner">
  <div class="brand">
    <img class="logo" src="__LOGO_SRC__" alt="pathfinder">
    <span class="name">pathfinder</span>
  </div>
  <div class="topright">
    <span class="refresh" id="refresh"><span class="dot"></span><span id="refresh-label">auto-refresh</span></span>
    <span class="sub" id="updated">loading…</span>
    <button class="lang-btn" id="lang-btn" title="Toggle language" aria-label="Toggle language"><span id="lang-label">EN</span></button>
    <button class="theme-btn" id="theme-btn" title="Toggle theme" aria-label="Toggle theme"><span id="theme-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none"/></svg></span></button>
  </div>
  </div>
</header>

<!-- static filter toolbar — sits OUTSIDE #root/#queue-root/#root-tail so render()'s
     innerHTML rewrites never touch it (input focus/caret survive the 3s poll). -->
<div id="filter-bar">
  <div class="fpanel">
    <div class="searchbox">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input id="f-search" type="text" placeholder="Search by title or slug…" autocomplete="off">
      <span class="slash">/</span>
    </div>
    <div class="frow">
      <span class="glabel" id="f-phase-label">phase</span><span id="f-phase" class="frow"></span>
      <span class="fsep"></span>
      <span class="glabel" id="f-kind-label">type</span><span id="f-kind" class="frow"></span>
      <span id="f-count"><span id="f-count-pre"></span> <b id="f-n">0</b> <span id="f-count-mid"></span> <span id="f-m">0</span> <span id="f-count-post"></span> <span id="f-reset">reset</span></span>
    </div>
  </div>
</div>

<div class="wrap" id="root">
  <div class="empty">loading…</div>
</div>
<div class="wrap" id="queue-root"></div>
<div class="wrap" id="root-tail"></div>
<div class="toast" id="toast"></div>

<script>
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c => (
  {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

// --- i18n: english-first dictionary + t(key) (theme pattern, ADR-0015) ---
// Flat english keys. This dictionary is DUPLICATED from templates/dashboard.html (the
// hub is a separate stream — functions are not shared; edits are synchronous, drift-prone).
// SHARED keys (identical text to the dashboard, ws5 verifies parity): header.toggleTheme,
// header.toggleLang. Hub-specific strings use the hub.* / hubq.* namespaces. Source of truth
// for the language is the global /settings.json (b1); localStorage['lang'] is a cache and the
// cross-page shared key. Hard 'en' default (no navigator.language). State (lang) lives in a
// module var + <html lang>, outside #root/#queue-root (rewritten by the 3s poll). Dynamic
// strings are read from the dictionary on EVERY render → a language switch re-renders.
const STR = {
  en: {
    "header.toggleTheme": "Toggle theme",
    "header.toggleLang": "Toggle language",
    "theme.toLight": "Dark theme — switch to light",
    "theme.toDark": "Light theme — switch to dark",
    "hub.loading": "loading…",
    "hub.search": "Search by title or slug…",
    "hub.filterPhase": "phase",
    "hub.filterKind": "type",
    "hub.countPre": "",                 // «<n> of <m> shown · filter active reset»
    "hub.countMid": "of",
    "hub.countPost": "shown · filter active",
    "hub.reset": "reset",
    "hub.openDashboard": "open dashboard →",
    "hub.open": "open",
    "hub.nothingFound": "nothing found",
    "hub.noActive": "no active runs",
    "hub.histEmpty": "history is empty",
    "hub.getStarted": "Getting started",
    "hub.emptyLead": "No tasks yet. Start one from your terminal:",
    "hub.cmd.feature": "build or change a feature in this codebase",
    "hub.cmd.improve": "audit the app and queue prioritized improvements",
    "hub.cmd.ask": "ask a read-only question about the code",
    "hub.cmd.design": "audit and improve the UI/UX of one component",
    "hub.awaiting": "⏳ awaiting reply",
    "hub.now": "Now:",
    "hub.awaitingYou": "awaiting you",
    "hub.inProgress": "in progress",
    "hub.colTask": "Task",
    "hub.colPhase": "Phase",
    "hub.colIter": "Iter.",
    "hub.colDur": "Dur.",
    "hub.colSubagents": "Sub-agents",
    "hub.colSessions": "Sessions",
    "hub.colUpdated": "Updated",
    "hub.secActive": "Active runs",
    "hub.secHistory": "History",
    "hub.secAnalytics": "Aggregate analytics",
    "hub.stat.total": "total tasks",
    "hub.stat.active": "active",
    "hub.stat.done": "completed",
    "hub.stat.subagents": "sub-agents",
    "hub.stat.sessions": "sessions",
    "hub.stat.medianDur": "median dur.",
    "hub.tokensNote": "Tokens and cost are not part of the cross-task aggregate (expensive and unavailable from a worktree) — they open lazily on the task dashboard.",
    "hub.updated": "updated",
    "hub.autoRefresh": "auto-refresh",
    "hub.steps": "steps",
    "hub.live": "live",
    "hub.months": "Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec",
    // /improve dispatch queue
    "hubq.title": "/improve queue",
    "hubq.copied": "Command copied",
    "hubq.copyDrain": "📋 Copy drain command",
    "hubq.source": "source",
    "hubq.remaining": "remaining",        // «remaining N»
    "hubq.failureOne": "failure",
    "hubq.failureMany": "failures",
    "hubq.colFeature": "Feature",
    "hubq.colPrism": "Prism",
    "hubq.colStatus": "Status",
    "hubq.status.done": "done",
    "hubq.status.inProgress": "in progress",
    "hubq.status.pending": "queued",
    "hubq.status.skipped": "skipped",
    "hubq.status.failed": "failed",
    // image attachments
    "attach.button": "Attach image",
    "attach.remove": "Remove image",
    "attach.hint": "Drag, paste, or browse to attach an image",
    "attach.errType": "Unsupported image type",
    "attach.errSize": "Image too large (max 5 MB)",
    "attach.errCount": "Too many images (max 6)",
    // duration units (fmtDur)
    "dur.s": "s",
    "dur.m": "m",
    "dur.h": "h",
  },
  ru: {
    "header.toggleTheme": "Сменить тему",
    "header.toggleLang": "Сменить язык",
    "theme.toLight": "Тёмная тема — переключить на светлую",
    "theme.toDark": "Светлая тема — переключить на тёмную",
    "hub.loading": "загрузка…",
    "hub.search": "Поиск по названию или slug…",
    "hub.filterPhase": "фаза",
    "hub.filterKind": "тип",
    "hub.countPre": "найдено",
    "hub.countMid": "из",
    "hub.countPost": "· фильтр активен",
    "hub.reset": "сбросить",
    "hub.openDashboard": "открыть дашборд →",
    "hub.open": "открыть",
    "hub.nothingFound": "ничего не найдено",
    "hub.noActive": "нет активных запусков",
    "hub.histEmpty": "история пуста",
    "hub.getStarted": "С чего начать",
    "hub.emptyLead": "Задач пока нет. Запустите первую из терминала:",
    "hub.cmd.feature": "построить или изменить фичу в этом коде",
    "hub.cmd.improve": "проаудитить приложение и собрать приоритезированный бэклог",
    "hub.cmd.ask": "задать read-only вопрос о коде",
    "hub.cmd.design": "аудит и улучшение UI/UX одного компонента",
    "hub.awaiting": "⏳ ждёт ответа",
    "hub.now": "Сейчас:",
    "hub.awaitingYou": "ждут вас",
    "hub.inProgress": "в работе",
    "hub.colTask": "Задача",
    "hub.colPhase": "Фаза",
    "hub.colIter": "Итер.",
    "hub.colDur": "Длит.",
    "hub.colSubagents": "Под-агенты",
    "hub.colSessions": "Сессии",
    "hub.colUpdated": "Обновлено",
    "hub.secActive": "Активные запуски",
    "hub.secHistory": "История",
    "hub.secAnalytics": "Обобщённая аналитика",
    "hub.stat.total": "всего задач",
    "hub.stat.active": "активных",
    "hub.stat.done": "завершено",
    "hub.stat.subagents": "под-агентов",
    "hub.stat.sessions": "сессий",
    "hub.stat.medianDur": "медиана длит.",
    "hub.tokensNote": "Токены и стоимость не входят в кросс-задачный агрегат (дорого и недоступно из worktree) — открываются лениво на дашборде задачи.",
    "hub.updated": "обновлено",
    "hub.autoRefresh": "автообновление",
    "hub.steps": "шагов",
    "hub.live": "live",
    "hub.months": "янв,фев,мар,апр,мая,июн,июл,авг,сен,окт,ноя,дек",
    "hubq.title": "Очередь /improve",
    "hubq.copied": "Команда скопирована",
    "hubq.copyDrain": "📋 Копировать команду дренажа",
    "hubq.source": "источник",
    "hubq.remaining": "осталось",
    "hubq.failureOne": "сбой",
    "hubq.failureMany": "сбоев",
    "hubq.colFeature": "Фича",
    "hubq.colPrism": "Призма",
    "hubq.colStatus": "Статус",
    "hubq.status.done": "done",
    "hubq.status.inProgress": "в работе",
    "hubq.status.pending": "в очереди",
    "hubq.status.skipped": "пропущено",
    "hubq.status.failed": "сбой",
    "attach.button": "Прикрепить изображение",
    "attach.remove": "Удалить изображение",
    "attach.hint": "Перетащите, вставьте или выберите изображение",
    "attach.errType": "Неподдерживаемый тип изображения",
    "attach.errSize": "Изображение слишком большое (макс. 5 МБ)",
    "attach.errCount": "Слишком много изображений (макс. 6)",
    "dur.s": "с",
    "dur.m": "м",
    "dur.h": "ч",
  },
};
let lang = (function(){ const l = localStorage.getItem("lang"); return l === "ru" ? "ru" : "en"; })();
function t(key){
  const d = STR[lang] || STR.en;
  if(d[key] != null) return d[key];
  if(STR.en[key] != null) return STR.en[key];   // fallback to en, then the key itself
  return key;
}
function storedLang(){ const l = localStorage.getItem("lang"); return (l === "en" || l === "ru") ? l : null; }
function resolveLang(){ return storedLang() || "en"; }   // hard en default (no navigator.language)
// Apply <html lang>, the control label (= CURRENT language, like the v1 mockup) and the
// static header/filter strings; the dynamic sections are redrawn by render()/renderQueue().
function applyLang(){
  lang = resolveLang();
  document.documentElement.setAttribute("lang", lang);
  const lbl = document.getElementById("lang-label"); if(lbl) lbl.textContent = lang.toUpperCase();
  const lb = document.getElementById("lang-btn");
  if(lb) lb.title = lang === "ru" ? "Switch to English" : "Switch to Russian";
  applyStaticStrings();
}
// Static nodes that exist in the markup (header + filter toolbar); the polled sections
// (#root/#queue-root) read the dictionary on each render(), no need to touch them here.
function applyStaticStrings(){
  const set = (id, val, attr) => { const el = document.getElementById(id); if(el){ if(attr) el.setAttribute(attr, val); else el.textContent = val; } };
  set("theme-btn", t("header.toggleTheme"), "title"); set("theme-btn", t("header.toggleTheme"), "aria-label");
  set("refresh-label", t("hub.autoRefresh"));
  set("f-search", t("hub.search"), "placeholder");
  set("f-phase-label", t("hub.filterPhase"));
  set("f-kind-label", t("hub.filterKind"));
  set("f-count-pre", t("hub.countPre"));
  set("f-count-mid", t("hub.countMid"));
  set("f-count-post", t("hub.countPost"));
  set("f-reset", t("hub.reset"));
}
// Re-render every dynamic zone under the current language (strings come from the dictionary
// on each render). Cached payloads (lastData/lastQueue) are re-rendered; the diff stamps are
// reset so a switch with unchanged data still repaints.
function relangAll(){
  if(lastData){ last = ""; render(lastData); }
  // queue is an independent poll: repaint from its cache and reset the diff stamp
  if(lastQueueData){ lastQueue = ""; renderQueue(lastQueueData); }
}
// POST the global setting (best-effort; b1 contract: {"lang": ...} with no slug). The hub is
// the writer of the global language — this is what makes it "change from the hub".
function pushLang(l){
  try { fetch("/settings", { method:"POST", body: JSON.stringify({ lang: l }) }).catch(() => {}); } catch(e){}
}
(function initLang(){
  applyLang();
  const btn = document.getElementById("lang-btn");
  if(btn) btn.addEventListener("click", function(){
    const next = lang === "ru" ? "en" : "ru";       // flip EN⇄RU
    localStorage.setItem("lang", next);             // shared cross-page key
    applyLang();
    pushLang(next);                                 // write the global /settings.json (b1)
    relangAll();                                    // repaint dynamic zones under the new language
  });
  // source of truth — /settings.json (b1); pull on start and sync the localStorage cache.
  fetch("/settings.json").then(r => r.ok ? r.json() : null).then(s => {
    if(!s) return;
    const sl = (s.lang === "en" || s.lang === "ru") ? s.lang : "en";
    if(sl !== resolveLang()){ localStorage.setItem("lang", sl); applyLang(); relangAll(); }
    else { localStorage.setItem("lang", sl); }      // pin the cache even when it already matches
  }).catch(() => {});
})();

// --- theme toggle: two modes (light/dark), icon button; shares localStorage['theme']
// with the dashboard. Bootstrap in <head> already set data-theme before paint; here we
// wire the button, persist the explicit choice, and follow the OS until one is made.
const themeMql = window.matchMedia("(prefers-color-scheme: dark)");
function storedTheme(){ var s = localStorage.getItem("theme"); return (s === "light" || s === "dark") ? s : null; }
function resolveTheme(){ return storedTheme() || (themeMql.matches ? "dark" : "light"); }
function applyTheme(){
  var th = resolveTheme();
  document.documentElement.setAttribute("data-theme", th);
  var btn = document.getElementById("theme-btn");
  if(btn) btn.title = th === "dark" ? t("theme.toLight") : t("theme.toDark");
}
var themeBtn = document.getElementById("theme-btn");
if(themeBtn) themeBtn.addEventListener("click", function(){
  var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  localStorage.setItem("theme", cur === "dark" ? "light" : "dark");
  applyTheme();
});
themeMql.addEventListener("change", function(){ if(!storedTheme()) applyTheme(); });
applyTheme();

// terminal outcome -> run-status class (history table / phase tints)
function runStatusClass(phase){
  if(phase === "DONE") return "done";
  if(phase === "ABORTED") return "failed";
  return "running";
}

// status badge for active cards (mirrors dashboard.html .status.working/.awaiting)
function statusBadge(status){
  const s = String(status||"");
  if(s === "awaiting-batch" || s === "awaiting")
    return '<span class="status awaiting"><span class="dot"></span>'+esc(t("hub.awaiting"))+'</span>';
  return '<span class="status working"><span class="dot"></span>'+esc(t("hub.inProgress"))+'</span>';
}

function fmtDur(ms){
  if(ms==null) return "—";
  const s=Math.round(ms/1000);
  if(s<60) return s+t("dur.s");
  const m=Math.floor(s/60);
  if(m<60){ const r=s%60; return r?`${m}${t("dur.m")} ${r}${t("dur.s")}`:`${m}${t("dur.m")}`; }
  const h=Math.floor(m/60), rm=m%60; return rm?`${h}${t("dur.h")} ${rm}${t("dur.m")}`:`${h}${t("dur.h")}`;
}

// month abbreviations come from the dictionary (comma-joined per language)
function months(){ return t("hub.months").split(","); }
function fmtDate(ts){
  if(!ts) return "—";
  const d=new Date(String(ts).replace(" ","T"));
  if(isNaN(d)) return esc(ts);
  const pad=n=>String(n).padStart(2,"0");
  return `${d.getDate()} ${months()[d.getMonth()]} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function progressPct(p){
  if(!p || !p.total) return 0;
  return Math.max(0, Math.min(100, Math.round((p.done||0)/p.total*100)));
}

// run status as flat coloured text (working / awaiting), shared by hero+slim cards
function runStatus(r){
  if(r.awaiting)
    return `<span class="status awaiting"><span class="dot"></span>${esc(t("hub.awaiting"))}</span>`;
  return statusBadge(r.status);
}

// /feature pipeline → segmented phase tracker (hero card). A phase that is not a
// /feature stage (scratch, ANSWER, …) maps to -1 and renders no tracker.
const PHASE_ORDER = ["EXPLORE","PLAN","IMPLEMENT","VERIFY","DONE"];
const PHASE_MAP = { INTAKE:0, EXPLORE:0, ELABORATE:1, "PLAN GATE":1, PLAN:1,
  IMPLEMENT:2, VERIFY:3, DONE:4 };
function phaseIndex(phase){
  const p = String(phase||"").toUpperCase();
  return (p in PHASE_MAP) ? PHASE_MAP[p] : -1;
}
function phaseTracker(phase){
  const idx = phaseIndex(phase);
  if(idx < 0) return "";
  const segs = PHASE_ORDER.map((_, i) =>
    i < idx ? '<div class="seg done"></div>'
    : i === idx ? '<div class="seg shim"></div>'
    : '<div class="seg"></div>').join("");
  const labels = PHASE_ORDER.map((name, i) =>
    `<span${i === idx ? ' class="cur"' : ''}>${name}</span>`).join("");
  return `<div class="track">${segs}</div><div class="tracklabels">${labels}</div>`;
}

// compact relative age (feat-12); literal s/m/h suffixes (unit-neutral)
function fmtAge(sec){
  if(sec < 60) return Math.round(sec) + "s";
  if(sec < 3600) return Math.round(sec/60) + "m";
  return Math.round(sec/3600) + "h";
}
// live "Now: …" line for a run card. Hidden while awaiting (the badge says it all)
// or when empty; greyed when the timestamp is stale (>90s), mirroring the task
// dashboard's renderNow.
function nowLine(r){
  if(r.awaiting || !r.now) return "";
  const ms = r.nowAt ? Date.parse(r.nowAt) : NaN;
  const age = isNaN(ms) ? null : Math.max(0, (Date.now() - ms)/1000);
  const stale = age != null && age > 90;
  const ageTxt = (age != null && !stale) ? " · " + fmtAge(age) : "";
  return `<div class="nowline${stale ? " stale" : ""}">${esc(t("hub.now"))} <b>${esc(r.now)}</b>${esc(ageTxt)}</div>`;
}

// hero card — the first active run, with phase tracker + stats row
function heroCard(r){
  const track = phaseTracker(r.phase);
  const slimbar = track ? "" :
    `<div class="slimbar"><div class="shim" style="width:${progressPct(r.progress)}%"></div></div>`;
  const steps = (r.progress && r.progress.total)
    ? `${esc(r.progress.done||0)}<small>/${esc(r.progress.total)}</small>` : "—";
  const iter = (r.iteration!=null) ? `<span class="iter">iter ${esc(r.iteration)}</span>` : "";
  return `<div class="runcard hero${r.awaiting ? " awaiting" : ""}">
    <div class="head">
      <div class="headl">
        <span class="slug">${esc(r.slug)}</span>
        ${r.phase ? `<span class="badge phase">${esc(r.phase)}</span>` : ""}
        ${r.kind ? `<span class="badge kind">${esc(r.kind)}</span>` : ""}
        ${iter}
      </div>
      ${runStatus(r)}
    </div>
    ${r.title ? `<div class="desc">${esc(r.title)}</div>` : ""}
    ${track}${slimbar}
    ${nowLine(r)}
    <div class="herostats">
      <div class="herostat"><div class="n">${steps}</div><div class="l">${esc(t("hub.steps"))}</div></div>
      <div class="herostat"><div class="n">${r.subagents==null?"—":esc(r.subagents)}</div><div class="l">${esc(t("hub.stat.subagents"))}</div></div>
      <div class="herostat"><div class="n">${r.sessions==null?"—":esc(r.sessions)}</div><div class="l">${esc(t("hub.stat.sessions"))}</div></div>
      <a class="open-link" style="margin-left:auto" href="/?slug=${encodeURIComponent(r.slug)}">${esc(t("hub.openDashboard"))}</a>
    </div>
  </div>`;
}

function histRow(r){
  const kind = r.kind ? ` <span class="badge kind">${esc(r.kind)}</span>` : "";
  return `<tr>
    <td class="task">${esc(r.slug)}${kind}</td>
    <td><span class="run-status ${runStatusClass(r.phase)}">${esc(r.phase||"—")}</span></td>
    <td class="mono">${r.iteration==null?"—":esc(r.iteration)}</td>
    <td class="mono">${fmtDur(r.durationMs)}</td>
    <td class="mono">${r.subagents==null?"—":esc(r.subagents)}</td>
    <td class="mono">${r.sessions==null?"—":esc(r.sessions)}</td>
    <td class="mono">${fmtDate(r.updatedAt)}</td>
    <td><a class="open-link" href="/?slug=${encodeURIComponent(r.slug)}">${esc(t("hub.open"))} →</a></td>
  </tr>`;
}

// phase distribution bars (aggregate analytics); bar colour hints terminal vs live
function phaseBarColor(name){
  const p = String(name||"").toUpperCase();
  if(p === "DONE") return "var(--ok)";
  if(p === "ABORTED" || p === "FAILED") return "var(--err)";
  return phaseIndex(p) >= 0 ? "var(--accent)" : "var(--muted2)";
}
function phaseBars(phases){
  const entries = Object.entries(phases||{});
  if(!entries.length) return "";
  const max = Math.max.apply(null, entries.map(e=>e[1]));
  return entries.map(([name,v])=>{
    const w = max ? Math.round(v/max*100) : 0;
    return `<div class="phasebar"><span class="name">${esc(name)}</span>`+
      `<span class="bar"><div style="width:${w}%;background:${phaseBarColor(name)}"></div></span>`+
      `<span class="v">${esc(v)}</span></div>`;
  }).join("");
}

// --- client-side search + chip filters (no server changes) ---
// State lives in module vars so a chip click can re-filter the cached lastData
// without a new fetch. The toolbar (#filter-bar) is a static node render() never
// rewrites, so input focus/caret survive the poll (the bug from feature 3).
const KIND_FEAT = "feature/improve"; // synthetic kind for runs with kind===null (/feature, /improve)
let q = "";
let activePhases = new Set();
let activeKinds = new Set();
let lastData = null;
let lastChipKey = ""; // serialised phase/kind sets — chips redraw only when this changes

function filterActive(){ return !!(q.trim() || activePhases.size || activeKinds.size); }

function matches(r){
  const text = (String(r.title||"") + " " + String(r.slug||"")).toLowerCase()
    .includes(q.trim().toLowerCase());
  const byPhase = !activePhases.size || activePhases.has(r.phase);
  const byKind = !activeKinds.size || activeKinds.has(r.kind || KIND_FEAT);
  return text && byPhase && byKind;
}

// Fresh-install getting-started block: shown instead of the three empty
// sections when there are no tasks at all (not merely filtered out).
function gettingStarted(){
  const cmds = [
    ["/feature", t("hub.cmd.feature")],
    ["/improve", t("hub.cmd.improve")],
    ["/ask",     t("hub.cmd.ask")],
    ["/design",  t("hub.cmd.design")],
  ].map(([c,d])=>`<li><code>${esc(c)}</code>${esc(d)}</li>`).join("");
  return `<div class="sec"><span class="title">${esc(t("hub.getStarted"))}</span></div>
    <div class="histcard onboard">
      <p>${esc(t("hub.emptyLead"))}</p>
      <ul class="cmdlist">${cmds}</ul>
    </div>`;
}
function render(data){
  const runs = data.runs || [];
  // No tasks at all → onboarding, not three "empty" sections (a filter can never
  // produce this since runs itself is empty).
  if(runs.length === 0){
    buildChips(runs);
    updateCount(0, 0);
    document.getElementById("root").innerHTML = gettingStarted();
    document.getElementById("root-tail").innerHTML = "";
    document.getElementById("updated").textContent =
      t("hub.updated") + " " + fmtDate(new Date().toISOString());
    return;
  }
  const shown = runs.filter(matches);
  const active = shown.filter(r=>r.active);
  active.sort((a,b)=>(b.awaiting?1:0)-(a.awaiting?1:0));
  const history = shown.filter(r=>!r.active);
  const a = data.analytics || {};

  buildChips(runs);
  updateCount(shown.length, runs.length);

  // empty text depends on whether a filter is active: "nothing found" when the
  // user filtered everything out, else the default section-empty message.
  const filtered = filterActive();
  const activeEmpty = filtered ? t("hub.nothingFound") : t("hub.noActive");
  const histEmpty = filtered ? t("hub.nothingFound") : t("hub.histEmpty");

  // active runs: one full-width column, every card the detailed (hero) view
  const activeHtml = active.length
    ? `<div class="cards">${active.map((r)=> heroCard(r)).join("")}</div>`
    : `<div class="empty">${activeEmpty}</div>`;

  const histHtml = history.length
    ? `<div class="histcard"><table class="hist"><thead><tr>
        <th>${esc(t("hub.colTask"))}</th><th>${esc(t("hub.colPhase"))}</th><th>${esc(t("hub.colIter"))}</th><th>${esc(t("hub.colDur"))}</th>
        <th>${esc(t("hub.colSubagents"))}</th><th>${esc(t("hub.colSessions"))}</th><th>${esc(t("hub.colUpdated"))}</th><th></th>
      </tr></thead><tbody>${history.map(histRow).join("")}</tbody></table></div>`
    : `<div class="empty">${histEmpty}</div>`;

  const stats = [
    [a.total, t("hub.stat.total"), ""],
    [a.active, t("hub.stat.active"), "ok"],
    [a.done, t("hub.stat.done"), ""],
    [a.subagents, t("hub.stat.subagents"), ""],
    [a.sessions, t("hub.stat.sessions"), ""],
    [fmtDur(a.medianDurationMs), t("hub.stat.medianDur"), ""],
  ].map(([n,l,c])=>`<div class="stat"><div class="n ${c}">${n==null?"—":esc(n)}</div>`+
    `<div class="l">${esc(l)}</div></div>`).join("");

  // #root holds active runs; #queue-root (filled by tickQueue) sits between;
  // #root-tail holds history + analytics. Splitting keeps the queue section
  // visually between "Active runs" and "History" while #root is overwritten every
  // /hub.json diff without ever touching the independent #queue-root node.
  const liveBadge = active.length
    ? `<span class="pill live">${active.length} ${esc(t("hub.live"))}</span>` : "";
  const awaitN = active.filter(r => r.awaiting).length;   // command center: who needs you (feat-12)
  const awaitBadge = awaitN
    ? `<span class="pill awaiting">${awaitN} ${esc(t("hub.awaitingYou"))}</span>` : "";
  document.getElementById("root").innerHTML = `
    <div class="sec"><span class="title">${esc(t("hub.secActive"))}</span>${liveBadge}${awaitBadge}</div>
    ${activeHtml}`;
  document.getElementById("root-tail").innerHTML = `
    <div class="sec"><span class="title">${esc(t("hub.secHistory"))}</span><span class="pill">${history.length}</span></div>
    ${histHtml}
    <div class="sec"><span class="title">${esc(t("hub.secAnalytics"))}</span></div>
    <div class="statgrid">${stats}</div>
    <div class="barcard">${phaseBars(a.phases)}<div class="note">${esc(t("hub.tokensNote"))}</div></div>`;
  document.getElementById("updated").textContent =
    t("hub.updated") + " " + fmtDate(new Date().toISOString());
}

// Build phase/kind chips dynamically from the data (the kind list is {ask,null}
// today, so a hardcoded list would be wrong — q1=A). null kind → synthetic
// KIND_FEAT chip (q2=A). Redraw only when the set of values changes (lastChipKey)
// so the toolbar DOM isn't churned every poll; #f-search is never rewritten.
function chipHtml(value, on, count){
  const num = (count==null) ? "" : ` <span class="num">${esc(count)}</span>`;
  return `<span class="chip${on ? " on" : ""}" data-v="${esc(value)}">${esc(value)}${num}</span>`;
}
function buildChips(runs){
  const phases = [...new Set(runs.map(r=>r.phase).filter(Boolean))].sort();
  const kinds = [...new Set(runs.map(r=>r.kind || KIND_FEAT))].sort();
  const phaseCount = {};
  runs.forEach(r=>{ if(r.phase) phaseCount[r.phase] = (phaseCount[r.phase]||0) + 1; });
  // counts join the key so a redraw refreshes the per-phase badge; the chip
  // container holds no <input>, so re-rendering it on a poll is harmless.
  const key = JSON.stringify([phases, kinds, phaseCount]);
  if(key === lastChipKey) {
    // value sets unchanged — only refresh the .on state (cheap, no input nearby)
    syncChipState();
    return;
  }
  lastChipKey = key;
  const phaseEl = document.getElementById("f-phase");
  const kindEl = document.getElementById("f-kind");
  if(phaseEl) phaseEl.innerHTML = phases.map(p=>chipHtml(p, activePhases.has(p), phaseCount[p])).join("");
  if(kindEl) kindEl.innerHTML = kinds.map(k=>chipHtml(k, activeKinds.has(k))).join("");
}
function syncChipState(){
  document.querySelectorAll("#f-phase .chip").forEach(el=>
    el.classList.toggle("on", activePhases.has(el.dataset.v)));
  document.querySelectorAll("#f-kind .chip").forEach(el=>
    el.classList.toggle("on", activeKinds.has(el.dataset.v)));
}
function updateCount(n, m){
  const box = document.getElementById("f-count");
  if(!box) return;
  if(filterActive()){
    document.getElementById("f-n").textContent = n;
    document.getElementById("f-m").textContent = m;
    box.classList.add("on");
  } else {
    box.classList.remove("on");
  }
}

// persist filter state (q3=A) — mirror of the localStorage['theme'] pattern.
function persistFilter(){
  try {
    localStorage.setItem("hubFilter", JSON.stringify({
      q, phases:[...activePhases], kinds:[...activeKinds] }));
  } catch (e) { /* storage unavailable — non-fatal */ }
}
function restoreFilter(){
  try {
    const s = JSON.parse(localStorage.getItem("hubFilter") || "null");
    if(!s) return;
    q = typeof s.q === "string" ? s.q : "";
    activePhases = new Set(Array.isArray(s.phases) ? s.phases : []);
    activeKinds = new Set(Array.isArray(s.kinds) ? s.kinds : []);
    const search = document.getElementById("f-search");
    if(search) search.value = q;
  } catch (e) { /* corrupt/absent — start with empty filter */ }
}

function reFilter(){ if(lastData) render(lastData); persistFilter(); }
function toggleChip(set, value){
  if(set.has(value)) set.delete(value); else set.add(value);
}

// wire toolbar handlers once: search input + delegated chip clicks + reset.
function initFilter(){
  restoreFilter();
  const search = document.getElementById("f-search");
  if(search) search.addEventListener("input", function(){ q = this.value; reFilter(); });
  const phaseEl = document.getElementById("f-phase");
  if(phaseEl) phaseEl.addEventListener("click", function(e){
    const chip = e.target.closest(".chip"); if(!chip) return;
    toggleChip(activePhases, chip.dataset.v); syncChipState(); reFilter();
  });
  const kindEl = document.getElementById("f-kind");
  if(kindEl) kindEl.addEventListener("click", function(e){
    const chip = e.target.closest(".chip"); if(!chip) return;
    toggleChip(activeKinds, chip.dataset.v); syncChipState(); reFilter();
  });
  const reset = document.getElementById("f-reset");
  if(reset) reset.addEventListener("click", function(){
    q = ""; activePhases.clear(); activeKinds.clear();
    if(search) search.value = "";
    syncChipState(); reFilter();
  });
  // "/" focuses the search box (matches the keycap hint in the toolbar), unless
  // the user is already typing in a field.
  document.addEventListener("keydown", function(e){
    if(e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    const el = document.activeElement;
    if(el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
    if(search){ e.preventDefault(); search.focus(); }
  });
}
initFilter();

let last = "";
async function tick(){
  try {
    const res = await fetch("/hub.json");
    const data = await res.json();
    lastData = data; // cache so chip/search clicks can re-filter without a fetch
    const stamp = JSON.stringify(data);
    if(stamp !== last){ last = stamp; render(data); }
  } catch (e) { /* server may be restarting — swallow and retry next tick */ }
}
tick();
setInterval(tick, 3000);

// --- cross-page language sync (every 3s, like the dashboard) ---
// The shared localStorage['lang'] only syncs within the same origin/tab lifetime; polling
// /settings.json keeps the hub in step when the language is changed on the dashboard (or in
// another hub tab). Independent of the /hub.json diff so a no-op poll is cheap.
async function tickLang(){
  try {
    const res = await fetch("/settings.json");
    const s = await res.json();
    const sl = (s && (s.lang === "en" || s.lang === "ru")) ? s.lang : "en";
    if(sl !== resolveLang()){ localStorage.setItem("lang", sl); applyLang(); relangAll(); }
  } catch (e) { /* server may be restarting — swallow and retry next tick */ }
}
setInterval(tickLang, 3000);

// --- toast (ported from templates/dashboard.html) ---
function toast(msg){
  const t = document.getElementById("toast");
  if(!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1800);
}

// copy the drain command to the clipboard, with an execCommand fallback for
// non-secure contexts (navigator.clipboard needs a secure origin).
const DRAIN_CMD = "/loop /feature";
function copyDrainCmd(){
  const done = () => toast(t("hubq.copied"));
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(DRAIN_CMD).then(done).catch(() => fallbackCopy(DRAIN_CMD) && done());
    return;
  }
  if(fallbackCopy(DRAIN_CMD)) done();
}
function fallbackCopy(text){
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}

// --- /improve dispatch queue section (independent poll, own diff) ---
// status -> CSS class on .run-status (in-progress maps to the running style)
function queueStatusClass(status){
  const s = String(status||"");
  if(s === "done") return "done";
  if(s === "failed") return "failed";
  if(s === "skipped") return "skipped";
  if(s === "pending") return "pending";
  return "running"; // in-progress and anything unknown
}
// queue status -> dictionary key (localized via t() on each render)
const QSTATUS_KEY = { done:"hubq.status.done", "in-progress":"hubq.status.inProgress",
  pending:"hubq.status.pending", skipped:"hubq.status.skipped", failed:"hubq.status.failed" };
function queueStatusLabel(status){
  const s = String(status||"");
  return QSTATUS_KEY[s] ? t(QSTATUS_KEY[s]) : (s || "—");
}
function queueRow(it){
  const cls = queueStatusClass(it.status);
  const rowCls = (cls === "failed" || cls === "skipped") ? " " + cls : "";
  const prism = it.prism ? esc(it.prism) : (it.candId ? esc(it.candId) : "—");
  const label = (cls === "done" ? "✓ " : "") + queueStatusLabel(it.status);
  // the status text doubles as the open link when the feature has a dashboard
  const status = it.slug
    ? `<a class="run-status ${cls}" href="/?slug=${encodeURIComponent(it.slug)}">${esc(label)}</a>`
    : `<span class="run-status ${cls}">${esc(label)}</span>`;
  return `<div class="qrow${rowCls}">
    <span class="n">${it.n==null?"":esc(it.n)}</span>
    <span class="ttl">${esc(it.title)}</span>
    <span class="prism">${prism}</span>
    ${status}
  </div>`;
}
function renderQueue(data){
  const root = document.getElementById("queue-root");
  if(!root) return;
  const items = (data && data.items) || [];
  if(!items.length){ root.innerHTML = ""; return; } // graceful: hide when empty
  // once the queue is fully drained (nothing still pending or in progress),
  // hide the whole block — the finished runs already live on in history.
  const active = items.some(i => i.status === "pending" || i.status === "in-progress");
  if(!active){ root.innerHTML = ""; return; }
  const total = items.length;
  const done = items.filter(i => i.status === "done").length;
  const failed = items.filter(i => i.status === "failed").length;
  const src = data.source
    ? `${esc(t("hubq.source"))} <code class="cmd">${esc(data.source)}</code>` : "";
  const failMeta = failed
    ? ` · ${failed} ${esc(failed===1?t("hubq.failureOne"):t("hubq.failureMany"))}` : "";
  root.innerHTML = `
    <div class="sec">
      <span class="title">${esc(t("hubq.title"))}</span>
      <span class="pill">${done}/${total}</span>
      <span class="submeta">${src}${failMeta}</span>
      <span class="grow"></span>
      <button class="copy primary" id="queue-copy">${esc(t("hubq.copyDrain"))}</button>
    </div>
    <div class="listcard"><div class="topbar"></div>${items.map(queueRow).join("")}</div>`;
  const btn = document.getElementById("queue-copy");
  if(btn) btn.addEventListener("click", copyDrainCmd);
}

let lastQueue = "";
let lastQueueData = null; // cache so a language switch can repaint without a fetch
async function tickQueue(){
  try {
    const res = await fetch("/queue.json");
    const data = await res.json();
    lastQueueData = data;
    const stamp = JSON.stringify(data);
    if(stamp !== lastQueue){ lastQueue = stamp; renderQueue(data); }
  } catch (e) { /* swallow — independent of /hub.json, retry next tick */ }
}
tickQueue();
setInterval(tickQueue, 3000);
</script>
</body>
</html>"""


class TelemetryForwarder(threading.Thread):
    """Tails each task's telemetry.jsonl and forwards new events to Langfuse.

    Cursor-based (telemetry.cursor records how many lines were shipped); the
    cursor only advances after a 2xx, so delivery is at-least-once and survives
    restarts. Disabled (thread not started) when Langfuse env is absent.
    """

    def __init__(self, workspace, config, interval=5):
        super().__init__(daemon=True)
        self.workspace = workspace
        self.config = config
        self.interval = interval
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _tick(self):
        try:
            slugs = os.listdir(self.workspace.tasks)
        except FileNotFoundError:
            return
        for slug in slugs:
            if not _aipf.safe_slug(slug):
                continue
            self._forward_task(slug)

    def _forward_task(self, slug):
        ws = self.workspace
        tpath = ws.task_file(slug, "telemetry.jsonl")
        if not os.path.isfile(tpath):
            return
        with open(tpath, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        # 1) forward new raw events (structure / timing / outcome).
        cpath = ws.task_file(slug, "telemetry.cursor")
        cursor = ws.read_json(cpath, {"n": 0}).get("n", 0)
        if len(lines) > cursor:
            events = []
            for line in lines[cursor:]:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            if events:
                batch = _aipf.events_to_langfuse_batch(events, slug)
                if not _aipf.post_ingestion(self.config, batch):
                    return  # leave cursor; retry next tick
            ws.write_json(cpath, {"n": len(lines), "ts": now_iso()})
        # 2) enrich generations with token usage once transcripts are ready.
        self._enrich_task(slug, lines)

    def _enrich_task(self, slug, lines):
        """Once a sub-agent's transcript exists, send a generation-update with its
        usage/model/cost. Tracked in telemetry.enriched.json to avoid re-sending."""
        ws = self.workspace
        epath = ws.task_file(slug, "telemetry.enriched.json")
        enriched = set(ws.read_json(epath, {"ids": []}).get("ids", []))
        pending = False  # cheap gate: any ended sub-agent not yet enriched?
        for line in lines:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "subagent.end":
                sid = ev.get("spanId") or ("span-" + (ev.get("toolUseId") or ""))
                if sid and sid not in enriched:
                    pending = True
                    break
        if not pending:
            return
        try:
            trace = _aipf.build_trace(ws.root, slug)
        except Exception:
            return
        items, fresh = _aipf.agent_usage_updates(trace, enriched)
        if items and _aipf.post_ingestion(self.config, items):
            enriched.update(fresh)
            ws.write_json(epath, {"ids": sorted(enriched), "ts": now_iso()})


def write_server_info(workspace, port, pid):
    os.makedirs(workspace.base, exist_ok=True)
    info = {"port": port, "pid": pid, "url": f"http://localhost:{port}",
            "root": os.path.realpath(workspace.root), "ts": now_iso()}
    workspace.write_json(os.path.join(workspace.base, "server.json"), info)
    return info


def process_alive(pid):
    """True if a process with ``pid`` looks alive (conservative on errors).

    Pure/offline: probes the OS, never touches the network. Unknown or
    non-numeric pids are treated as dead. On POSIX uses ``os.kill(pid, 0)``;
    on Windows (where ``os.kill`` cannot send a 0-probe) it asks the kernel via
    ``OpenProcess`` so a genuinely dead pid is reported dead — see
    ``_process_alive_windows``. On any unexpected probe error we stay
    conservative and report alive, so a working server is never thrown away
    over a probe limitation.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _process_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive but owned by another user
    except OSError:
        return True  # unsupported / unknown — don't reject a live server
    return True


def _process_alive_windows(pid):
    """Windows pid liveness via ``OpenProcess`` (ctypes, stdlib only).

    ``os.kill(pid, 0)`` is not a liveness probe on Windows (it would terminate
    the target), so we open the process for a minimal-rights query instead. A
    handle means it exists; failure with ERROR_INVALID_PARAMETER (87) means no
    such process → dead; ERROR_ACCESS_DENIED (5) means it exists but we lack
    rights → alive. Any other failure stays conservative (alive)."""
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_INVALID_PARAMETER = 87
    ERROR_ACCESS_DENIED = 5
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except OSError:
        return True  # ctypes/kernel32 unavailable — don't reject a live server
    if handle:
        kernel32.CloseHandle(handle)
        return True
    err = ctypes.get_last_error()
    if err == ERROR_INVALID_PARAMETER:
        return False  # no process with this pid
    if err == ERROR_ACCESS_DENIED:
        return True   # exists, but not enough rights to open it
    return True       # unknown failure — stay conservative


def read_server_info(workspace):
    """Read ``<base>/server.json`` written by ``write_server_info``.

    Returns the parsed dict, or ``None`` when the file is missing, not valid
    JSON, or not a dict. Never raises — the server must not crash on a stale or
    corrupt file (see conventions.md).
    """
    info = workspace.read_json(
        os.path.join(workspace.base, "server.json"), None)
    return info if isinstance(info, dict) else None


def server_info_is_stale(info, current_port=None):
    """True if ``info`` describes a server that should be replaced.

    Stale when: ``info`` is empty/missing a pid, the recorded pid is not alive,
    or ``current_port`` is given and differs from the recorded port.
    """
    if not info or "pid" not in info:
        return True
    if not process_alive(info.get("pid")):
        return True
    if current_port is not None and info.get("port") != current_port:
        return True
    return False


def server_info_age(info):
    """Seconds since the server.json heartbeat ``ts``, or ``None`` if unparseable.

    ``ts`` is written by :func:`now_iso` in local time. A live server refreshes
    it every ``HEARTBEAT_SECS`` (see :class:`Heartbeat`); a corpse's ``ts`` stops
    advancing. Returns ``None`` when ``ts`` is missing/malformed so callers fall
    back to the pid probe rather than wrongly declaring a server stale.
    """
    ts = (info or {}).get("ts")
    if not ts:
        return None
    try:
        epoch = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None
    return max(0.0, time.time() - epoch)


def server_is_live(info, root=None):
    """True when ``info`` describes a server we should reuse, not replace.

    Live when the recorded pid is alive AND — if a heartbeat ``ts`` is present —
    it is fresh (< ``SERVER_STALE_SECS`` old) AND — if ``root`` is given and the
    file records a ``root`` — the two paths match. Missing fields never *force*
    "live": an unknown heartbeat or unknown root falls back to the pid probe, so
    an old-schema ``server.json`` is treated conservatively. This is the
    idempotency gate: a second launch reuses a live server instead of spawning a
    duplicate (the root cause of the orphan pile-up).
    """
    if not info or not process_alive(info.get("pid")):
        return False
    age = server_info_age(info)
    if age is not None and age > SERVER_STALE_SECS:
        return False  # pid alive but heartbeat stopped — a hung/recycled corpse
    if root is not None and info.get("root") is not None:
        if os.path.realpath(root) != os.path.realpath(info.get("root")):
            return False  # a live server, but serving a different project
    return True


def port_for_root(root):
    """Stable preferred port derived from the project root.

    Keeps a project's dashboard URL from drifting between runs and makes two
    different projects rarely target the same default port (the cross-project
    port-squatting seen in the wild). Uses a salt-free SHA-1 digest because
    Python's built-in ``hash()`` is randomized per process.
    """
    digest = hashlib.sha1(os.path.realpath(root).encode("utf-8")).digest()
    return DEFAULT_PORT + (digest[0] % PORT_SCAN)


class Heartbeat(threading.Thread):
    """Daemon thread that refreshes ``server.json``'s ``ts`` while the server is
    alive, so a reader can tell a live server from a corpse whose pid the OS has
    since recycled. Dies with the process; hiccups never take the server down.
    """

    def __init__(self, workspace, port, pid, interval=HEARTBEAT_SECS):
        super().__init__(daemon=True)
        self.workspace = workspace
        self.port = port
        self.pid = pid
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self.interval):
            try:
                write_server_info(self.workspace, self.port, self.pid)
            except Exception:
                pass  # a heartbeat write must never crash the server

    def stop(self):
        self._stop.set()


def _probe_health(port, timeout=0.3):
    """``GET /health`` on localhost:``port``; parsed dict or ``None`` on any error."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def discover_servers(port_range=None):
    """Probe the local port range for live ai-pathfinder servers.

    Returns a list of ``{port, pid, root}`` for every port that answers
    ``/health`` like one of ours. I/O; the *decision* of what to kill is the
    pure :func:`gc_targets` so it stays unit-testable.
    """
    ports = port_range if port_range is not None else range(
        DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN)
    found = []
    for p in ports:
        h = _probe_health(p)
        if isinstance(h, dict) and h.get("ok") and "pid" in h:
            found.append({"port": p, "pid": h.get("pid"), "root": h.get("root")})
    return found


def gc_targets(servers, root=None, exclude_pid=None):
    """Pure: which discovered servers to reap.

    Keeps a server when (``root`` given) its reported root matches (realpath),
    always dropping ``exclude_pid`` (self). A server with no reported root is
    *not* matched against a specific ``root`` — we never kill an unidentified
    server on someone else's behalf.
    """
    rp = os.path.realpath(root) if root else None
    out = []
    for s in servers:
        if exclude_pid is not None and s.get("pid") == exclude_pid:
            continue
        if rp is not None:
            sr = s.get("root")
            if not sr or os.path.realpath(sr) != rp:
                continue
        out.append(s)
    return out


def reap_servers(root=None, exclude_pid=None, port_range=None):
    """Discover and SIGTERM orphan ai-pathfinder servers (best-effort).

    Returns the list of ``{port, pid, root}`` actually signalled.
    """
    targets = gc_targets(discover_servers(port_range),
                         root=root, exclude_pid=exclude_pid)
    killed = []
    for s in targets:
        try:
            os.kill(int(s["pid"]), signal.SIGTERM)
            killed.append(s)
        except (OSError, ValueError, TypeError):
            pass
    return killed


def await_ports_free(ports, timeout=3.0):
    """Best-effort wait until ``ports`` stop answering ``/health``.

    A predecessor we just SIGTERM'd needs a moment to release its listening
    socket; binding its port before then loses the race and drifts onto another
    (the reserved-preview-port-lands-in-the-default-band bug). Polling /health
    until it goes silent tracks the process actually exiting. Bounded so a wedged
    corpse never hangs startup. No TIME_WAIT concern: listening sockets don't
    enter it, so the port is bindable the instant the holder is gone.
    """
    deadline = time.time() + timeout
    pending = [p for p in ports if p]
    while pending and time.time() < deadline:
        pending = [p for p in pending if _probe_health(p) is not None]
        if pending:
            time.sleep(0.1)


def clear_server_info(workspace, pid):
    """Remove ``server.json`` iff it still records ``pid`` — never clobber a
    successor's pointer when shutting down."""
    try:
        info = read_server_info(workspace)
        if info and info.get("pid") == pid:
            os.remove(os.path.join(workspace.base, "server.json"))
    except OSError:
        pass


def install_shutdown_cleanup(workspace, pid):
    """On graceful exit (return, ``sys.exit``, SIGTERM/SIGINT) drop our own
    ``server.json`` so the next launch never finds a corpse to trip over."""
    atexit.register(clear_server_info, workspace, pid)

    def _on_signal(signum, frame):
        sys.exit(0)  # unwinds serve_forever -> finally + atexit run

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass  # not in the main thread / unsupported — atexit still covers us


class FeedbackServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that fails loudly when a port is already taken.

    HTTPServer sets ``allow_reuse_address = 1`` (SO_REUSEADDR). On POSIX that
    only relaxes TIME_WAIT, but on Windows SO_REUSEADDR lets *two* processes
    bind the **same** port at once — so a second session would silently bind
    8473 instead of letting ``bind()``'s scan move to a free port, and the two
    servers would fight over it (the reported "same port in two terminals"
    bug). Disable reuse on Windows (and request exclusive use) so a taken port
    raises OSError and the scan advances; keep reuse on POSIX so a quick
    restart isn't blocked by TIME_WAIT.
    """

    allow_reuse_address = (os.name != "nt")

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(
                    socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


def bind(workspace, preferred):
    last_err = None
    candidates = [preferred] if preferred else []
    candidates += [DEFAULT_PORT + i for i in range(PORT_SCAN)]
    for port in candidates:
        try:
            httpd = FeedbackServer(("127.0.0.1", port), Handler)
            return httpd, port
        except OSError as e:
            last_err = e
            continue
    raise SystemExit(f"Could not bind a port: {last_err}")


HUB_PAGE = HUB_PAGE.replace("__LOGO_SRC__", LOGO_DATA_URI)
INDEX_LANDING = INDEX_LANDING.replace("__LOGO_SRC__", LOGO_DATA_URI)


def main(argv=None):
    ap = argparse.ArgumentParser(description="ai-pathfinder companion feedback server")
    ap.add_argument("--root", default=os.getcwd(), help="project root (default: cwd)")
    ap.add_argument("--port", type=int, default=0, help="preferred port (0 = auto)")
    ap.add_argument("--open", default="", help="task slug to open in a browser")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    ap.add_argument("--no-forward", action="store_true",
                    help="disable Langfuse telemetry forwarding")
    ap.add_argument("--force", action="store_true",
                    help="start a fresh server even if a live one already serves this root")
    ap.add_argument("--gc", action="store_true",
                    help="reap orphaned ai-pathfinder servers for this root and exit")
    args = ap.parse_args(argv)

    workspace = Workspace(args.root)
    os.makedirs(workspace.tasks, exist_ok=True)
    Handler.workspace = workspace

    if args.gc:
        killed = reap_servers(root=workspace.root, exclude_pid=os.getpid())
        clear_server_info(workspace, (read_server_info(workspace) or {}).get("pid"))
        if killed:
            for s in killed:
                print(f"ai-pathfinder gc: reaped pid={s['pid']} port={s['port']}",
                      flush=True)
        else:
            print("ai-pathfinder gc: no orphan servers for this root", flush=True)
        return

    prev_info = read_server_info(workspace)

    # Idempotent launch: reuse a live server for this root instead of spawning a
    # duplicate. This is the fix for the orphan pile-up — repeated launches (one
    # per session) used to each bind a new port and leave the previous one
    # running forever.
    if not args.force and server_is_live(prev_info, workspace.root):
        print("ai-pathfinder server: reusing live server at "
              f"{prev_info.get('url')} (pid={prev_info.get('pid')}) — "
              "already serving this project", flush=True)
        return

    # Stale/foreign/forced: best-effort reap any live orphan of *this* root, then
    # bind on the project's stable preferred port (scan as fallback).
    preferred = args.port or port_for_root(workspace.root)
    # The default reap scan covers only [DEFAULT_PORT, DEFAULT_PORT+PORT_SCAN).
    # An explicit --port may sit *outside* that band (e.g. the preview harness
    # reserves a rare port), so add it — otherwise a previous server of this
    # root on that exact port is never found and the rebind below would collide.
    reap_range = range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN)
    if preferred not in reap_range:
        reap_range = list(reap_range) + [preferred]
    killed = reap_servers(root=workspace.root, exclude_pid=os.getpid(),
                          port_range=reap_range)
    # Let just-reaped predecessors release their sockets before we rebind, so the
    # bind below lands on our stable/reserved preferred port instead of drifting.
    if killed:
        await_ports_free([s.get("port") for s in killed])

    httpd, port = bind(workspace, preferred)
    Handler.server_port = port
    if server_info_is_stale(prev_info, port):
        print("ai-pathfinder server: stale server.json "
              f"(prev pid={(prev_info or {}).get('pid')}, "
              f"port={(prev_info or {}).get('port')}) — replacing", flush=True)
    info = write_server_info(workspace, port, os.getpid())

    # Keep server.json's heartbeat fresh and drop it on graceful shutdown, so the
    # next launch can always tell a live server from a corpse.
    Heartbeat(workspace, port, os.getpid()).start()
    install_shutdown_cleanup(workspace, os.getpid())

    config = None if args.no_forward else _aipf.langfuse_config_from_env()
    if config:
        TelemetryForwarder(workspace, config).start()
        print(f"ai-pathfinder telemetry: forwarding to {config[2]}", flush=True)
    else:
        print("ai-pathfinder telemetry: local only (set LANGFUSE_* to forward)",
              flush=True)

    url = info["url"]
    if args.open:
        slug = safe_slug(args.open)
        if slug:
            url = f"{info['url']}/?slug={slug}"
            if not args.no_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
    print(f"ai-pathfinder server: {info['url']}  (root={workspace.root})", flush=True)
    if args.open:
        print(f"dashboard: {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
