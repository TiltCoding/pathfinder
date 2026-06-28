# Per-task worktrees (the default)

**Every `/feature` task runs in its own git worktree** — this is no longer opt-in. Each task gets an
isolated working tree and branch so two tasks (or a task and your uncommitted hand edits) never share a
branch or fight over files, and every drained `/improve` item lands on its own reviewable branch
instead of piling onto the current one. A single task still gets its own worktree — uniform behavior,
no special-casing. (The only exception: a non-git project, where you work in place.)

This file is the mechanism. You invoke it from `SKILL.md` step 1 / INTAKE for **every** task; running
several tasks at once is then just the same mechanism applied concurrently.

## The model: one store, many worktrees

- **One companion server per project.** Even with several worktree-backed tasks you run exactly one
  server (rooted at the main repo). Don't start a second one — reuse it (see `feedback-loop.md`). The
  hub at **`/hub`** lists every active run and the history across all tasks.
- **One shared store.** Every task's artifacts (`telemetry.jsonl`, `state.json`, `dashboard.json`, …)
  live in the single `<main>/.workflow/tasks/<slug>/`, which the one server reads. A task runs in its
  own git worktree but its artifacts still land in the shared store, via a symlink
  `<worktree>/.workflow -> <main>/.workflow` that `scripts/worktree.py` creates.
- **Own branch, own working files.** The worktree gives each task an isolated working tree and branch
  (`<slug>`, off the base ref — the main repo's current branch by default, or the queue's `baseCommit`
  in queue mode), so no two tasks editing the repo ever fight over files.

## Standing up the worktree (INTAKE)

For **every** task, create its worktree with the helper instead of working in the main tree. This is
the one extra step at INTAKE; the rest of the state machine is unchanged.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py" add <slug>
# optional: --base <ref> (default: the main repo's current branch, else main)   --branch <name> (default <slug>)
# queue mode: pass --base <baseCommit> so the branch forks the queue's base
```

It is idempotent (safe to re-run on resume): an existing worktree or branch is reused, not recreated.
The helper:

1. creates the worktree at `../pathfinder-worktrees/<slug>/` (a sibling of the repo, off the base ref),
2. symlinks `<worktree>/.workflow -> <main>/.workflow` so artifacts flow into the shared store,
3. records `worktreePath` and `branch` in the task's `state.json` (the server diffs the
   **«Изменения»** tab against that worktree — see `state-schema.md`).

**Where the session works.** The orchestrator session is usually launched in the main repo, so you do
not need to relocate it: after `add`, route **all file work into the worktree** — give every `wf-coder`
the worktree path as its working root, run `wf-reviewer`'s tests/build there, and target the worktree
with absolute paths from edits. Artifacts (`state.json`, `dashboard.json`, …) keep going to the shared
store at `<main>/.workflow/` (the symlink makes the two the same place). Telemetry attribution does not
depend on `cwd` — it uses the per-session pointer below — so the session staying in the main tree is
fine. (If you started the session *inside* the worktree directory, even simpler: everything is already
local.)

| command                                                              | what it does                              |
|----------------------------------------------------------------------|-------------------------------------------|
| `worktree.py add <slug> [--base <ref>] [--branch <name>]`            | create/resume the worktree + symlink + state |
| `worktree.py list`                                                   | show worktree-backed tasks vs `state.json`|
| `worktree.py remove <slug> [--force]`                                | drop the worktree (clears its `worktreePath`/`branch` from `state.json`); **keeps** task history |

## Per-session attribution

Because the store is shared, the single `.workflow/active.json` would be overwritten when two tasks run
at once and session-level telemetry (`SessionStart`/`Stop`) would be attributed to the wrong task. So
**also** write a per-session pointer alongside `active.json` (cheap, and required as soon as a second
task appears — write it for every worktree-backed task so concurrency is always safe):

```
.workflow/active/<session_id>.json = { "slug": "<slug>", "sessionId": "<session_id>", "updatedAt": "<iso>" }
```

The hook prefers this per-session file (keyed by `session_id`) and falls back to `active.json` when
it's absent, so single-task runs behave exactly as before.

## Cleanup (manual, after merge)

Worktrees are cleaned up **by hand** once the branch is merged:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py" remove <slug>   # add --force if the tree is dirty
```

`remove` drops the worktree and the symlink but **never** deletes `<main>/.workflow/tasks/<slug>/` —
the task's history stays in the shared store and remains visible in the hub's History section.
