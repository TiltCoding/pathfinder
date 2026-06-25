#!/usr/bin/env python3
"""Manage per-task git worktrees for parallel ai-pathfinder runs (stdlib only).

The companion server is one-per-project and reads a single shared store at
`<main repo>/.workflow/`. To run a task in parallel with another already in
flight, the orchestrator stands the task up in its own git worktree (own branch,
own working files) while its artifacts still land in the one shared store. This
helper does that multi-step, idempotent dance so the skill stays high-level:

    git worktree add  +  symlink <worktree>/.workflow -> <main>/.workflow
                      +  record worktreePath/branch in the task's state.json

Subcommands:
    add <slug> [--base main] [--branch <name>]   create/resume the worktree
    list                                          show worktrees vs state.json
    remove <slug> [--force]                       drop the worktree (keep history)

The worktree lives as a sibling of the repo at
`../pathfinder-worktrees/<slug>/`, so it never lands inside the main work tree
(no .gitignore churn). Branch defaults to <slug>, base to the main repo's
current branch (else main). Everything is idempotent: an existing worktree or
branch is reused (resume), never fatal.

No third-party dependencies; shares filesystem/layout helpers with the server
and hook via `_aipf` (same sys.path trick used by server.py / tests/).
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _aipf  # noqa: E402  (shared helpers: layout, read/write_json, now_iso_utc)

WORKTREES_DIRNAME = "pathfinder-worktrees"


# ---- git plumbing -----------------------------------------------------------

def _git(*args, cwd=None, timeout=30):
    """Run a git command. Returns (rc, stdout, stderr); never raises."""
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8",
                           errors="replace")
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def main_root(start=None):
    """The main repository root, even when called from inside a worktree.

    `git rev-parse --git-common-dir` points at the shared `.git` dir (the main
    repo's `.git`, identical from every linked worktree); its parent is the main
    work tree. Returns an absolute path, or None if not in a git repo.
    """
    rc, out, _ = _git("rev-parse", "--git-common-dir", cwd=start)
    if rc != 0:
        return None
    common = out.strip()
    if not common:
        return None
    # Resolve relative to the start cwd (git may print a relative ".git").
    common = os.path.abspath(os.path.join(start or os.getcwd(), common))
    return os.path.dirname(common)


def worktree_dir(root, slug):
    """Sibling directory `<parent of root>/pathfinder-worktrees/<slug>`."""
    parent = os.path.dirname(os.path.abspath(root))
    return os.path.join(parent, WORKTREES_DIRNAME, slug)


def list_worktrees(root):
    """Parse `git worktree list --porcelain` into a list of dicts.

    Each entry: {path, branch, head}. `branch` is the short ref name (no
    `refs/heads/`) or None for a detached HEAD. Robust to git failure (-> [])."""
    rc, out, _ = _git("worktree", "list", "--porcelain", cwd=root)
    if rc != 0:
        return []
    entries = []
    cur = {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": os.path.abspath(val), "branch": None, "head": None}
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val[len("refs/heads/"):] if val.startswith(
                "refs/heads/") else val
        elif key == "detached":
            cur["branch"] = None
    if cur:
        entries.append(cur)
    return entries


def branch_exists(root, branch):
    rc, _, _ = _git("rev-parse", "--verify", "--quiet",
                    "refs/heads/" + branch, cwd=root)
    return rc == 0


def default_base(root):
    """Base ref for a new worktree branch when --base is not given.

    The main repo's default branch is not always `main` (master/develop exist),
    so use the main repo's *current* branch. Falls back to `main` if HEAD is
    detached or unreadable, then to the detached HEAD sha so `add` still works on
    a checkout that has no branch at all. Never raises."""
    rc, out, _ = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    branch = out.strip() if rc == 0 else ""
    if branch and branch != "HEAD":
        return branch
    if branch_exists(root, "main"):
        return "main"
    rc, out, _ = _git("rev-parse", "HEAD", cwd=root)
    head = out.strip() if rc == 0 else ""
    return head or "main"


# ---- state.json wiring (pure, testable without real git) --------------------

def record_worktree_in_state(state, worktree_path, branch):
    """Return `state` with worktreePath/branch set (append-only, idempotent).

    Pure: no I/O, no git. The caller reads/writes state.json around it so the
    write path can be unit-tested without an actual worktree. Mutates and
    returns the same dict; absent fields are added, existing ones overwritten
    with the resolved values (a resume rewrites them with the same path).
    """
    if not isinstance(state, dict):
        state = {}
    state["worktreePath"] = os.path.abspath(worktree_path)
    state["branch"] = branch
    # local time without 'Z', to match every other state.json updatedAt write
    state["updatedAt"] = _aipf.now_iso()
    return state


def _quarantine_corrupt_state(spath):
    """Move an unparseable state.json aside to `state.json.corrupt-<TS>`.

    Never silently overwrite a state.json we couldn't read: a JSONDecodeError
    there usually means the orchestrator's INTAKE write (phase/iteration/
    dispatched/questions) is half-written or clobbered, and minting a fresh
    minimal state on top would lose it. We rename the byte-for-byte original
    aside instead, then let the caller mint a fresh one.

    `<TS>` = local time + pid with NO colons (NTFS forbids ':' in names; that
    is also why `now_iso` is unusable here). `os.replace` is atomic and works
    cross-platform. Returns the quarantine basename, or None if the move failed
    (in which case the caller proceeds to overwrite — losing a file we already
    can't read is the lesser evil than aborting `add`)."""
    ts = time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid())
    dest = spath + ".corrupt-" + ts
    try:
        os.replace(spath, dest)
    except OSError:
        return None
    return os.path.basename(dest)


def _write_state_fields(root, slug, worktree_path, branch):
    """Read the task's state.json, set worktreePath/branch, write it back.

    Tolerant: if state.json is missing we create a minimal one (the
    orchestrator usually writes it in INTAKE, but `add` may run first on a
    resume). A *corrupt* state.json (present but unparseable) is NOT silently
    overwritten — it is quarantined to `state.json.corrupt-<TS>` with a visible
    warning, then a fresh minimal one is minted (see _quarantine_corrupt_state).

    Returns (state, status) where status is one of:
        "created"     — file was missing, we minted a minimal one
        "recovered"   — file was corrupt, quarantined, minted a minimal one
        "overwritten" — file was corrupt but quarantine failed; we had to
                        overwrite it (a visible stderr warning was printed)
        "merged"      — file was a valid dict, worktree fields merged in
    """
    spath = _aipf.task_file(root, slug, "state.json")
    # os.path.exists BEFORE the read so we can tell "missing" (no file) from
    # "corrupt" (file there, but read_json's graceful default kicked in). This
    # keeps read_json's contract untouched (its default is relied on elsewhere,
    # e.g. test_corrupt_json_is_graceful).
    existed = os.path.exists(spath)
    existing = _aipf.read_json(spath, None)
    if isinstance(existing, dict):
        status = "merged"
        state = existing
    elif existed:
        # present but unparseable (or non-dict json) -> quarantine, don't clobber
        name = _quarantine_corrupt_state(spath)
        if name:
            print("warning: битый state.json сохранён как " + name
                  + ", создаю новый", file=sys.stderr)
            status = "recovered"
        else:
            # quarantine провалился — файл будет перезаписан, сохранить его не
            # удалось. Честно скажем об этом, чтобы cmd_add не утверждал ниже,
            # будто оригинал лежит рядом как state.json.corrupt-*.
            print("warning: битый state.json не удалось сохранить (" + spath
                  + "), файл перезаписан", file=sys.stderr)
            status = "overwritten"
        state = {}
        state.setdefault("slug", slug)
    else:
        status = "created"
        state = {}
        state.setdefault("slug", slug)
    state = record_worktree_in_state(state, worktree_path, branch)
    _aipf.write_json(spath, state)
    return state, status


def _clear_state_worktree_fields(root, slug):
    """Drop worktreePath/branch from the task's state.json after a remove.

    Leaves a stale pointer (worktreePath) and branch off the hub card, which
    would otherwise diff against a tree that no longer exists. History stays
    untouched (the task dir is kept). Tolerant: a missing/corrupt state.json is
    a no-op and never raises. Returns True if it wrote a change."""
    spath = _aipf.task_file(root, slug, "state.json")
    state = _aipf.read_json(spath, None)
    if not isinstance(state, dict):
        return False
    if "worktreePath" not in state and "branch" not in state:
        return False
    state.pop("worktreePath", None)
    state.pop("branch", None)
    state["updatedAt"] = _aipf.now_iso()
    try:
        _aipf.write_json(spath, state)
    except OSError:
        return False
    return True


def _ensure_workflow_symlink(main_root_, wt_dir):
    """Symlink `<worktree>/.workflow -> <main>/.workflow` (shared store).

    Only creates it when absent. If a correct symlink is already there, it is
    left alone (idempotent resume). Returns a human-readable status string;
    never raises — a failed symlink must not abort the whole `add`.
    """
    target = _aipf.workflow_base(main_root_)
    link = os.path.join(wt_dir, ".workflow")
    try:
        if os.path.islink(link):
            if os.path.abspath(os.path.realpath(link)) == os.path.abspath(target):
                return "symlink ok (already points at shared store)"
            return ("warning: " + link + " is a symlink to "
                    + os.path.realpath(link) + ", not the shared store")
        if os.path.exists(link):
            return ("warning: " + link
                    + " already exists and is not a symlink; left as-is")
        os.symlink(target, link)
        return "symlink created -> " + target
    except OSError as e:
        return "warning: could not create symlink (" + str(e) + ")"


def _ensure_git_exclude(root, pattern=".workflow"):
    """Ensure `pattern` is in the shared git exclude (<git-common-dir>/info/exclude).

    The `.workflow` symlink we create in each worktree is not matched by the
    committed `.gitignore` rule `.workflow/` (a trailing slash matches a
    directory, not a symlink), so without this it surfaces as a phantom
    untracked entry in the task's «Изменения» tab. `info/exclude` lives in the
    *common* git dir, so a single line covers every linked worktree immediately,
    regardless of which commit each worktree has checked out. Idempotent and
    best-effort: never raises — failing to write it only reintroduces cosmetic
    diff noise, it must not abort `add`.
    """
    rc, out, _ = _git("rev-parse", "--git-common-dir", cwd=root)
    if rc != 0 or not out.strip():
        return
    common = os.path.abspath(os.path.join(root, out.strip()))
    info = os.path.join(common, "info")
    exclude = os.path.join(info, "exclude")
    try:
        # never follow a symlink here: a planted `exclude` symlink would make the
        # append clobber an arbitrary file. Bailing only reintroduces cosmetic
        # diff noise, so it is safe to skip.
        if os.path.islink(exclude):
            return
        existing = ""
        if os.path.isfile(exclude):
            with open(exclude, "r", encoding="utf-8") as f:
                existing = f.read()
        if any(line.strip() == pattern for line in existing.splitlines()):
            return  # already excluded
        os.makedirs(info, exist_ok=True)
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        with open(exclude, "a", encoding="utf-8") as f:
            f.write(sep + pattern + "\n")
    except OSError:
        return


# ---- subcommands ------------------------------------------------------------

def cmd_add(args):
    root = main_root()
    if not root:
        print("error: not inside a git repository", file=sys.stderr)
        return 1
    slug = _aipf.safe_slug(args.slug)
    if not slug:
        print("error: invalid slug", file=sys.stderr)
        return 1
    branch = args.branch or slug
    base = args.base or default_base(root)
    wt_dir = worktree_dir(root, slug)

    # Idempotency: reuse an existing worktree at this path (resume), and reuse
    # an existing branch instead of trying to create it again.
    existing = next((w for w in list_worktrees(root)
                     if os.path.abspath(w["path"]) == os.path.abspath(wt_dir)),
                    None)
    if existing:
        print("worktree already exists, reusing: " + wt_dir)
        if existing.get("branch"):
            branch = existing["branch"]
    else:
        os.makedirs(os.path.dirname(wt_dir), exist_ok=True)
        if branch_exists(root, branch):
            # Branch is already there -> attach without -b (it may be checked
            # out elsewhere; git will refuse that, which we surface plainly).
            rc, out, err = _git("worktree", "add", wt_dir, branch, cwd=root)
        else:
            rc, out, err = _git("worktree", "add", wt_dir, "-b", branch, base,
                                cwd=root)
        if rc != 0:
            print("error: git worktree add failed:\n" + (err or out).strip(),
                  file=sys.stderr)
            return 1
        print("worktree created: " + wt_dir + " (branch " + branch + ")")

    sym = _ensure_workflow_symlink(root, wt_dir)
    print(sym)
    # Keep the .workflow symlink out of the worktree's diff (shared exclude).
    _ensure_git_exclude(root)

    _, status = _write_state_fields(root, slug, wt_dir, branch)
    if status == "created":
        print("note: state.json was missing -> created a minimal one "
              "(the orchestrator normally writes it in INTAKE)")
    elif status == "recovered":
        print("note: state.json was corrupt -> quarantined as "
              "state.json.corrupt-* and minted a minimal one "
              "(rerun the orchestrator's INTAKE to restore phase/iteration)")
    elif status == "overwritten":
        print("note: state.json was corrupt and could NOT be quarantined -> "
              "overwritten with a minimal one (the original was lost; see the "
              "warning above; rerun the orchestrator's INTAKE to restore "
              "phase/iteration)")
    print("recorded worktreePath/branch in "
          + _aipf.task_file(root, slug, "state.json"))

    print("")
    print("Run the task session there, e.g.:")
    print("  cd " + wt_dir)
    print("  # then start /feature for slug '" + slug + "' in that directory")
    print("The companion server is one-per-project; the hub is at /hub.")
    return 0


def cmd_list(args):
    root = main_root()
    if not root:
        print("error: not inside a git repository", file=sys.stderr)
        return 1
    worktrees = list_worktrees(root)
    # Map worktree path -> entry for cross-checking against state.json below.
    by_path = {os.path.abspath(w["path"]): w for w in worktrees}

    rows = []
    tasks = _aipf.tasks_dir(root)
    try:
        names = sorted(os.listdir(tasks))
    except OSError:
        names = []
    for name in names:
        slug = _aipf.safe_slug(name)
        if not slug or not os.path.isdir(_aipf.task_dir(root, slug)):
            continue  # skip stray files like .DS_Store
        state = _aipf.read_json(_aipf.task_file(root, slug, "state.json"), {})
        if not isinstance(state, dict):
            continue
        wt = state.get("worktreePath")
        if not wt:
            continue  # ordinary (non-worktree) task — not part of this list
        wt_abs = os.path.abspath(wt)
        rows.append((slug, state.get("branch") or "-", wt_abs,
                     "yes" if os.path.isdir(wt_abs) else "no",
                     "yes" if wt_abs in by_path else "no"))

    if not rows:
        print("No worktree-backed tasks recorded in "
              + _aipf.tasks_dir(root) + ".")
        if worktrees:
            print("(git worktree list shows " + str(len(worktrees))
                  + " worktree(s), incl. the main one.)")
        return 0

    # Plain aligned table (no third-party formatting).
    headers = ("slug", "branch", "path", "dir", "git")
    widths = [max(len(h), *(len(str(r[i])) for r in rows))
              for i, h in enumerate(headers)]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))
    return 0


def cmd_remove(args):
    root = main_root()
    if not root:
        print("error: not inside a git repository", file=sys.stderr)
        return 1
    slug = _aipf.safe_slug(args.slug)
    if not slug:
        print("error: invalid slug", file=sys.stderr)
        return 1
    wt_dir = worktree_dir(root, slug)

    # Drop the symlink first so `git worktree remove` doesn't fret over it; it
    # only points at the shared store, so removing the link keeps all history.
    link = os.path.join(wt_dir, ".workflow")
    try:
        if os.path.islink(link):
            os.unlink(link)
            print("removed symlink " + link)
    except OSError as e:
        print("warning: could not remove symlink (" + str(e) + ")")

    git_args = ["worktree", "remove", wt_dir]
    if args.force:
        git_args.append("--force")
    rc, out, err = _git(*git_args, cwd=root)
    if rc != 0:
        msg = (err or out).strip()
        if "is not a working tree" in msg or "No such file" in msg:
            print("worktree not present (nothing to remove): " + wt_dir)
        else:
            print("error: git worktree remove failed:\n" + msg, file=sys.stderr)
            print("(retry with --force if the working tree is dirty)",
                  file=sys.stderr)
            return 1
    else:
        print("removed worktree " + wt_dir)

    # Clear the now-dangling pointer/branch off the task's state.json so the hub
    # card stops referencing a tree that no longer exists (history is untouched).
    if _clear_state_worktree_fields(root, slug):
        print("cleared worktreePath/branch in "
              + _aipf.task_file(root, slug, "state.json"))

    # History stays: we deliberately do NOT touch <main>/.workflow/tasks/<slug>/.
    print("kept task history in " + _aipf.task_dir(root, slug))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="worktree.py",
        description="Manage per-task git worktrees for parallel ai-pathfinder runs.")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="create or resume a task's worktree")
    a.add_argument("slug", help="task slug (kebab-case)")
    a.add_argument("--base", default=None,
                   help="base ref for a new branch "
                        "(default: the main repo's current branch, else main)")
    a.add_argument("--branch", default=None,
                   help="branch name (default: the slug)")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="list worktree-backed tasks")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("remove", help="remove a task's worktree (keeps history)")
    r.add_argument("slug", help="task slug (kebab-case)")
    r.add_argument("--force", action="store_true",
                   help="force removal even if the working tree is dirty")
    r.set_defaults(func=cmd_remove)
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
