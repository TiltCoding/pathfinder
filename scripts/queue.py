#!/usr/bin/env python3
"""Manage the /improve dispatch queue (`.workflow/dispatch-queue.json`) — stdlib only.

The dispatch queue is the single durable source of truth for the sequential
`/feature` drain of an `/improve` audit (contract: `skills/improve/dispatch-queue.md`).
It survives the `/clear` between features and lives in the shared store under
concurrency (a worktree symlinks its `.workflow` at the main repo's, ADR-0010), so
several sessions may touch it. Until now every status transition
(`pending -> in-progress -> done`) was a hand edit of the JSON by the prompt agent
— no atomic write, no validation. A half-written file then read back as
`{"items": []}` and the drain silently believed the queue was empty.

This CLI gives the queue a real code writer, exactly as `worktree.py` did for the
per-task worktree dance. Every mutation goes through `_aipf.atomic_write`
(per-process temp + retrying replace, ADR-0021), and reads distinguish *missing*
(no queue) from *corrupt* (present but unparseable) instead of collapsing both to
"empty".

Subcommands::

    next                       take the lowest-n pending item -> in-progress
                               (+startedAt) and print its fields (KEY=VALUE)
    done <slug>                mark an item done (+doneAt)
    fail <slug> [--reason R]   mark an item failed (+failReason, +doneAt)
    skip <slug>                mark an item skipped (+doneAt)
    status                     progress N/M + the next pending item (human view)
    append --slug S --feat-id F --brief P [--title T] [--cand-id C] [--prism X]
                               append a new pending item (the /improve writer side)
    validate                   check the queue against the schema; exit 1 on errors

Exit codes: 0 ok · 1 error (corrupt/io/validation) · 2 usage (argparse) ·
3 nothing to do (no pending item / unknown slug).

No third-party dependencies; shares filesystem/atomic-write helpers with the
server and worktree CLI via `_aipf` (same sys.path trick used elsewhere).
"""

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _aipf  # noqa: E402  (shared helpers: layout, atomic write_json, now_iso)

QUEUE_RELPATH = os.path.join(".workflow", "dispatch-queue.json")

# Status lifecycle. Forward-only in normal operation; `requeue`-style reversals
# are a deliberate human action handled elsewhere (not here).
STATUSES = ("pending", "in-progress", "done", "skipped", "failed")
TERMINAL = ("done", "skipped", "failed")

# Top-level + per-item keys required by the writer<->drainer contract
# (dispatch-queue.md). `validate()` pins them so the contract is an executable
# spec, not prose — a drift fails CI (tests/test_dispatch_queue.py).
TOP_REQUIRED = ("version", "source", "mode", "baseCommit", "items")
ITEM_REQUIRED = ("n", "featId", "slug", "briefPath", "status")

# An item is set `in-progress` BEFORE its `/feature` runs; if that session dies
# mid-flight the item sticks there forever and the drain (which picks the lowest-n
# `pending`) skips it, silently losing the feature. The drain is sequential — only
# one item is genuinely running at a time — so an `in-progress` whose `startedAt` is
# older than this threshold is a crashed session, safe to return to `pending`
# (feat-14). Generous default; override with `next --max-running-age`.
DEFAULT_STALE_SECS = 1800


# ---- root / path resolution -------------------------------------------------

def find_root(start=None):
    """Walk up from `start` (cwd) to the first directory that has `.workflow`.

    Works from the main repo and from a worktree alike: a worktree's `.workflow`
    is a symlink at the main store, so the queue resolves to the one shared file
    either way. Falls back to the start dir when no `.workflow` is found."""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, ".workflow")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start or os.getcwd())
        cur = parent


def queue_path(root):
    return os.path.join(os.path.abspath(root), QUEUE_RELPATH)


# ---- load / validate / save -------------------------------------------------

def load_queue(path):
    """Read the queue file, telling *missing* from *corrupt*.

    Returns (data, status) where status is one of:
        "missing"   — no file (a queue was never written / already drained away)
        "corrupt"   — file present but not valid JSON
        "malformed" — valid JSON but not a dict with an `items` list
        "ok"        — a usable queue dict
    `data` is the parsed dict only when status == "ok", else None. This is the
    crux of the reliability fix: a half-written queue must NOT read back as empty.
    """
    if not os.path.exists(path):
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, "corrupt"
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None, "malformed"
    return data, "ok"


def validate(data):
    """Return a list of human-readable schema errors ([] == valid).

    Light but real: top-level shape, per-item required keys, status enum, and a
    dense 1-based `n` sequence in order. `tests/test_dispatch_queue.py` (feat-8)
    imports this so the writer<->drainer contract is pinned by an executable spec
    rather than prose review."""
    errors = []
    if not isinstance(data, dict):
        return ["queue is not a JSON object"]
    for k in TOP_REQUIRED:
        if k not in data:
            errors.append("missing top-level '%s'" % k)
    items = data.get("items")
    if not isinstance(items, list):
        return errors + ["missing or non-list 'items'"]
    seen_slugs = set()
    for i, it in enumerate(items):
        where = "items[%d]" % i
        if not isinstance(it, dict):
            errors.append("%s is not an object" % where)
            continue
        for k in ITEM_REQUIRED:
            if k not in it:
                errors.append("%s missing '%s'" % (where, k))
        st = it.get("status")
        if st is not None and st not in STATUSES:
            errors.append("%s status %r not in %r" % (where, st, list(STATUSES)))
        if it.get("n") != i + 1:
            errors.append("%s 'n'=%r breaks the dense 1-based order (expected %d)"
                          % (where, it.get("n"), i + 1))
        slug = it.get("slug")
        if slug in seen_slugs:
            errors.append("%s duplicate slug %r" % (where, slug))
        seen_slugs.add(slug)
    return errors


def _save(path, data):
    """Stamp updatedAt and write the queue atomically (ADR-0021)."""
    data["updatedAt"] = _aipf.now_iso()
    _aipf.write_json(path, data)


def _quarantine_corrupt(path):
    """Move an unparseable queue aside to `dispatch-queue.json.corrupt-<TS>`.

    Mirrors the state.json recovery in `worktree.py` (state-json-corrupt-recovery):
    never silently overwrite or drop a queue we could not read — a JSONDecodeError
    usually means a writer's mutation was half-written or an agent edit clobbered
    it, and treating it as an empty/absent queue would lose the whole drain. We
    rename the byte-for-byte original aside (visible warning) so a human can
    recover it. `<TS>` = local time + pid with NO colons (NTFS forbids ':' in
    names). Returns the quarantine basename, or None if the move failed."""
    ts = time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid())
    dest = path + ".corrupt-" + ts
    try:
        os.replace(path, dest)
    except OSError:
        return None
    return os.path.basename(dest)


def _load_or_die(path):
    """Load for a mutating command; print a clear reason and exit on trouble.

    Returns the queue dict, or calls sys.exit with a non-zero code. A *corrupt*
    queue is never treated as empty — it is quarantined aside (so it doesn't
    masquerade as a drained queue) and the command fails loud."""
    data, status = load_queue(path)
    if status == "missing":
        print("no dispatch queue at " + path + " (nothing queued)",
              file=sys.stderr)
        raise SystemExit(3)
    if status in ("corrupt", "malformed"):
        name = _quarantine_corrupt(path)
        if name:
            print("error: dispatch queue was " + status + " (NOT empty) — "
                  "quarantined as " + name + " at " + os.path.dirname(path)
                  + "\n  a broken queue is not a drained one; recover it or "
                  "re-run /improve DISPATCH to rewrite the queue.",
                  file=sys.stderr)
        else:
            print("error: dispatch queue is " + status + " (NOT empty) at "
                  + path + " and could not be quarantined — fix it by hand.",
                  file=sys.stderr)
        raise SystemExit(1)
    return data


def _find_item(data, slug):
    for it in data.get("items", []):
        if it.get("slug") == slug:
            return it
    return None


def _age_secs(iso):
    """Seconds since an ISO-8601 local timestamp (as written by `_aipf.now_iso`),
    or None if absent/unparseable. Naive local time on both sides."""
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso))
    except (ValueError, TypeError):
        return None
    return (datetime.datetime.now() - dt).total_seconds()


def _recover_stale(data, max_age):
    """Return any `in-progress` item older than `max_age` to `pending` so a crashed
    drain doesn't lose it (feat-14). Tags it `resumedFrom:"in-progress"` for
    visibility and clears `startedAt`. Returns the list of recovered slugs (caller
    persists). A None/unparseable `startedAt` is left alone (can't judge its age)."""
    recovered = []
    for it in data.get("items", []):
        if it.get("status") != "in-progress":
            continue
        age = _age_secs(it.get("startedAt"))
        if age is not None and age > max_age:
            it["status"] = "pending"
            it["resumedFrom"] = "in-progress"
            it["startedAt"] = None
            recovered.append(it.get("slug"))
    return recovered


def _print_item_fields(data, it):
    """Emit a drained item's fields as KEY=VALUE lines (machine-parseable)."""
    out = {
        "n": it.get("n"),
        "slug": it.get("slug"),
        "featId": it.get("featId"),
        "candId": it.get("candId"),
        "title": it.get("title"),
        "briefPath": it.get("briefPath"),
        "baseCommit": data.get("baseCommit"),
        "autonomous": "true" if data.get("autonomous") else "false",
    }
    for k, v in out.items():
        print("%s=%s" % (k, "" if v is None else v))


# ---- subcommands ------------------------------------------------------------

def cmd_next(args):
    path = queue_path(args.root or find_root())
    data = _load_or_die(path)
    # step 0: self-heal — return any crashed (stale in-progress) item to pending
    # so the sequential drain resumes it instead of losing it (feat-14).
    max_age = getattr(args, "max_running_age", DEFAULT_STALE_SECS)
    recovered = _recover_stale(data, max_age)
    pending = [it for it in data["items"] if it.get("status") == "pending"]
    if not pending:
        if recovered:
            _save(path, data)
        print("no pending items (queue drained or all in-progress/terminal)",
              file=sys.stderr)
        return 3
    it = min(pending, key=lambda x: x.get("n", 1 << 30))
    it["status"] = "in-progress"
    it["startedAt"] = _aipf.now_iso()
    _save(path, data)
    if recovered:
        print("recovered %d stale in-progress -> pending: %s"
              % (len(recovered), ", ".join(recovered)), file=sys.stderr)
    _print_item_fields(data, it)
    return 0


def cmd_recover(args):
    path = queue_path(args.root or find_root())
    data = _load_or_die(path)
    recovered = _recover_stale(data, args.age)
    if recovered:
        _save(path, data)
        print("recovered %d stale in-progress -> pending: %s"
              % (len(recovered), ", ".join(recovered)))
    else:
        print("no stale in-progress items (none older than %gs)" % args.age)
    return 0


def _mark(args, new_status, reason=None):
    path = queue_path(args.root or find_root())
    data = _load_or_die(path)
    it = _find_item(data, args.slug)
    if it is None:
        print("error: no item with slug %r in the queue" % args.slug,
              file=sys.stderr)
        return 3
    it["status"] = new_status
    it["doneAt"] = _aipf.now_iso()
    if reason is not None:
        it["failReason"] = reason
    _save(path, data)
    print("%s -> %s" % (args.slug, new_status))
    return 0


def cmd_done(args):
    return _mark(args, "done")


def cmd_fail(args):
    return _mark(args, "failed", reason=args.reason or "")


def cmd_skip(args):
    return _mark(args, "skipped")


def cmd_status(args):
    path = queue_path(args.root or find_root())
    data, status = load_queue(path)
    if status == "missing":
        print("no dispatch queue at " + path)
        return 0
    if status in ("corrupt", "malformed"):
        print("dispatch queue is " + status.upper() + " at " + path
              + " (NOT empty — fix the file)")
        return 1
    items = data["items"]
    done = sum(1 for it in items if it.get("status") == "done")
    total = len(items)
    auto = "yes" if data.get("autonomous") else "no"
    base = (data.get("baseCommit") or "")[:7]
    print("dispatch queue: %d/%d done · autonomous=%s · base=%s · %s"
          % (done, total, auto, base, data.get("improveSlug", "")))
    for it in items:
        print("  %2s  %-12s %s" % (it.get("n", "?"), it.get("status", "?"),
                                   it.get("slug", "?")))
    nxt = min((it for it in items if it.get("status") == "pending"),
              key=lambda x: x.get("n", 1 << 30), default=None)
    if nxt:
        print("next pending: %s  (%s)" % (nxt.get("slug"), nxt.get("briefPath")))
    else:
        print("next pending: (none — queue drained)")
    return 0


def cmd_append(args):
    path = queue_path(args.root or find_root())
    data, status = load_queue(path)
    if status == "missing":
        data = {"version": 1, "source": "improve-runtime",
                "mode": "sequential-feature", "baseCommit": None,
                "createdAt": _aipf.now_iso(), "items": []}
    elif status in ("corrupt", "malformed"):
        print("error: refusing to append to a " + status + " queue at " + path,
              file=sys.stderr)
        return 1
    n = max((it.get("n", 0) for it in data["items"]), default=0) + 1
    data["items"].append({
        "n": n, "featId": args.feat_id, "slug": args.slug,
        "title": args.title or "", "candId": args.cand_id or "",
        "prism": args.prism or "", "briefPath": args.brief,
        "status": "pending", "startedAt": None, "doneAt": None,
    })
    _save(path, data)
    print("appended n=%d slug=%s" % (n, args.slug))
    return 0


def cmd_validate(args):
    path = queue_path(args.root or find_root())
    data, status = load_queue(path)
    if status == "missing":
        print("no dispatch queue at " + path)
        return 0
    if status in ("corrupt", "malformed"):
        print("INVALID: queue is " + status + " at " + path)
        return 1
    errors = validate(data)
    if errors:
        print("INVALID (%d):" % len(errors))
        for e in errors:
            print("  - " + e)
        return 1
    print("valid: %d items" % len(data["items"]))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="queue.py",
        description="Manage the /improve dispatch queue (atomic, stdlib only).")
    p.add_argument("--root", default=None,
                   help="project root (default: walk up to the nearest .workflow)")
    sub = p.add_subparsers(dest="cmd")

    n = sub.add_parser("next", help="take the lowest-n pending item -> in-progress")
    n.add_argument("--max-running-age", dest="max_running_age", type=float,
                   default=DEFAULT_STALE_SECS,
                   help="seconds after which a stale in-progress item is recovered "
                        "to pending before picking (default %d)" % DEFAULT_STALE_SECS)
    n.set_defaults(func=cmd_next)

    rc = sub.add_parser("recover",
                        help="return stale in-progress items (crashed drains) to pending")
    rc.add_argument("--age", type=float, default=DEFAULT_STALE_SECS,
                    help="staleness threshold in seconds (default %d)" % DEFAULT_STALE_SECS)
    rc.set_defaults(func=cmd_recover)

    d = sub.add_parser("done", help="mark an item done")
    d.add_argument("slug")
    d.set_defaults(func=cmd_done)

    f = sub.add_parser("fail", help="mark an item failed")
    f.add_argument("slug")
    f.add_argument("--reason", default=None, help="why it failed (-> failReason)")
    f.set_defaults(func=cmd_fail)

    s = sub.add_parser("skip", help="mark an item skipped")
    s.add_argument("slug")
    s.set_defaults(func=cmd_skip)

    st = sub.add_parser("status", help="progress + next pending item")
    st.set_defaults(func=cmd_status)

    a = sub.add_parser("append", help="append a new pending item")
    a.add_argument("--slug", required=True)
    a.add_argument("--feat-id", required=True, dest="feat_id")
    a.add_argument("--brief", required=True, help="briefPath")
    a.add_argument("--title", default=None)
    a.add_argument("--cand-id", default=None, dest="cand_id")
    a.add_argument("--prism", default=None)
    a.set_defaults(func=cmd_append)

    v = sub.add_parser("validate", help="check the queue against the schema")
    v.set_defaults(func=cmd_validate)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
