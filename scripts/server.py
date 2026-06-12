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

# Files inside a task dir that the browser is allowed to GET.
READABLE_FILES = {"index.html", "dashboard.json", "state.json", "replies.json",
                  "reviews.json"}

# Visual-demo mockup files (self-contained HTML/SVG) served from <task>/mockups/.
MOCKUP_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\.(html|svg)$")


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
    def _send(self, code, body=b"", content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
            return self._json(200, {"ok": True, "ts": now_iso()})
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
        return self._send(200, data, ctype)

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

    def _git(self, *args, timeout=10):
        """Run a git command in the project root. Returns (rc, stdout, stderr)."""
        try:
            p = subprocess.run(["git", "-C", self.workspace.root, *args],
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            return p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.SubprocessError) as e:
            return 1, "", str(e)

    def _base_commit(self, slug):
        """The ref the task's diff is measured from (state.baseCommit or HEAD)."""
        state = self.workspace.read_json(
            self.workspace.task_file(slug, "state.json"), {})
        base = state.get("baseCommit") or "HEAD"
        # if the recorded base is no longer a valid commit, fall back to HEAD
        if base != "HEAD" and self._git("cat-file", "-e", base + "^{commit}")[0] != 0:
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
        if self._git("rev-parse", "--is-inside-work-tree")[0] != 0:
            return {"base": None, "files": [], "notGit": True}
        base = self._base_commit(slug)
        files = {}
        # tracked changes with line counts (working tree vs base)
        # quotePath=false gives raw UTF-8 paths (no C-quoting of cyrillic)
        rc, out, _ = self._git("-c", "core.quotePath=false",
                               "diff", "--numstat", base)
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
                               "status", "--porcelain", "--untracked-files=all")
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
                    entry["added"] = self._count_lines(rest)
                    entry["removed"] = 0
        # drop stray noise: empty (0-byte) untracked files like "-" or accidental
        # fragments. Tracked changes and non-empty untracked files are kept.
        kept = [f for f in files.values()
                if not self._is_noise(f["path"], f["status"])]
        return {"base": base, "files": sorted(kept, key=lambda f: f["path"]),
                "notGit": False}

    def _is_noise(self, relpath, status):
        """A stray untracked file worth hiding: an empty (0-byte) new file.
        Conservative — if we cannot stat it, we do NOT treat it as noise."""
        if status != "added":
            return False
        try:
            return os.path.getsize(
                os.path.join(self.workspace.root, relpath)) == 0
        except OSError:
            return False

    def _count_lines(self, relpath):
        try:
            with open(os.path.join(self.workspace.root, relpath),
                      "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except OSError:
            return None

    def _changes_file(self, slug, relpath):
        """Unified diff of one file vs the task base. Read-only, traversal-guarded."""
        if not relpath:
            return {"error": "missing file"}
        root = os.path.realpath(self.workspace.root)
        target = os.path.realpath(os.path.join(root, relpath))
        if os.path.commonpath([target, root]) != root:
            return {"error": "not found"}
        base = self._base_commit(slug)
        rc, out, _ = self._git("diff", base, "--", relpath)
        if rc == 0 and out.strip():
            return {"file": relpath, "diff": out}
        # untracked / new file: diff against an empty blob
        _, out, _ = self._git("diff", "--no-index", "--", os.devnull, relpath)
        return {"file": relpath, "diff": out}

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
<!--TASKS-->
</body>"""


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

    httpd, port = bind(workspace, args.port)
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
