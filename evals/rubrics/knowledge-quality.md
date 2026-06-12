# Rubric: knowledge-base quality (`docs/knowledge/`)

Judge what `wf-documenter` produced for the task.

- **INDEX current** — new/changed docs are linked from `INDEX.md` with a one-line hook.
- **Area doc** — the touched subsystem has/updates an `areas/<area>.md` (purpose, key files, invariants, how to extend).
- **ADR for decisions** — non-obvious choices made during the task have an ADR with a real **why**.
- **Task log** — a `task-log.md` entry records what changed and why, linking the plan.
- **Why over what** — docs capture rationale/invariants/traps, not restatements of what code trivially shows.
- **Link, don't copy** — clickable references; concise and accurate.
- **Freshness** — `updated:` lines set; drift fixed or flagged, not silently left.

A small accurate base that speeds up the next task's EXPLORE beats a large stale one.
