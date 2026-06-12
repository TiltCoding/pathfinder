# Fixture: greenfield-mini

A near-empty **greenfield** fixture used to smoke-test the `/new-product` command end-to-end. There is
no code here on purpose — only a product pitch and pre-seeded human feedback. The orchestrator is
expected to run `git init` (0 commits → empty-tree baseCommit `4b825dc642cb6eb9a060e54bf8d69288fbee4904`)
and scaffold into the (practically empty) repo root.

## Product pitch — `todo`, a tiny single-file CLI to-do list

A trivial command-line to-do tool a developer could `pip install` and use without any service or DB.
State lives in one JSON file in the user's home dir. Deliberately minimal so a headless run with the
in-prompt cap (≤2 iterations/phase, ≤2 phases) terminates fast.

What it should do:

- **Add** a task: `todo add "buy milk"` → appends a task, prints its id.
- **List** open tasks: `todo list` → prints id + text for every not-done task, in insertion order.
- **Complete** a task: `todo done <id>` → marks that task done so it no longer shows in `list`;
  an unknown id exits non-zero with a clear message.

Out of scope for the MVP: due dates, priorities, tags, sync, a TUI, multi-user.

## Pre-seeded feedback (`.aipf-seed/submissions/`)

`.aipf-seed/submissions/*.json` hold the human feedback batches the headless eval run consumes so the
workflow runs unattended (mirrors `py-service/.aipf-seed/`):

- **`1.json`** — DISCOVER requirements-elicitation answers (storage location, id scheme, scope).
- **`2.json`** — PRD-GATE batch: approves the PRD scope with a small clarifying comment.
- **`3.json`** — PLAN-GATE batch: approves the phase plan with a small clarifying comment.

The plan-approval button itself (`approve-plan` signal) is fired by the headless harness per
`evals/run-eval.md`; these batches carry only the answers/comments that accompany each gate.

Evals copy this fixture to a fresh dir before running so the original stays clean.
