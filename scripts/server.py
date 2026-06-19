#!/usr/bin/env python3
"""Companion feedback server for the ai-pathfinder plugin.

Stdlib only (no third-party deps). One server per project root. It serves the
per-task HTML dashboard and provides a tiny JSON API so a human can:

  - accumulate comments on plan blocks and answers to questions (a *draft batch*),
  - send the whole batch to the agent at once ("Отправить агенту на доработку"),
  - approve the plan ("Утвердить план").

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
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _aipf  # noqa: E402  (shared helpers: layout, Langfuse forwarding)

SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DEFAULT_PORT = 8473
PORT_SCAN = 25

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
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


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
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

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
            return self._json(200, {"ok": True, "ts": now_iso(),
                                    "pid": os.getpid(),
                                    "port": getattr(self, "server_port", None)})
        if path == "/data":
            return self._serve_task_file(slug, "dashboard.json")
        if path == "/mockup":
            return self._serve_mockup(slug, (qs.get("file") or [""])[0])
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
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
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
            f'<li><a href="/?slug={t}">{t}</a></li>' for t in tasks) or "<li>пока нет задач</li>"
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
        Изменения tab diff that tree instead of main (see _task_root)."""
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
        if not text:
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
        with ws.lock(slug):
            _aipf.append_jsonl(ws.task_file(slug, "chat.jsonl"), msg)
            self._append_signal(slug, "chat", {"ts": msg["ts"]})
        self._wake(slug)
        return self._json(200, {"ok": True, "message": msg})

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
<p>Сервер запущен. Открывайте дашборд задачи по ссылке вида
<code>/?slug=&lt;task-slug&gt;</code> — её печатает агент при старте задачи.</p>
<p style="font-size:16px"><a href="/hub" style="font-weight:600">Открыть хаб всех запусков → /hub</a></p>
<!--TASKS-->
</body>"""


# The hub page: self-contained (inline <style>+<script>, no CDN). It is an
# evolution of the landing page that fetches /hub.json and renders three
# sections per the approved layout (variant A): Активные cards, История table,
# Аналитика counters+bars. It polls every 3s, diffs on a serialized snapshot
# (like the dashboard's tick()), and swallows errors silently. Visual language
# mirrors templates/dashboard.html (root tokens, phase/status/run-status badges).
HUB_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ai-pathfinder — хаб запусков</title>
<script>
  // theme bootstrap — runs before <style>/paint so there is no flash (FOUC).
  // Same contract as the dashboard: two modes. localStorage['theme'] = 'light'|'dark'
  // (explicit choice); absent (or legacy 'system') = follow the OS. documentElement
  // carries the resolved data-theme ('light'|'dark').
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
  })();
</script>
<style>
  :root[data-theme="light"] {
    --bg:#fbfbfa; --panel:#ffffff; --ink:#1f2328; --muted:#6b7280;
    --line:#e5e7eb; --accent:#4f46e5; --accent-soft:#eef2ff;
    --ok:#16a34a; --warn:#d97706; --chip:#f3f4f6;
    --reply-bg:#f0fdf4; --reply-line:#bbf7d0;
    --shadow:0 1px 3px rgba(0,0,0,.08), 0 8px 24px rgba(0,0,0,.06);
    --err:#ef4444; --err-soft:#fef2f2; --awaiting-soft:#fff7ed;
  }
  :root[data-theme="dark"] {
    --bg:#0f1115; --panel:#181b21; --ink:#e6e8eb; --muted:#9aa3af;
    --line:#2a2f37; --accent:#818cf8; --accent-soft:#1e2230;
    --ok:#16a34a; --warn:#d97706; --chip:#232830;
    --reply-bg:#132019; --reply-line:#1f4d31;
    --shadow:0 1px 3px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
    --err:#f87171; --err-soft:#2a1414; --awaiting-soft:#2a2113;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding-bottom:64px; }
  a { color:var(--accent); }
  .wrap { max-width:1180px; margin:0 auto; padding:24px 20px; }
  header.top { position:sticky; top:0; z-index:20; background:var(--bg);
    border-bottom:1px solid var(--line); padding:14px 20px; }
  .top-inner { max-width:1180px; margin:0 auto; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  h1.title { font-size:18px; margin:0; font-weight:650; flex:1 1 280px; }
  .sub { color:var(--muted); font-size:13px; }
  .badge { font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px; background:var(--chip); white-space:nowrap; }
  .badge.phase { background:var(--accent-soft); color:var(--accent); }
  .badge.kind { background:var(--chip); color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .status { display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px; }
  .status.working { background:var(--accent-soft); color:var(--accent); }
  .status.awaiting { background:var(--awaiting-soft); color:var(--warn); }
  .dot { width:8px; height:8px; border-radius:50%; background:currentColor; }
  .status.working .dot { animation:pulse 1.2s ease-in-out infinite; }
  /* theme toggle — icon button (☀️/🌙), shares localStorage['theme'] with the dashboard */
  .theme-btn { margin-left:auto; display:inline-flex; align-items:center; justify-content:center; min-width:38px;
    background:var(--panel); color:var(--ink); border:1px solid var(--line); border-radius:9px;
    padding:7px 10px; font-size:15px; line-height:1; cursor:pointer; }
  .theme-btn:hover { border-color:var(--accent); }
  @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:.3;} }
  .progress { height:6px; background:var(--line); border-radius:999px; overflow:hidden; margin-top:10px; }
  .progress > div { height:100%; background:var(--accent); transition:width .4s; }
  .run-status { font-size:11px; font-weight:600; padding:2px 9px; border-radius:999px; }
  .run-status.running { background:var(--accent-soft); color:var(--accent); }
  .run-status.done { background:var(--reply-bg); color:var(--ok); }
  .run-status.failed { background:var(--err-soft); color:var(--err); }
  /* extra queue statuses, built from existing tokens (no new palette — ADR-0015) */
  .run-status.pending { background:var(--chip); color:var(--muted); }
  .run-status.skipped { background:var(--awaiting-soft); color:var(--warn); }

  section.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin:16px 0; box-shadow:var(--shadow); }
  section.card > h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:0 0 14px; display:flex; align-items:center; gap:10px; }
  section.card > h2 .count { background:var(--chip); color:var(--ink); border-radius:999px; padding:1px 9px; font-size:12px; }
  .empty { color:var(--muted); font-size:13px; }

  /* hub search + filter toolbar (static node #filter-bar — lives between header
     and #root, never rewritten by render(), so input focus survives the poll).
     Tokens reused from the shared palette (both :root[data-theme]); no new colours. */
  #filter-bar { max-width:1180px; margin:16px auto 0; padding:0 20px; }
  .fpanel { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    box-shadow:var(--shadow); padding:14px 16px; display:flex; flex-direction:column; gap:11px; }
  #f-search { width:100%; background:var(--bg); color:var(--ink); border:1px solid var(--line);
    border-radius:9px; padding:9px 13px; font-size:14px; }
  #f-search:focus { outline:none; border-color:var(--accent); }
  .frow { display:flex; align-items:center; gap:7px; flex-wrap:wrap; }
  .frow .glabel { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; width:42px; flex:none; }
  .chip { font-size:12px; font-weight:600; padding:4px 11px; border-radius:999px; cursor:pointer;
    background:var(--chip); color:var(--muted); border:1px solid transparent; user-select:none; }
  .chip:hover { border-color:var(--accent); }
  .chip.on { background:var(--accent-soft); color:var(--accent); }
  #f-count { display:none; align-items:center; gap:10px; font-size:12px; color:var(--accent);
    background:var(--accent-soft); border-radius:8px; padding:6px 11px; align-self:flex-start; }
  #f-count.on { display:inline-flex; }
  #f-count b { font-weight:700; }
  #f-reset { color:var(--muted); cursor:pointer; text-decoration:underline; font-weight:600; }

  .cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:14px; }
  .runcard { border:1px solid var(--line); border-radius:10px; padding:14px 16px; background:var(--bg); display:flex; flex-direction:column; gap:8px; }
  .runcard.awaiting { border-color:var(--warn); background:var(--awaiting-soft); }
  .runcard .head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .runcard .slug { font-weight:650; font-size:15px; }
  .runcard .desc { color:var(--muted); font-size:13px; }
  .runcard .meta { display:flex; gap:8px; flex-wrap:wrap; font:12px ui-monospace, SFMono-Regular, Menlo, monospace; color:var(--muted); }
  .runcard .meta .git { background:var(--chip); border-radius:6px; padding:1px 7px; color:var(--ink); }
  .runcard .open { align-self:flex-start; color:var(--accent); font-size:13px; text-decoration:none; font-weight:600; }
  .runcard .open:hover { text-decoration:underline; }

  table.hist { width:100%; border-collapse:collapse; font-size:13px; }
  table.hist th { text-align:left; color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; padding:6px 10px; border-bottom:1px solid var(--line); }
  table.hist td { padding:8px 10px; border-bottom:1px solid var(--line); }
  table.hist tr:hover td { background:var(--accent-soft); }
  table.hist a { color:var(--accent); text-decoration:none; font-weight:600; }
  table.hist .mono { font:12px ui-monospace, monospace; color:var(--muted); }

  .stats { display:grid; grid-template-columns:repeat(auto-fill, minmax(160px,1fr)); gap:12px; }
  .stat { border:1px solid var(--line); border-radius:10px; padding:12px 14px; background:var(--bg); }
  .stat .n { font-size:26px; font-weight:700; }
  .stat .l { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
  .phasebars { margin-top:6px; display:flex; flex-direction:column; gap:6px; }
  .phasebar { display:flex; align-items:center; gap:10px; font-size:13px; }
  .phasebar .name { width:120px; color:var(--muted); }
  .phasebar .bar { flex:1; height:8px; background:var(--line); border-radius:999px; overflow:hidden; }
  .phasebar .bar > div { height:100%; background:var(--accent); }
  .phasebar .v { width:28px; text-align:right; color:var(--ink); }

  /* /improve dispatch queue — neutral table section (between active & history) */
  /* #queue-root and #root-tail continue the #root column: drop their vertical
     padding so the card margins stay even (each .card already has margin:16px 0). */
  #queue-root, #root-tail { padding-top:0; padding-bottom:0; }
  #queue-root:empty { display:none; }
  .q-head { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:6px; }
  .q-head .meta { color:var(--muted); font-size:13px; }
  .q-head .progress { flex:1; min-width:160px; max-width:320px; margin-top:0; }
  .q-actions { margin-left:auto; display:flex; gap:8px; }
  button.copy { background:var(--panel); color:var(--ink); border:1px solid var(--line); border-radius:9px; padding:7px 12px; font-weight:600; font-size:13px; cursor:pointer; }
  button.copy:hover { border-color:var(--accent); }
  button.copy.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  code.cmd { font:12px ui-monospace, SFMono-Regular, Menlo, monospace; background:var(--chip); border-radius:6px; padding:1px 7px; color:var(--ink); }
  table.queue { width:100%; border-collapse:collapse; font-size:13px; margin-top:8px; }
  table.queue th { text-align:left; color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; padding:6px 10px; border-bottom:1px solid var(--line); }
  table.queue td { padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:middle; }
  table.queue tr:hover td { background:var(--accent-soft); }
  table.queue .n { font:12px ui-monospace, monospace; color:var(--muted); width:28px; }
  table.queue .ttl { font-weight:600; }
  table.queue .prism { color:var(--muted); font:12px ui-monospace, monospace; }
  table.queue a { color:var(--accent); text-decoration:none; font-weight:600; }
  table.queue tr.failed td { background:var(--err-soft); }
  table.queue tr.skipped td { background:var(--awaiting-soft); }
  table.queue tr.failed:hover td { background:var(--err-soft); }
  table.queue tr.skipped:hover td { background:var(--awaiting-soft); }

  /* toast — ported from templates/dashboard.html */
  .toast { position:fixed; bottom:84px; left:50%; transform:translateX(-50%); background:var(--ink); color:var(--bg); padding:10px 18px; border-radius:10px; font-size:13px; opacity:0; transition:opacity .25s; pointer-events:none; z-index:30; }
  .toast.show { opacity:1; }
</style>
</head>
<body>
<header class="top"><div class="top-inner">
  <h1 class="title">ai-pathfinder — хаб запусков</h1>
  <span class="sub" id="updated">загрузка…</span>
  <button class="theme-btn" id="theme-btn" title="Сменить тему" aria-label="Сменить тему"><span id="theme-icon">☀️</span></button>
</div></header>

<!-- static filter toolbar — sits OUTSIDE #root/#queue-root/#root-tail so render()'s
     innerHTML rewrites never touch it (input focus/caret survive the 3s poll). -->
<div id="filter-bar">
  <div class="fpanel">
    <input id="f-search" type="text" placeholder="Поиск по названию или slug…" autocomplete="off">
    <div class="frow"><span class="glabel">фаза</span><span id="f-phase" class="frow" style="gap:7px"></span></div>
    <div class="frow"><span class="glabel">тип</span><span id="f-kind" class="frow" style="gap:7px"></span></div>
    <span id="f-count">найдено <b id="f-n">0</b> из <span id="f-m">0</span> · фильтр активен <span id="f-reset">сбросить</span></span>
  </div>
</div>

<div class="wrap" id="root">
  <section class="card"><div class="empty">загрузка…</div></section>
</div>
<div class="wrap" id="queue-root"></div>
<div class="wrap" id="root-tail"></div>
<div class="toast" id="toast"></div>

<script>
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c => (
  {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

// --- theme toggle: two modes (light/dark), icon button; shares localStorage['theme']
// with the dashboard. Bootstrap in <head> already set data-theme before paint; here we
// wire the button, persist the explicit choice, and follow the OS until one is made.
const themeMql = window.matchMedia("(prefers-color-scheme: dark)");
function storedTheme(){ var s = localStorage.getItem("theme"); return (s === "light" || s === "dark") ? s : null; }
function resolveTheme(){ return storedTheme() || (themeMql.matches ? "dark" : "light"); }
function applyTheme(){
  var t = resolveTheme();
  document.documentElement.setAttribute("data-theme", t);
  var icon = document.getElementById("theme-icon");
  if(icon) icon.textContent = t === "dark" ? "🌙" : "☀️";
  var btn = document.getElementById("theme-btn");
  if(btn) btn.title = t === "dark" ? "Тёмная тема — переключить на светлую" : "Светлая тема — переключить на тёмную";
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
    return '<span class="status awaiting"><span class="dot"></span>⏳ ждёт ответа</span>';
  return '<span class="status working"><span class="dot"></span>в работе</span>';
}

function fmtDur(ms){
  if(ms==null) return "—";
  const s=Math.round(ms/1000);
  if(s<60) return s+"с";
  const m=Math.floor(s/60);
  if(m<60){ const r=s%60; return r?`${m}м ${r}с`:`${m}м`; }
  const h=Math.floor(m/60), rm=m%60; return rm?`${h}ч ${rm}м`:`${h}ч`;
}

const MONTHS=["янв","фев","мар","апр","мая","июн","июл","авг","сен","окт","ноя","дек"];
function fmtDate(ts){
  if(!ts) return "—";
  const d=new Date(String(ts).replace(" ","T"));
  if(isNaN(d)) return esc(ts);
  const pad=n=>String(n).padStart(2,"0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function progressPct(p){
  if(!p || !p.total) return 0;
  return Math.max(0, Math.min(100, Math.round((p.done||0)/p.total*100)));
}

function runCard(r){
  const pct = progressPct(r.progress);
  const branch = r.branch ? `<span class="git">⎇ ${esc(r.branch)}</span>` : "";
  const wt = r.worktreePath ? `<span>${esc(r.worktreePath)}</span>` : "";
  const meta = (branch||wt) ? `<div class="meta">${branch}${wt}</div>` : "";
  return `<div class="runcard${r.awaiting ? " awaiting" : ""}">
    <div class="head">
      <span class="slug">${esc(r.slug)}</span>
      ${r.kind ? `<span class="badge kind">${esc(r.kind)}</span>` : ""}
      ${r.phase ? `<span class="badge phase">${esc(r.phase)}</span>` : ""}
      ${statusBadge(r.status)}
    </div>
    ${r.title ? `<div class="desc">${esc(r.title)}</div>` : ""}
    <div class="progress"><div style="width:${pct}%"></div></div>
    ${meta}
    <a class="open" href="/?slug=${encodeURIComponent(r.slug)}">открыть дашборд →</a>
  </div>`;
}

function histRow(r){
  const kind = r.kind ? ` <span class="badge kind">${esc(r.kind)}</span>` : "";
  return `<tr>
    <td>${esc(r.slug)}${kind}</td>
    <td><span class="run-status ${runStatusClass(r.phase)}">${esc(r.phase||"—")}</span></td>
    <td class="mono">${r.iteration==null?"—":esc(r.iteration)}</td>
    <td class="mono">${fmtDur(r.durationMs)}</td>
    <td class="mono">${r.subagents==null?"—":esc(r.subagents)}</td>
    <td class="mono">${r.sessions==null?"—":esc(r.sessions)}</td>
    <td class="mono">${fmtDate(r.updatedAt)}</td>
    <td><a href="/?slug=${encodeURIComponent(r.slug)}">открыть</a></td>
  </tr>`;
}

function phaseBars(phases){
  const entries = Object.entries(phases||{});
  if(!entries.length) return "";
  const max = Math.max.apply(null, entries.map(e=>e[1]));
  return entries.map(([name,v])=>{
    const w = max ? Math.round(v/max*100) : 0;
    return `<div class="phasebar"><span class="name">${esc(name)}</span>`+
      `<span class="bar"><div style="width:${w}%"></div></span>`+
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

  // empty text depends on whether a filter is active: "ничего не найдено" when the
  // user filtered everything out, else the default section-empty message.
  const filtered = filterActive();
  const activeEmpty = filtered ? "ничего не найдено" : "нет активных запусков";
  const histEmpty = filtered ? "ничего не найдено" : "история пуста";

  const activeHtml = active.length
    ? `<div class="cards">${active.map(runCard).join("")}</div>`
    : `<div class="empty">${activeEmpty}</div>`;

  const histHtml = history.length
    ? `<table class="hist"><thead><tr>
        <th>Задача</th><th>Фаза</th><th>Итер.</th><th>Длит.</th>
        <th>Под-агенты</th><th>Сессии</th><th>Обновлено</th><th></th>
      </tr></thead><tbody>${history.map(histRow).join("")}</tbody></table>`
    : `<div class="empty">${histEmpty}</div>`;

  const stats = [
    [a.total, "всего задач"],
    [a.active, "активных"],
    [a.done, "завершено"],
    [a.subagents, "под-агентов"],
    [a.sessions, "сессий"],
    [fmtDur(a.medianDurationMs), "медиана длит."],
  ].map(([n,l])=>`<div class="stat"><div class="n">${n==null?"—":esc(n)}</div>`+
    `<div class="l">${esc(l)}</div></div>`).join("");

  // #root holds active runs; #queue-root (filled by tickQueue) sits between;
  // #root-tail holds history + analytics. Splitting keeps the queue section
  // visually between "Активные" and "История" while #root is overwritten every
  // /hub.json diff without ever touching the independent #queue-root node.
  document.getElementById("root").innerHTML = `
    <section class="card">
      <h2>Активные запуски <span class="count">${active.length}</span></h2>
      ${activeHtml}
    </section>`;
  document.getElementById("root-tail").innerHTML = `
    <section class="card">
      <h2>История <span class="count">${history.length}</span></h2>
      ${histHtml}
    </section>
    <section class="card">
      <h2>Обобщённая аналитика</h2>
      <div class="stats">${stats}</div>
      <div class="phasebars" style="margin-top:16px">${phaseBars(a.phases)}</div>
      <p class="sub" style="margin-top:14px">Токены и стоимость не входят в кросс-задачный
      агрегат (дорого и недоступно из worktree) — открываются лениво на дашборде задачи.</p>
    </section>`;
  document.getElementById("updated").textContent =
    "обновлено " + fmtDate(new Date().toISOString()) + " · автообновление";
}

// Build phase/kind chips dynamically from the data (the kind list is {ask,null}
// today, so a hardcoded list would be wrong — q1=A). null kind → synthetic
// KIND_FEAT chip (q2=A). Redraw only when the set of values changes (lastChipKey)
// so the toolbar DOM isn't churned every poll; #f-search is never rewritten.
function chipHtml(value, on){
  return `<span class="chip${on ? " on" : ""}" data-v="${esc(value)}">${esc(value)}</span>`;
}
function buildChips(runs){
  const phases = [...new Set(runs.map(r=>r.phase).filter(Boolean))].sort();
  const kinds = [...new Set(runs.map(r=>r.kind || KIND_FEAT))].sort();
  const key = JSON.stringify([phases, kinds]);
  if(key === lastChipKey) {
    // value sets unchanged — only refresh the .on state (cheap, no input nearby)
    syncChipState();
    return;
  }
  lastChipKey = key;
  const phaseEl = document.getElementById("f-phase");
  const kindEl = document.getElementById("f-kind");
  if(phaseEl) phaseEl.innerHTML = phases.map(p=>chipHtml(p, activePhases.has(p))).join("");
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
  const done = () => toast("Команда скопирована");
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
const QSTATUS_RU = { done:"done", "in-progress":"в работе", pending:"в очереди",
  skipped:"пропущено", failed:"сбой" };
function queueStatusLabel(status){
  const s = String(status||"");
  return QSTATUS_RU[s] || s || "—";
}
function queueRow(it){
  const cls = queueStatusClass(it.status);
  const trCls = (cls === "failed" || cls === "skipped") ? ` class="${cls}"` : "";
  const prism = it.prism ? esc(it.prism) : (it.candId ? esc(it.candId) : "—");
  const link = it.slug
    ? `<a href="/?slug=${encodeURIComponent(it.slug)}">открыть</a>`
    : "";
  return `<tr${trCls}>
    <td class="n">${it.n==null?"":esc(it.n)}</td>
    <td class="ttl">${esc(it.title)}</td>
    <td class="prism">${prism}</td>
    <td><span class="run-status ${cls}">${esc(queueStatusLabel(it.status))}</span></td>
    <td>${link}</td>
  </tr>`;
}
function renderQueue(data){
  const root = document.getElementById("queue-root");
  if(!root) return;
  const items = (data && data.items) || [];
  if(!items.length){ root.innerHTML = ""; return; } // graceful: hide when empty
  const total = items.length;
  const done = items.filter(i => i.status === "done").length;
  const failed = items.filter(i => i.status === "failed").length;
  const pct = total ? Math.round(done/total*100) : 0;
  const src = data.source
    ? `источник <code class="cmd">${esc(data.source)}</code> · ` : "";
  const failMeta = failed ? ` · ${failed} ${failed===1?"сбой":"сбоев"}` : "";
  const meta = `${src}осталось ${total - done}${failMeta}`;
  root.innerHTML = `
    <section class="card">
      <h2>Очередь /improve <span class="count">${done} / ${total}</span></h2>
      <div class="q-head">
        <div class="progress"><div style="width:${pct}%"></div></div>
        <span class="meta">${meta}</span>
        <div class="q-actions">
          <button class="copy primary" id="queue-copy">📋 Копировать команду дренажа</button>
        </div>
      </div>
      <table class="queue">
        <thead><tr><th>#</th><th>Фича</th><th>Призма</th><th>Статус</th><th></th></tr></thead>
        <tbody>${items.map(queueRow).join("")}</tbody>
      </table>
    </section>`;
  const btn = document.getElementById("queue-copy");
  if(btn) btn.addEventListener("click", copyDrainCmd);
}

let lastQueue = "";
async function tickQueue(){
  try {
    const res = await fetch("/queue.json");
    const data = await res.json();
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
            "ts": now_iso()}
    workspace.write_json(os.path.join(workspace.base, "server.json"), info)
    return info


def process_alive(pid):
    """True if a process with ``pid`` looks alive (conservative on errors).

    Pure/offline: uses signal 0 only, never touches the network. Unknown or
    non-numeric pids are treated as dead. On platforms where ``os.kill`` is
    unsupported (e.g. Windows) we stay conservative and report alive so a
    working server is never thrown away over a probe limitation.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive but owned by another user
    except OSError:
        return True  # unsupported / unknown — don't reject a live server
    return True


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
    args = ap.parse_args(argv)

    workspace = Workspace(args.root)
    os.makedirs(workspace.tasks, exist_ok=True)
    Handler.workspace = workspace

    prev_info = read_server_info(workspace)

    httpd, port = bind(workspace, args.port)
    Handler.server_port = port
    if server_info_is_stale(prev_info, port):
        print("ai-pathfinder server: stale server.json "
              f"(prev pid={(prev_info or {}).get('pid')}, "
              f"port={(prev_info or {}).get('port')}) — replacing", flush=True)
    info = write_server_info(workspace, port, os.getpid())

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
