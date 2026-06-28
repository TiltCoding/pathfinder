# Knowledge base guide — `docs/knowledge/`

This is the durable, **committed** memory of the project, written **for agents to read** (not as
end-user docs). It is the flywheel: every task enriches it, and the next task's EXPLORE phase reads it
first and goes faster. The `wf-documenter` agent owns it during IMPLEMENT/VERIFY; explorers read it.

Place it at `docs/knowledge/`. If the project already has a `docs/` tree, nest there. Create/refresh a
root `CLAUDE.md` that points at `docs/knowledge/INDEX.md` so any agent (and Claude Code) bootstraps
from it. Seed missing files from `templates/knowledge/`.

## Files

- **`INDEX.md`** — the map agents read **first**. One line per doc: link + a one-line hook + the topics
  it covers (the MEMORY.md pattern). Must stay current; it's the entry point.
- **`architecture.md`** — modules and their responsibilities, boundaries, data/control flow, the main
  entry points. The 10,000-ft view someone needs before touching anything.
- **`areas/<area>.md`** — one per subsystem (from `templates/knowledge/areas/area-template.md`):
  purpose, key files (clickable paths), the public interface, invariants, gotchas, how to extend.
- **`conventions.md`** — coding patterns, naming, error handling, logging, testing patterns. So coders
  match the house style instead of inventing their own.
- **`decisions/ADR-XXXX-title.md`** — lightweight ADRs (from `adr-template.md`): context, the decision,
  **why**, consequences. Add one whenever the workflow makes a non-obvious choice.
- **`glossary.md`** — domain terms and entities (the domain model in words).
- **`integrations.md`** — external services/APIs, env/config keys (names, **never secret values**).
- **`task-log.md`** — append-only ledger: per task → slug, date, what changed and **why**, link to its
  `plan.md`. Gives future agents the history behind the current shape of the code.

## Principles (why this works for agents)

- **Why over what.** Code and git already show *what*. Capture the non-obvious: rationale, invariants,
  cross-cutting patterns, traps. Don't restate what a reader could trivially get from the source.
- **Link, don't copy.** Use clickable `path/to/file.py:42` references; point at the code, summarize the
  intent. Keep docs short enough to stay true.
- **`INDEX.md` is sacred.** If a doc is added/renamed/meaningfully changed, update the index in the same
  pass, or agents won't find it.
- **Freshness over completeness.** Each file carries an `updated:` line. When the documenter touches an
  area whose doc has drifted from the code, fix it or flag it `> ⚠ возможно устарело` rather than
  leaving silent rot. A small, accurate base beats a large, stale one.
- **Incremental.** The documenter updates only the areas a task touched, plus the index/task-log/ADRs.
  It does not try to document the whole repo at once.
