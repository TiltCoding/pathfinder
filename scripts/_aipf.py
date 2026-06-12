"""Shared helpers for the ai-pathfinder plugin tooling (stdlib only).

Used by both the companion server (`server.py`) and the telemetry hook
(`telemetry_hook.py`) so the two never drift on path layout, atomic writes, or
the Langfuse mapping. No third-party dependencies.
"""

import base64
import glob
import json
import os
import re
import time
import urllib.request
import urllib.error
import uuid

SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def now_iso_utc():
    """ISO-8601 with a trailing Z — what Langfuse ingestion expects."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_slug(slug):
    if not slug or not SLUG_RE.match(slug):
        return None
    if slug in (".", ".."):
        return None
    return slug


# ---- filesystem layout ------------------------------------------------------

def workflow_base(root):
    return os.path.join(os.path.abspath(root), ".workflow")


def tasks_dir(root):
    return os.path.join(workflow_base(root), "tasks")


def task_dir(root, slug):
    return os.path.join(tasks_dir(root), slug)


def task_file(root, slug, name):
    return os.path.join(task_dir(root, slug), name)


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def append_jsonl(path, obj):
    """Append one JSON object as a line. Append is atomic enough for our size."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ---- session -> slug resolution --------------------------------------------

def slug_from_workspace_path(text):
    """Pull a task slug out of a `.workflow/tasks/<slug>/...` reference."""
    if not text:
        return None
    m = re.search(r"\.workflow/tasks/([A-Za-z0-9._-]{1,128})", text)
    return safe_slug(m.group(1)) if m else None


def active_slug(root, session_id=None):
    """Best-effort: which task is this session working on?

    1. `.workflow/active.json` written by the orchestrator (slug, session_id).
    2. Fallback: the most recently updated task `state.json`.
    """
    active = read_json(os.path.join(workflow_base(root), "active.json"), None)
    if isinstance(active, dict):
        slug = safe_slug(active.get("slug", ""))
        # active.json usually has no session_id; accept it, or an exact match.
        if slug and (session_id is None or active.get("session_id") in (None, session_id)):
            return slug
    # fallback: newest state.json
    base = tasks_dir(root)
    best, best_mtime = None, -1
    try:
        for name in os.listdir(base):
            sp = os.path.join(base, name, "state.json")
            try:
                mt = os.path.getmtime(sp)
            except OSError:
                continue
            if mt > best_mtime and safe_slug(name):
                best, best_mtime = safe_slug(name), mt
    except FileNotFoundError:
        pass
    return best


# ---- Langfuse forwarding ----------------------------------------------------

def langfuse_config_from_env(env=None):
    """Return (public_key, secret_key, host) or None if not fully configured."""
    if env is None:
        env = os.environ
    pub = env.get("LANGFUSE_PUBLIC_KEY")
    sec = env.get("LANGFUSE_SECRET_KEY")
    host = env.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not pub or not sec:
        return None
    return (pub, sec, host.rstrip("/"))


def _envelope(item_type, body):
    return {
        "id": str(uuid.uuid4()),
        "type": item_type,
        "timestamp": now_iso_utc(),
        "body": body,
    }


def events_to_langfuse_batch(events, slug):
    """Turn telemetry.jsonl events for one task into Langfuse ingestion items.

    Trace id == slug, so every session/sub-agent of a task lands in one trace
    that survives across `/feature` sessions. Sessions and sub-agents become
    spans; parallel sub-agents are sibling spans (the "branching" view).
    """
    batch = [_envelope("trace-create", {
        "id": slug,
        "name": slug,
        "timestamp": now_iso_utc(),
    })]
    for ev in events:
        kind = ev.get("event")
        ts = ev.get("ts") or now_iso_utc()
        sess = ev.get("session_id")
        sess_span = "sess-" + sess if sess else None
        tags = {k: ev.get(k) for k in ("phase", "iteration", "workstream", "bg")
                if ev.get(k) is not None}
        if kind == "session.start":
            batch.append(_envelope("span-create", {
                "id": sess_span, "traceId": slug, "name": "session",
                "startTime": ts, "metadata": {"source": ev.get("summary"), **tags},
            }))
        elif kind == "session.end":
            if sess_span:
                batch.append(_envelope("span-update", {
                    "id": sess_span, "traceId": slug, "endTime": ts,
                }))
        elif kind == "subagent.start":
            # Sub-agents are LLM calls -> Langfuse generations (carry model/usage/cost).
            span = ev.get("spanId") or ("span-" + (ev.get("toolUseId") or str(uuid.uuid4())))
            batch.append(_envelope("generation-create", {
                "id": span, "traceId": slug, "parentObservationId": sess_span,
                "name": ev.get("role") or "subagent", "startTime": ts,
                "input": ev.get("summary"), "metadata": tags,
            }))
        elif kind == "subagent.end":
            span = ev.get("spanId") or ("span-" + (ev.get("toolUseId") or ""))
            if span and span != "span-":
                batch.append(_envelope("generation-update", {
                    "id": span, "traceId": slug, "endTime": ts,
                    "output": ev.get("summary"),
                    "level": "DEFAULT" if ev.get("ok", True) else "ERROR",
                }))
        elif kind == "file.touch":
            batch.append(_envelope("event-create", {
                "id": str(uuid.uuid4()), "traceId": slug,
                "parentObservationId": sess_span, "name": ev.get("tool") or "edit",
                "startTime": ts, "metadata": {"file": ev.get("file"), **tags},
            }))
        elif kind in ("phase", "gate"):
            batch.append(_envelope("event-create", {
                "id": str(uuid.uuid4()), "traceId": slug,
                "parentObservationId": sess_span,
                "name": kind + ":" + str(ev.get("summary") or ""),
                "startTime": ts, "metadata": tags,
            }))
        # turn.stop and unknown events are intentionally not forwarded (noise).
    return batch


def post_ingestion(config, batch, timeout=10):
    """POST a batch to Langfuse. Returns True on 2xx. Never raises."""
    pub, sec, host = config
    url = host + "/api/public/ingestion"
    data = json.dumps({"batch": batch}).encode("utf-8")
    auth = base64.b64encode(f"{pub}:{sec}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Basic " + auth)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        # 207 multi-status is success-ish; Langfuse uses it for partial.
        return 200 <= e.code < 300 or e.code == 207
    except Exception:
        return False


# ---- transcript mining (tokens / context / duration per sub-agent) ----------
#
# Hook payloads carry no token data, but Claude Code writes per-message usage to
# transcripts on disk: the orchestrator session at
#   ~/.claude/projects/<proj>/<session>.jsonl
# and each sub-agent at
#   ~/.claude/projects/<proj>/<session>/subagents/agent-<agentId>.jsonl
# Every assistant entry has message.usage (input/output/cache tokens), model,
# timestamp, and (for sub-agents) attributionAgent (role). We read ONLY numbers
# — never the prose — and join them with telemetry spans to attribute
# tokens/context/time to each sub-agent run.

CONTEXT_WINDOW = 200000  # token window assumed for the "context fill" bar

# Rough USD per 1M tokens (input, output, cache-write, cache-read). Approximate
# and easy to edit; matched by substring of the model id. Unknown model -> None.
PRICING = {
    "opus":   {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "sonnet": {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.3},
    "haiku":  {"in": 1.0,  "out": 5.0,  "cw": 1.25,  "cr": 0.1},
}


def _projects_dir(projects_dir=None):
    return projects_dir or os.path.expanduser("~/.claude/projects")


def find_main_transcript(session_id, projects_dir=None):
    hits = glob.glob(os.path.join(_projects_dir(projects_dir), "*", session_id + ".jsonl"))
    return hits[0] if hits else None


def find_subagent_files(session_id, projects_dir=None):
    return sorted(glob.glob(os.path.join(
        _projects_dir(projects_dir), "*", session_id, "subagents", "agent-*.jsonl")))


def _ts_to_epoch(ts):
    """Parse an ISO-8601 timestamp (with optional 'Z'/fractional) to epoch secs."""
    if not ts:
        return None
    import datetime
    s = ts.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def estimate_cost(model, fresh_in, out, cache_read, cache_create):
    p = None
    for key, rates in PRICING.items():
        if model and key in model:
            p = rates
            break
    if not p:
        return None
    return round(
        fresh_in / 1e6 * p["in"] + out / 1e6 * p["out"]
        + cache_create / 1e6 * p["cw"] + cache_read / 1e6 * p["cr"], 4)


def parse_transcript_usage(path):
    """Aggregate one transcript file into a per-run usage record (numbers only)."""
    out = fresh_in = cache_read = cache_create = 0
    peak_ctx = 0
    msgs = 0
    models = []
    first = last = None
    role = None
    for line in _iter_lines(path):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("attributionAgent") and not role:
            role = e.get("attributionAgent")
        ts = e.get("timestamp")
        if ts:
            first = first or ts
            last = ts
        if e.get("type") != "assistant":
            continue
        msg = e.get("message") or {}
        u = msg.get("usage") or {}
        if not u:
            continue
        msgs += 1
        m = msg.get("model")
        if m and m not in models:
            models.append(m)
        fi = u.get("input_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        cc = u.get("cache_creation_input_tokens", 0) or 0
        out += u.get("output_tokens", 0) or 0
        fresh_in += fi
        cache_read += cr
        cache_create += cc
        peak_ctx = max(peak_ctx, fi + cr + cc)  # per-message context footprint
    fe, le = _ts_to_epoch(first), _ts_to_epoch(last)
    dur = int((le - fe) * 1000) if (fe and le and le >= fe) else None
    total_in = fresh_in + cache_read + cache_create
    return {
        "role": role, "models": models, "model": models[0] if models else None,
        "msgs": msgs, "out": out, "freshIn": fresh_in,
        "cacheRead": cache_read, "cacheCreate": cache_create,
        "peakContext": peak_ctx, "totalIn": total_in,
        "cacheHitPct": round(100 * cache_read / total_in) if total_in else None,
        "firstTs": first, "lastTs": last, "durationMs": dur,
        "costUsd": estimate_cost(models[0] if models else None,
                                 fresh_in, out, cache_read, cache_create),
    }


def parse_transcript_messages(path):
    """Collect assistant text messages from one transcript (the prose).

    Returns a list of {ts, text} in file order, one per text block of every
    `type=="assistant"` record (message.content blocks with type=="text").
    This is the ONLY function that reads transcript prose, and it is called only
    on an explicit per-agent request (lazy) — never on the hot feed path. Opens
    utf-8 (transcripts are utf-8; console mojibake is a cp1251 stdout issue, not
    file corruption) and is robust to broken lines.
    """
    out = []
    for line in _iter_lines(path):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant":
            continue
        msg = e.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        ts = e.get("timestamp")
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if text:
                out.append({"ts": ts, "text": text})
    return out


def _iter_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                yield line
    except OSError:
        return


def _iter_lines_from(path, offset):
    """Read only the tail of a file past `offset` bytes (cursor reading).

    Reads in binary so the cursor stays a precise byte offset even when values
    contain multi-byte utf-8 (text-mode seek/tell mixes byte size with a text
    cursor and is unreliable there). Returns (lines, new_offset) where
    new_offset points at the start of any not-yet-terminated trailing bytes:
    only complete lines (ending in '\\n') are emitted, and an unterminated tail
    is neither returned nor counted, so it is re-read intact on the next tick
    once the hook finishes appending it -- a completed line is never skipped.
    Robust to an offset past EOF (returns [] and the current size) and to a
    missing file (returns [] and the offset unchanged). Keeps polling
    O(new bytes) instead of O(whole file) as telemetry.jsonl grows.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if offset < 0:
        offset = 0
    if offset >= size:
        return [], size
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        # Nothing complete yet: keep the cursor put so the tail is re-read whole.
        return [], offset
    complete = data[:last_nl + 1]
    new_offset = offset + len(complete)
    lines = [chunk.decode("utf-8", errors="replace")
             for chunk in complete.splitlines()]
    return lines, new_offset


# How tool.* events are surfaced to the live feed. We deliberately do NOT pair
# start/end into spans here: the feed is delta-only (each tick reads just the
# bytes appended since `since_offset`), so the server stays stateless and
# O(new lines). Each tool event becomes a flat record; the client stitches
# start<->end by spanId incrementally and decides `running` itself.

def _feed_lane(ev, active_subagents):
    """Best-effort lane for a tool event (q6).

    A tool event belongs to whichever sub-agent span is currently open in the
    same session (subagent.start seen, no subagent.end yet). If none is open we
    treat it as the orchestrator's own action.
    TODO(q6): same session_id is shared by orchestrator and sub-agents, so when
    several sub-agents run concurrently in one session we cannot tell which one
    a tool event belongs to from telemetry alone; we attribute to the single
    open span if there is exactly one, else fall back to "orchestrator".
    """
    sess = ev.get("session_id")
    open_spans = active_subagents.get(sess) or []
    if len(open_spans) == 1:
        sp = open_spans[0]
        return sp["spanId"], sp.get("role")
    return "orchestrator", None


def build_feed(root, slug, since_offset=0):
    """Lightweight incremental action feed — NEVER reads transcripts.

    Reads only the tail of telemetry.jsonl past `since_offset` (byte cursor)
    and returns the new tool.* events as flat delta records, in file order:

        {spanId, tool, arg, event:"start"|"end", ok, ts, role, lane, session_id}

    Stitching start/end pairs and deriving `running` is done by the client (the
    UI keeps an incremental model keyed by spanId). This keeps the server
    strictly O(new lines) and stateless across ticks — no full model is held in
    memory, which is required by offset reading.

    `lane` groups actions: orchestrator actions land in lane "orchestrator";
    sub-agent tool actions are grouped under the sub-agent's span (best-effort,
    see _feed_lane). Returns {"events", "nextOffset", "generatedAt"}.

    Note: when since_offset == 0 the tail is the whole file, so the subagent
    lane state is fully reconstructed; for since_offset > 0 lane attribution for
    a tool event whose subagent.start fell before the cursor degrades to
    "orchestrator" — acceptable, the client can correct via its own model.
    """
    tpath = task_file(root, slug, "telemetry.jsonl")
    lines, next_offset = _iter_lines_from(tpath, since_offset)
    events = []
    active_subagents = {}  # session_id -> [ {spanId, role} ] currently open
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = e.get("event")
        sess = e.get("session_id")
        if kind == "subagent.start":
            sp = e.get("spanId") or ("span-" + (e.get("toolUseId") or ""))
            active_subagents.setdefault(sess, []).append(
                {"spanId": sp, "role": e.get("role")})
            continue
        if kind == "subagent.end":
            sp = e.get("spanId") or ("span-" + (e.get("toolUseId") or ""))
            lst = active_subagents.get(sess) or []
            active_subagents[sess] = [s for s in lst if s["spanId"] != sp]
            continue
        if kind not in ("tool.start", "tool.end"):
            continue
        lane, role = _feed_lane(e, active_subagents)
        rec = {
            "spanId": e.get("spanId") or ("tool-" + (e.get("toolUseId") or "")),
            "tool": e.get("tool"),
            "event": "start" if kind == "tool.start" else "end",
            "ts": e.get("ts"),
            "role": role,
            "lane": lane,
            "session_id": sess,
        }
        if kind == "tool.start":
            rec["arg"] = e.get("arg")
        else:
            rec["ok"] = e.get("ok", True)
        events.append(rec)
    return {"events": events, "nextOffset": next_offset,
            "generatedAt": now_iso_utc()}


def _spans_from_events(events):
    """Pair subagent.start/end events into spans (one per sub-agent run)."""
    spans = {}
    for ev in events:
        if ev.get("event") not in ("subagent.start", "subagent.end"):
            continue
        sid = ev.get("spanId") or ("span-" + (ev.get("toolUseId") or ""))
        s = spans.setdefault(sid, {"spanId": sid, "session_id": ev.get("session_id")})
        if ev["event"] == "subagent.start":
            s.update(role=ev.get("role"), workstream=ev.get("workstream"),
                     phase=ev.get("phase"), bg=ev.get("bg"),
                     startTs=ev.get("ts"), summary=ev.get("summary"))
        else:
            s.update(endTs=ev.get("ts"), ok=ev.get("ok", True))
            if not s.get("role"):
                s["role"] = ev.get("role")
    return list(spans.values())


def build_trace(root, slug, window=CONTEXT_WINDOW, projects_dir=None):
    """Join telemetry spans (domain structure) with transcript usage (numbers).

    Returns a render model for the dashboard's trace tab. Degrades gracefully:
    spans without a transcript keep null token fields; transcripts without a
    span are still shown (role from attributionAgent).
    """
    events = []
    tpath = task_file(root, slug, "telemetry.jsonl")
    for line in _iter_lines(tpath):
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    session_ids = []
    for ev in events:
        sid = ev.get("session_id")
        if sid and sid not in session_ids:
            session_ids.append(sid)

    spans = _spans_from_events(events)
    agents = []
    sessions = []

    for sid in session_ids:
        sess_spans = [s for s in spans if s.get("session_id") == sid]
        # parse sub-agent transcripts for this session
        parsed = []
        for fp in find_subagent_files(sid, projects_dir):
            u = parse_transcript_usage(fp)
            u["_fe"] = _ts_to_epoch(u["firstTs"])
            parsed.append(u)
        matched = _join_spans_transcripts(sess_spans, parsed, sid)
        agents.extend(matched)

        # orchestrator (main session) transcript as its own row
        main = find_main_transcript(sid, projects_dir)
        if main:
            mu = parse_transcript_usage(main)
            agents.append(_agent_record(
                spanId="orch-" + sid, role="оркестратор", session_id=sid,
                kind="orchestrator", usage=mu, window=window))

        sess_epochs = [e for s in sess_spans
                       for e in (_ts_to_epoch(s.get("startTs")), _ts_to_epoch(s.get("endTs")))
                       if e]
        sessions.append({
            "sessionId": sid,
            "startTs": min((s.get("startTs") for s in sess_spans if s.get("startTs")), default=None),
            "endTs": max((s.get("endTs") for s in sess_spans if s.get("endTs")), default=None),
            "agentsCount": len(sess_spans),
        })

    # timeline bounds + totals
    epochs = []
    for a in agents:
        for t in (a.get("startTs"), a.get("endTs")):
            e = _ts_to_epoch(t)
            if e:
                epochs.append(e)
    totals = {
        "agents": sum(1 for a in agents if a["kind"] == "subagent"),
        "out": sum(a.get("out") or 0 for a in agents),
        "contextPeak": max((a.get("peakContext") or 0 for a in agents), default=0),
        "durationMs": int((max(epochs) - min(epochs)) * 1000) if epochs else None,
        "costUsd": round(sum(a.get("costUsd") or 0 for a in agents), 4),
        "byModel": _totals_by_model(agents),
    }
    return {
        "slug": slug, "generatedAt": now_iso_utc(), "window": window,
        "sessions": sessions, "agents": agents, "totals": totals,
        "timeline": {"t0": min(epochs) if epochs else None,
                     "t1": max(epochs) if epochs else None},
    }


def _agent_record(spanId, role, session_id, kind, usage, window,
                  workstream=None, phase=None, bg=None, ok=True,
                  startTs=None, endTs=None):
    peak = usage.get("peakContext") if usage else None
    return {
        "spanId": spanId, "role": role, "session_id": session_id, "kind": kind,
        "workstream": workstream, "phase": phase, "bg": bg, "ok": ok,
        "startTs": startTs or (usage.get("firstTs") if usage else None),
        "endTs": endTs or (usage.get("lastTs") if usage else None),
        "durationMs": usage.get("durationMs") if usage else None,
        "model": usage.get("model") if usage else None,
        "out": usage.get("out") if usage else None,
        "freshIn": usage.get("freshIn") if usage else None,
        "cacheRead": usage.get("cacheRead") if usage else None,
        "cacheCreate": usage.get("cacheCreate") if usage else None,
        "peakContext": peak,
        "contextPct": round(100 * peak / window) if peak else None,
        "cacheHitPct": usage.get("cacheHitPct") if usage else None,
        "costUsd": usage.get("costUsd") if usage else None,
        "hasUsage": bool(usage and usage.get("msgs")),
    }


def _join_spans_transcripts(spans, parsed, sid, window=CONTEXT_WINDOW):
    """Attach transcript usage to each span. Primary: role match + time overlap.
    Fallback: zip by start order. Spans win for the display role/workstream."""
    spans = sorted(spans, key=lambda s: _ts_to_epoch(s.get("startTs")) or 0)
    pool = sorted([p for p in parsed], key=lambda p: p.get("_fe") or 0)
    used = set()
    records = []

    def take(pred):
        for i, p in enumerate(pool):
            if i in used:
                continue
            if pred(p):
                used.add(i)
                return p
        return None

    for sp in spans:
        s_start = _ts_to_epoch(sp.get("startTs"))
        role = sp.get("role")
        # 1) same role + start within a tolerant window
        u = take(lambda p: role and p.get("role") and _role_match(role, p["role"])
                 and _near(s_start, p.get("_fe")))
        # 2) any unused transcript that started near this span
        if not u:
            u = take(lambda p: _near(s_start, p.get("_fe")))
        records.append(_agent_record(
            spanId=sp.get("spanId"), role=role or (u or {}).get("role") or "subagent",
            session_id=sid, kind="subagent", usage=u, window=window,
            workstream=sp.get("workstream"), phase=sp.get("phase"), bg=sp.get("bg"),
            ok=sp.get("ok", True), startTs=sp.get("startTs"), endTs=sp.get("endTs")))

    # 3) leftover transcripts with no span (e.g. built-in agents) -> zip in
    for i, p in enumerate(pool):
        if i in used:
            continue
        records.append(_agent_record(
            spanId="tx-" + (p.get("firstTs") or str(i)), role=p.get("role") or "subagent",
            session_id=sid, kind="subagent", usage=p, window=window))
    return records


def _role_match(span_role, attribution):
    a, b = (span_role or "").lower(), (attribution or "").lower()
    return a == b or a.endswith(b) or b in a or a.replace("wf-", "") == b


def _near(a, b, tol=120):
    return a is not None and b is not None and abs(a - b) <= tol


def _totals_by_model(agents):
    by = {}
    for a in agents:
        m = a.get("model")
        if not m:
            continue
        d = by.setdefault(m, {"out": 0, "costUsd": 0.0, "agents": 0})
        d["out"] += a.get("out") or 0
        d["costUsd"] = round(d["costUsd"] + (a.get("costUsd") or 0), 4)
        d["agents"] += 1
    return by


def agent_usage_updates(trace, enriched):
    """Langfuse generation-update envelopes that add usage/model/cost to spans.

    Only for sub-agent spans that have real usage and a real span id, and that
    aren't already in `enriched`. Returns (envelopes, newly_enriched_ids).
    """
    items, fresh = [], []
    for a in trace.get("agents", []):
        sid = a.get("spanId")
        if (a.get("kind") != "subagent" or not a.get("hasUsage")
                or not sid or sid in enriched or sid.startswith("tx-")):
            continue
        total_in = (a.get("freshIn") or 0) + (a.get("cacheRead") or 0) + (a.get("cacheCreate") or 0)
        items.append(_envelope("generation-update", {
            "id": sid, "traceId": trace["slug"], "model": a.get("model"),
            "usage": {"input": total_in, "output": a.get("out") or 0,
                      "total": total_in + (a.get("out") or 0), "unit": "TOKENS"},
            "metadata": {"peakContext": a.get("peakContext"),
                         "cacheHitPct": a.get("cacheHitPct"),
                         "costUsd": a.get("costUsd"),
                         "workstream": a.get("workstream"), "phase": a.get("phase")},
        }))
        fresh.append(sid)
    return items, fresh
