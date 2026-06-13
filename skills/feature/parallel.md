# Parallel runs in worktrees

Read this when the human explicitly asks to run a task **in parallel** with another `/feature` task
that is already in flight (so their working files and branch don't collide). For a single task at a
time you don't need any of this — the normal `SKILL.md` start procedure is enough.

## The model: one store, many worktrees

- **One companion server per project.** Even with several parallel tasks you run exactly one server
  (rooted at the main repo). Don't start a second one — reuse it (see `feedback-loop.md`). The hub at
  **`/hub`** lists every active run and the history across all tasks.
- **One shared store.** Every task's artifacts (`telemetry.jsonl`, `state.json`, `dashboard.json`, …)
  live in the single `<main>/.workflow/tasks/<slug>/`, which the one server reads. A parallel task
  runs in its own git worktree but its artifacts still land in the shared store, via a symlink
  `<worktree>/.workflow -> <main>/.workflow` that `scripts/worktree.py` creates.
- **Own branch, own working files.** The worktree gives the parallel task an isolated working tree and
  branch (`<slug>` off `main` by default), so two tasks editing the repo never fight over files.

## Standing up a parallel task (INTAKE)

When the human wants a task run in parallel, create its worktree with the helper instead of working in
the main tree. This is the only extra step at INTAKE; the rest of the state machine is unchanged.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py" add <slug>
# optional: --base <ref> (default: the main repo's current branch, else main)   --branch <name> (default <slug>)
```

It is idempotent (safe to re-run on resume): an existing worktree or branch is reused, not recreated.
The helper:

1. creates the worktree at `../pathfinder-worktrees/<slug>/` (a sibling of the repo, off the main
   repo's current branch by default),
2. symlinks `<worktree>/.workflow -> <main>/.workflow` so artifacts flow into the shared store,
3. records `worktreePath` and `branch` in the task's `state.json` (the server diffs the
   **«Изменения»** tab against that worktree — see `state-schema.md`).

Then run the task's session **inside that worktree directory** (`cd ../pathfinder-worktrees/<slug>/`),
so the telemetry hook's `cwd` resolves through the symlink into the shared store.

| command                                                              | what it does                              |
|----------------------------------------------------------------------|-------------------------------------------|
| `worktree.py add <slug> [--base <ref>] [--branch <name>]`            | create/resume the worktree + symlink + state |
| `worktree.py list`                                                   | show worktree-backed tasks vs `state.json`|
| `worktree.py remove <slug> [--force]`                                | drop the worktree (clears its `worktreePath`/`branch` from `state.json`); **keeps** task history |

## Per-session attribution

Because the store is shared, the single `.workflow/active.json` would be overwritten by concurrent
sessions and session-level telemetry (`SessionStart`/`Stop`) would be attributed to the wrong task. So
for a parallel run **also** write a per-session pointer alongside `active.json`:

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
