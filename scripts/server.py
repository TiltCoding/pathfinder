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
              "img-src data:; font-src data:; base-uri 'none'; form-action 'none'")
MOCKUP_SEC_HEADERS = {"X-Content-Type-Options": "nosniff",
                      "Content-Security-Policy": MOCKUP_CSP}

# Image attachments served from <task>/attachments/. Server-generated safe name
# (att-<8 hex>.<ext>); the original client filename is carried as metadata only.
# SVG is excluded on purpose (active content — would need the /mockup CSP path).
ATTACH_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}\.(png|jpe?g|gif|webp)$")
ATTACH_MAX_BYTES = 5 * 1024 * 1024     # 5 MB per image
ATTACH_MAX_PER_MSG = 6                  # max images carried on one chat message
# Allow-listed MIME -> saved extension. jpg/jpeg both map to image/jpeg; the
# saved extension for jpeg is "jpg" (matches ATTACH_RE's jpe?g alternative).
ATTACH_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg",
                   "image/gif": "gif", "image/webp": "webp"}
# Reverse map (extension -> content-type) for the serve route.
ATTACH_EXT_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                   "gif": "image/gif", "webp": "image/webp"}


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
        # Per-process temp + retrying replace, shared with _aipf so the two
        # central writers never drift (see _aipf.atomic_temp_name/atomic_replace
        # and write_lang): parallel runs share one store, so a fixed ".tmp" would
        # let concurrent writers collide on the same target.
        tmp = _aipf.atomic_temp_name(path)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
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
    # Per-process temp name: the settings file is global (one per machine), so
    # servers of different project roots may write it concurrently. A shared
    # ".tmp" name would let their byte streams collide; a pid-suffixed temp
    # keeps each writer isolated and os.replace stays atomic (last-writer-wins).
    tmp = path + ".%d.tmp" % os.getpid()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"lang": lang, "ts": now_iso()}, f,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, path)
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
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
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
                                    "root": os.path.realpath(ws.root) if ws else None})
        if path == "/data":
            return self._serve_task_file(slug, "dashboard.json")
        if path == "/mockup":
            return self._serve_mockup(slug, (qs.get("file") or [""])[0])
        if path == "/image":
            return self._serve_image(slug, (qs.get("file") or [""])[0])
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
            return self._json(200, self._chat_get(slug))
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
        if path == "/settings.json":      # global UI language (no slug, no cache)
            return self._json(200, {"lang": read_lang()})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
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
    def _serve_task_file(self, slug, name, content_type="application/json; charset=utf-8"):
        if not slug or name not in READABLE_FILES:
            return self._json(404, {"error": "not found"})
        path = self.workspace.task_file(slug, name)
        if not os.path.isfile(path):
            if name.endswith(".json"):
                return self._json(200, {})
            return self._json(404, {"error": "not found"})
        with open(path, "rb") as f:
            data = f.read()
        return self._send(200, data, content_type)

    def _serve_mockup(self, slug, name):
        """Serve a self-contained visual-demo file from <task>/mockups/.
        Read-only; rendered inside a sandboxed iframe by the dashboard."""
        if not slug or not name or not MOCKUP_RE.match(name):
            return self._json(404, {"error": "not found"})
        mockups = os.path.join(self.workspace.task_dir(slug), "mockups")
        path = os.path.realpath(os.path.join(mockups, name))
        # confine to the mockups dir (defence in depth against traversal)
        if os.path.commonpath([path, os.path.realpath(mockups)]) != os.path.realpath(mockups):
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
        path = os.path.realpath(os.path.join(attach_dir, name))
        # confine to the attachments dir (defence in depth against traversal)
        if os.path.commonpath([path, os.path.realpath(attach_dir)]) != os.path.realpath(attach_dir):
            return self._json(404, {"error": "not found"})
        if not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        ext = name.rsplit(".", 1)[1].lower()
        ctype = ATTACH_EXT_MIME.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        return self._send(200, data, ctype,
                          extra_headers={"X-Content-Type-Options": "nosniff"})

    # ---- trace (computed, not a file) ----------------------------------
    def _trace(self, slug):
        """Build the trace render model with a short mtime-keyed cache so the
        dashboard can poll without re-parsing megabyte transcripts each time."""
        ws = self.workspace
        tpath = ws.task_file(slug, "telemetry.jsonl")
        try:
            mt = os.path.getmtime(tpath)
        except OSError:
            mt = 0
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
        target = os.path.realpath(os.path.join(root, relpath))
        if os.path.commonpath([target, root]) != root:
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
            data = self._build_hub()
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

    def _build_hub(self):
        """Walk _list_tasks(), build a run card per task from state.json /
        dashboard.json plus one light pass over telemetry.jsonl (event counters
        only — no transcripts, no build_trace), classify active vs history, and
        compute cheap cross-task analytics. Everything per-task is wrapped so one
        bad task cannot sink the whole aggregate."""
        now = time.time()
        runs = []
        for slug in self._list_tasks():
            try:
                runs.append(self._hub_run(slug, now))
            except Exception:
                continue  # skip a broken task, keep the rest
        return {"runs": runs, "analytics": self._hub_analytics(runs)}

    def _hub_run(self, slug, now):
        """Build one run card. Fields are sourced from state.json (authoritative
        for phase/iteration/timestamps) and dashboard.json (render model: title,
        status, progress), with a light telemetry pass for activity counters."""
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
        active = self._hub_is_active(phase, updated, now)

        tele = self._hub_telemetry(slug)

        return {
            "slug": slug,
            "title": dash.get("title") or state.get("title") or slug,
            "kind": state.get("kind"),  # e.g. "ask"; None for /feature, /improve
            "phase": phase,
            "status": status,
            "awaiting": (state.get("checkpoint") == "awaiting-batch")
            or (dash.get("status") == "awaiting-batch"),
            "iteration": state.get("iteration"),
            "progress": {"done": done, "total": total},
            "createdAt": created,
            "updatedAt": updated,
            "worktreePath": state.get("worktreePath"),
            "branch": state.get("branch"),
            "active": active,
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
        root = os.path.realpath(self.workspace.root)
        target = os.path.realpath(os.path.join(root, rel))
        if os.path.commonpath([target, root]) != root or not target.endswith(".md"):
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
    def _chat_get(self, slug):
        """Return the task's chat transcript (human + agent turns)."""
        path = self.workspace.task_file(slug, "chat.jsonl")
        msgs = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msgs.append(json.loads(line))
                    except ValueError:
                        pass
        except FileNotFoundError:
            pass
        return {"messages": msgs}

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
        # Safe, unique, server-generated name (no client-controlled separators).
        servername = "att-%s.%s" % (os.urandom(4).hex(), ext)
        if not ATTACH_RE.match(servername):
            return self._json(400, {"ok": False, "error": "name"})
        ws = self.workspace
        ws.ensure_task(slug)
        attach_dir = os.path.join(ws.task_dir(slug), "attachments")
        os.makedirs(attach_dir, exist_ok=True)
        path = os.path.realpath(os.path.join(attach_dir, servername))
        # confine to the attachments dir (defence in depth against traversal)
        if os.path.commonpath([path, os.path.realpath(attach_dir)]) != os.path.realpath(attach_dir):
            return self._json(400, {"ok": False, "error": "name"})
        # Atomic write: temp file then os.replace, under the per-slug lock.
        tmp = path + ".tmp"
        try:
            with ws.lock(slug):
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
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


INDEX_LANDING = """<!doctype html><meta charset=utf-8>
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
<title>ai-pathfinder — runs hub</title>
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
  .brand .logo { width:42px; height:42px; border-radius:9px; flex:none; display:block; }
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
    <!-- brand mark: a lime path threading a maze to an arrow. The maze uses
         currentColor (= --ink) so it inverts with the theme; the path stays lime. -->
    <svg class="logo" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <g stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M11 9 H27"/><path d="M37 9 H53"/>
        <path d="M11 9 V25"/><path d="M11 41 V55"/>
        <path d="M53 9 V25"/><path d="M53 41 V55"/>
        <path d="M11 55 H27"/><path d="M37 55 H53"/>
        <path d="M27 9 V21 H21"/><path d="M37 9 V21 H45"/>
        <path d="M27 55 V43 H21"/><path d="M45 43 H37 V55"/>
        <path d="M32 23 V41"/>
      </g>
      <g fill="currentColor">
        <rect x="16" y="14" width="7" height="7" rx="1.5"/>
        <rect x="41" y="14" width="7" height="7" rx="1.5"/>
        <rect x="16" y="43" width="7" height="7" rx="1.5"/>
        <rect x="41" y="43" width="7" height="7" rx="1.5"/>
      </g>
      <g stroke="#7ed321" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="6" cy="32" r="3.6" stroke-width="3.6"/>
        <path d="M10 32 H25 V24 H40 V40 H55"/>
        <path d="M50 35 L58 40 L50 45"/>
      </g>
    </svg>
    <span class="name">ai-pathfinder</span>
    <span class="tag" id="brand-tag">runs hub</span>
  </div>
  <div class="topright">
    <span class="refresh" id="refresh"><span class="dot"></span><span id="refresh-label">auto-refresh</span></span>
    <span class="sub" id="updated">loading…</span>
    <button class="lang-btn" id="lang-btn" title="Toggle language" aria-label="Toggle language"><span id="lang-label">EN</span></button>
    <button class="theme-btn" id="theme-btn" title="Toggle theme" aria-label="Toggle theme"><span id="theme-icon">☀️</span></button>
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
    "hub.awaiting": "⏳ awaiting reply",
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
    "hub.subtitle": "runs hub",
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
    "hub.awaiting": "⏳ ждёт ответа",
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
    "hub.subtitle": "хаб запусков",
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
  set("brand-tag", t("hub.subtitle"));
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
  var icon = document.getElementById("theme-icon");
  if(icon) icon.textContent = th === "dark" ? "🌙" : "☀️";
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

function render(data){
  const runs = data.runs || [];
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
  document.getElementById("root").innerHTML = `
    <div class="sec"><span class="title">${esc(t("hub.secActive"))}</span>${liveBadge}</div>
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
    reap_servers(root=workspace.root, exclude_pid=os.getpid())

    preferred = args.port or port_for_root(workspace.root)
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
