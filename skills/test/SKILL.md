---
name: test
description: >-
  Generate or augment the automated tests for a chosen module/area and close coverage gaps — a focused,
  test-only workflow. Use this for "/test", "напиши тесты для…", "покрой тестами…", "добавь тесты на…",
  "find coverage gaps", "test this module", "backfill tests", or whenever the user wants TESTS written
  for existing code (not new functionality). It autonomously analyses the target with a read-only swarm
  to find uncovered branches/contracts/edge-cases, proposes a concrete test list the human approves at a
  single plan gate, then writes `tests/test_*.py` with parallel coding sub-agents and verifies them — a
  green run is the gate. It is **test-only**, NOT a feature build (use the **feature** skill to add or
  change behaviour), NOT a code review of a diff (use the **review** skill / `/code-review` to critique
  changes), NOT an app-wide improvement backlog (use the **improve** skill), and NOT read-only Q&A (use
  the **ask** skill). It never changes production behaviour — it only adds/strengthens tests.
---

# Test Workflow (orchestrator)

You are the **orchestrator** of a focused, test-only workflow. You do not write production code and you
do not change behaviour — you run a state machine, spawn specialized sub-agents (via the Agent tool),
keep a live HTML dashboard in sync, consume **batched** human feedback at one plan gate, and produce
**only tests** for an existing module/area.

The whole point: take a target the human names — a module, a file, an area, or "the untested bits of X"
— through a read-only coverage analysis to a **reviewed test plan**, then **write the tests** and prove
them green. The change set is strictly `tests/**` (plus, at most, a tiny test helper) — never a change
to the code under test.

This is a sibling of the **feature**, **improve**, **ask**, and **design** workflows, and it rides the
**same** companion server + dashboard contract (0 server changes). The difference is what it produces:
`/feature` implements one predefined task; `/improve` discovers and queues improvements app-wide; `/ask`
explains read-only; `/design` critiques and redesigns one component; `/test` **writes tests** for code
that already exists.

## Mental model

- **Stages** (`state.json.phase`): `INTAKE → ANALYZE → PLAN → PLAN GATE → IMPLEMENT → VERIFY → DONE`.
- **The target is given by name/path.** A module (`scripts/queue.py`), an area, or "the uncovered
  branches of X". Name → you locate it (Grep/Glob) and read it; "area" → you enumerate its files.
- **One hard gate: approve the test plan.** The analysis is autonomous; the human's single decision is
  which proposed tests to write (the plan, as commentable blocks + an approve-plan signal — exactly like
  `/feature`'s plan gate). Everything else — the coverage analysis, the implementation, the verification
  — is autonomous.
- **A green run IS the acceptance gate.** New tests must pass (`python dev.py test`); the suite stays
  green. A test that can only pass by being trivial/tautological is not done — tests must actually
  exercise the cited branch/contract and fail if it regresses.
- **Behaviour is never changed.** If a test reveals a real bug, you do NOT fix the code here (that is a
  `/feature`). You record the finding (a `task-log` line / chat note) and, if the brief allows, mark the
  test `xfail`/skip with a clear reason — but the default is to test the code as-is.
- **Checkpoints & parking.** At the PLAN GATE you **park** and wait for the human to send a batch from
  the dashboard, then approve. You never poll while actively working — same cadence as `/feature`.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the durable, committed
  project knowledge base in `docs/knowledge/` (the documenter grows it at DONE — at least a `task-log`
  line; an area/convention note if the tests established a new pattern).
- **You mediate every handoff.** Sub-agents are read-only analysts or coders and **cannot spawn
  sub-agents** — so the chain runs through you: analysis fan-out → you consolidate the gap list → plan →
  gate → you dispatch coders → verify. There are no direct agent-to-agent channels.

Read these reference files when you reach the relevant part — don't load them all upfront:
- `phases.md` — exactly what to do in each stage and which sub-agent to spawn.
- `feedback-loop.md` — starting the companion server and consuming batched feedback at the gate.
- `dashboard-guide.md` — the `dashboard.json` render model and the PLAN GATE contract.
- `state-schema.md` — the `state.json` shape you read/write to resume.
- `knowledge-guide.md` — what the documenter writes at DONE.
- `parallel.md` — per-task git worktree (the default): each `/test` run stands up its own worktree off
  the current branch so the new tests land on an isolated branch `<slug>`.

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). Run independent ones in parallel (one message,
several calls). Give each the task slug and the absolute workspace path so it writes artifacts in the
right place. The `/test` workflow **reuses the `wf-*` roster** — no new agent type:

| subagent_type   | role                                                                       | when      |
|-----------------|----------------------------------------------------------------------------|-----------|
| `wf-explorer`   | read-only cartographer — maps the target's branches/contracts/edge-cases and what is already covered, into `exploration.md` | ANALYZE |
| `wf-planner`    | turns the gap analysis into a concrete, reviewable **test plan** (one block per proposed test) + open questions | PLAN |
| `wf-coder`      | writes one work-stream of `tests/test_*.py` per the approved plan, by the test conventions | IMPLEMENT |
| `wf-reviewer`   | runs the suite + reviews the new tests for genuineness (no tautologies, real assertions) | VERIFY |
| `wf-documenter` | grows `docs/knowledge/` (task-log + any new convention) at DONE             | DONE      |

`wf-explorer`/`wf-reviewer` are read-only; `wf-coder` writes **only** under `tests/**` (and at most a
tiny shared test helper) — it must not touch the module under test. None spawns its own sub-agents —
every analysis → plan → gate → implement → verify handoff runs through you.

## Start / resume procedure

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the target (e.g. `test-<module>`).
   Workspace is `.workflow/tasks/<slug>/`. If `state.json` already exists there, **resume**: read it and
   jump to the phase/checkpoint it records. Otherwise create the workspace. Write `.workflow/active.json`
   = `{ "slug": "<slug>", "updatedAt": "<iso>" }` so telemetry hooks map this session to the task.
   **Stand the task up in its own git worktree** (`${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py add <slug>`,
   idempotent on resume — see `parallel.md`), then route all file work and sub-agents at the worktree
   path. (Only skip this outside a git repo — then work in place.)
   - **In queue mode (a `/improve` drain)** the slug/brief come from `.workflow/dispatch-queue.json` (use
     `scripts/queue.py next`); skip INTAKE elicitation and adopt that brief. This is the same queue
     contract `/feature` uses — `/test` is a normal drainable target.
2. **Locate the plugin assets.** `${CLAUDE_PLUGIN_ROOT}/scripts/server.py`,
   `${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py`, `${CLAUDE_PLUGIN_ROOT}/templates/`. If unset, find the
   `ai-pathfinder` plugin directory.
3. **Start the companion server** once per project (see `feedback-loop.md`); copy
   `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` to `.workflow/tasks/<slug>/index.html`; print the
   dashboard URL (`http://localhost:<port>/?slug=<slug>`).
4. **Run the state machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **Keep the dashboard the source of truth for the human.** After every stage/iteration, rewrite
  `dashboard.json` (status, phase, the test plan as `planBlocks` + the open questions, work-streams +
  progress at IMPLEMENT). Status is `working` while you act and `awaiting-batch` while parked at the
  PLAN GATE.
- **The PLAN GATE is approve-the-test-plan.** Each proposed test is one `planBlocks[]` card (what it
  covers / which branch or contract / how it asserts). The human comments/edits, clicks **«Отправить»**
  (Submit), then **«Утвердить план»** (Approve). The mandatory order is **Submit → Approve**. Open
  questions (e.g. "mock the network or skip?") are `questions[]` the human answers. **0 server changes**
  — this reuses the `planBlocks` + `questions` + `approve-plan` contract.
- **Tests follow the project convention (mandatory).** Offline stdlib `unittest`, no network/pip; the
  `sys.path` shim to import `scripts/`; `tempfile.mkdtemp()` + `addCleanup` for any disk; cross-platform
  (skip by real capability not `os.name`; `newline=""` for jsonl; paths from `tempfile`/`realpath`).
  See `docs/knowledge/conventions.md` §tests — the coder MUST read it first.
- **Verify for real.** A green suite is necessary but not sufficient: `wf-reviewer` (and you) confirm
  each new test actually exercises the cited branch and would fail on a regression — reject tautologies
  and assertion-free "smoke" that proves nothing. If a new test is red because it caught a **real** bug,
  STOP and surface it (do not silently fix the code — that is a `/feature`); record it and, per the
  brief, xfail/skip with a documented reason or escalate.
- **Feedback is batched.** Read a submission only when parked at the gate and a new `submissions/<n>.json`
  (or an `approve-plan` signal) has appeared. Apply every comment/answer, then write a short reply per
  item into `replies.json`.
- **Headless/eval & autonomous modes.** With `--eval`/`AIPF_EVAL=1` skip the human gate (auto-approve
  the plan / apply pre-seeded submissions). When draining a queue stamped `autonomous:true` (or invoked
  `--auto`), do not park at the PLAN GATE — self-resolve open questions with sensible defaults and
  auto-approve, but KEEP VERIFY (the green-run gate stays). See `parallel.md` / the dispatch-queue
  contract.
- **Output language — the human's request language wins.** Resolve at INTAKE: auto-detect the language
  of the human's request and record it in `state.json.lang` (fallback: the global plugin setting, default
  English). Pass `lang` to every sub-agent; it governs all human-facing output (terminal, dashboard,
  plan/questions, chat/replies). **Always English regardless:** `docs/knowledge/**`, git commit messages,
  and **the test code/identifiers** (test names, comments — match the existing `tests/*.py`, which are
  English). These skill/agent instructions stay English.
- **Prefer reuse.** Sub-agents read `docs/knowledge/INDEX.md` first and match existing test patterns
  (`tests/test_*.py`) before writing new ones — fit the suite, don't invent a parallel style.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` — a span per session
and per sub-agent (parallel analysts/coders are siblings: the branching view), keyed so a task is one
trace (trace id = slug). You don't manage this; just keep `state.json.phase` current and `active.json`
fresh (step 1). The companion server forwards to Langfuse when keys are set, local-only otherwise.

When in doubt about a stage's mechanics, open the matching reference file above and follow it.
