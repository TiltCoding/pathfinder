---
name: debug
description: >-
  Diagnose and fix a bug or failing test in an existing codebase — reproduce → locate the root cause →
  apply a minimal fix → prove it with a regression test. Use this for "/debug", "почему падает…",
  "почему ломается X", "найди причину бага", "fix this failing test", "diagnose this error", "this
  throws/crashes/returns wrong", or whenever the user reports something BROKEN and wants it understood
  and corrected (not a new capability). It autonomously reproduces the symptom, spawns a read-only swarm
  to map suspect code paths and form root-cause hypotheses, confirms the cause with the human at a single
  root-cause gate, then makes the smallest behaviour-correcting change plus a regression test and proves
  the suite green. It is a **focused fix workflow**, NOT a feature build (use the **feature** skill to add
  or change intended behaviour), NOT test-only (use the **test** skill to backfill coverage without a
  bug), NOT a diff/PR critique (use **/code-review**), and NOT read-only Q&A (use the **ask** skill to
  only *explain* how something works). It changes production code only as much as the root cause requires.
---

# Debug Workflow (orchestrator)

You are the **orchestrator** of a focused bug-fixing workflow. You do not guess-and-patch — you run a
state machine, spawn specialized sub-agents (via the Agent tool), keep a live HTML dashboard in sync,
consume **batched** human feedback at one root-cause gate, and land a **minimal, verified** fix for one
reported defect.

The whole point: take a symptom the human reports — a stack trace, a wrong result, a failing test, "X
breaks when Y" — from a **reproduction** through a disciplined **root-cause diagnosis** to a **small
corrective change guarded by a regression test** that fails before the fix and passes after. The change
set is as narrow as the cause allows — never an opportunistic refactor or feature.

This is a sibling of the **feature**, **test**, **improve**, **ask**, and **design** workflows, and it
rides the **same** companion server + dashboard contract (0 server changes). The difference is what it
produces: `/feature` implements one predefined task; `/test` backfills coverage; `/ask` explains
read-only; `/debug` **finds and fixes one broken thing**, root-cause-first.

## Mental model

- **Stages** (`state.json.phase`): `INTAKE → REPRO → DIAGNOSE → ROOT-CAUSE GATE → FIX → VERIFY → DONE`.
- **Reproduce before you theorise.** The first job is a deterministic reproduction (a failing test, a
  command + observed vs expected, a minimal trigger). No repro → you cannot prove a fix; say so and ask
  the human for the missing detail rather than patch blindly.
- **Root cause, not symptom.** The swarm forms competing hypotheses tied to `path:line` evidence; you
  consolidate them and the human confirms the actual cause at the gate. A fix that silences the symptom
  without explaining the cause is rejected.
- **One hard gate: confirm the root cause.** The reproduction and diagnosis are autonomous; the human's
  single decision is *"yes, that's the cause — fix it that way"* (the confirmed hypothesis + the proposed
  minimal fix, as commentable blocks + an approve-plan signal — exactly like `/feature`'s plan gate).
- **The regression test IS the proof.** Before the fix, a test reproduces the bug and is **red**; after
  the fix it is **green** and the rest of the suite stays green. A fix without a guarding test is not
  done (unless the defect is genuinely untestable — then say why).
- **Minimal blast radius.** Change only what the root cause requires. If the real fix is large or is
  actually a feature/refactor, STOP at the gate and hand off (`/feature`) rather than scope-creep here.
- **Checkpoints & parking.** At the ROOT-CAUSE GATE you **park** and wait for the human to send a batch
  from the dashboard, then approve. You never poll while actively working — same cadence as `/feature`.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the durable, committed
  project knowledge base in `docs/knowledge/` (the documenter grows it at DONE — at least a `task-log`
  line; an ADR if the bug exposed a non-obvious invariant worth recording).
- **You mediate every handoff.** Sub-agents are read-only analysts or coders and **cannot spawn
  sub-agents** — so the chain runs through you: hypothesis fan-out → you consolidate → gate → you dispatch
  the fix → verify. There are no direct agent-to-agent channels.

Read these reference files when you reach the relevant part — don't load them all upfront:
- `../_shared/dashboard-contract.md` — the **canonical invariant contract** (companion server, dashboard.json schema + endpoints, the Submit→Approve gate, shared `state.json` fields) that the per-skill `feedback-loop.md` / `dashboard-guide.md` / `state-schema.md` build on — change the shared core there.
- `phases.md` — exactly what to do in each stage and which sub-agent to spawn.
- `feedback-loop.md` — starting the companion server and consuming batched feedback at the gate.
- `dashboard-guide.md` — the `dashboard.json` render model and the gate contract.
- `state-schema.md` — the `state.json` shape you read/write to resume.
- `knowledge-guide.md` — what the documenter writes at DONE.
- `parallel.md` — per-task git worktree (the default): each `/debug` run stands up its own worktree off
  the current branch so the fix lands on an isolated branch `<slug>`.

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). Run independent ones in parallel (one message,
several calls). Give each the task slug and the absolute workspace path. `/debug` **reuses the `wf-*`
roster** — no new agent type:

| subagent_type   | role                                                                        | when       |
|-----------------|-----------------------------------------------------------------------------|------------|
| `wf-explorer`   | read-only cartographer — traces the suspect code path(s) and forms root-cause hypotheses tied to `path:line`, into `exploration.md` | DIAGNOSE |
| `wf-planner`    | turns the confirmed/leading hypothesis into a minimal **fix plan** (the change + the regression test) + open questions | DIAGNOSE→GATE |
| `wf-coder`      | applies the approved fix and writes the regression test, smallest change that addresses the cause | FIX |
| `wf-reviewer`   | runs the suite + reviews the fix for correctness against the cause (and that the regression test truly guards it) | VERIFY |
| `wf-documenter` | grows `docs/knowledge/` (task-log + any ADR for a non-obvious invariant) at DONE | DONE |

`wf-explorer`/`wf-reviewer` are read-only; `wf-coder` makes the narrow corrective change + its test.
None spawns its own sub-agents — every diagnose → gate → fix → verify handoff runs through you.

## Start / resume procedure

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the symptom (e.g. `debug-<area>`).
   Workspace is `.workflow/tasks/<slug>/`. If `state.json` already exists there, **resume**: read it and
   jump to the phase/checkpoint it records. Otherwise create the workspace. Write `.workflow/active.json`
   = `{ "slug": "<slug>", "updatedAt": "<iso>" }` so telemetry hooks map this session to the task.
   **Stand the task up in its own git worktree** (`${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py add <slug>`,
   idempotent on resume — see `parallel.md`), then route all file work and sub-agents at the worktree
   path. (Only skip this outside a git repo — then work in place.)
   - **In queue mode (a `/improve` drain)** the slug/brief come from `.workflow/dispatch-queue.json` (use
     `scripts/queue.py next`); skip INTAKE elicitation and adopt that brief. Same queue contract
     `/feature` uses — `/debug` is a normal drainable target.
2. **Locate the plugin assets.** `${CLAUDE_PLUGIN_ROOT}/scripts/server.py`,
   `${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py`, `${CLAUDE_PLUGIN_ROOT}/templates/`. If unset, find the
   `ai-pathfinder` plugin directory.
3. **Start the companion server** once per project (see `feedback-loop.md`); copy
   `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` to `.workflow/tasks/<slug>/index.html`; print the
   dashboard URL (`http://localhost:<port>/?slug=<slug>`).
4. **Run the state machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **Keep the dashboard the source of truth for the human.** After every stage/iteration, rewrite
  `dashboard.json` (status, phase; at the gate the confirmed-cause + fix plan as `planBlocks` and the
  open questions; work-streams + progress at FIX). Status is `working` while you act and `awaiting-batch`
  while parked at the ROOT-CAUSE GATE.
- **The ROOT-CAUSE GATE is confirm-the-cause-and-fix.** The leading hypothesis (with its `path:line`
  evidence and reproduction) and the proposed minimal fix + regression test are `planBlocks[]` cards; the
  human comments/edits, clicks **«Отправить»** (Submit), then **«Утвердить план»** (Approve). The
  mandatory order is **Submit → Approve**. Open decisions (e.g. "fix at the call site or the helper?")
  are `questions[]`. **0 server changes** — reuses the `planBlocks` + `questions` + `approve-plan`
  contract.
- **Reproduce first, fix second, prove always.** Do not advance to FIX without a reproduction, and do not
  reach DONE without a regression test that is red before the change and green after (or a documented
  reason the defect is untestable). The full suite must end green (`python dev.py test`) and stay
  stdlib/no-CDN clean (`python scripts/check_stdlib.py`).
- **Stay minimal; escalate scope.** If the confirmed fix turns out to be large, cross-cutting, or really
  a feature/refactor, STOP at the gate and recommend `/feature` rather than growing the change here. A
  destructive or irreversible step is a hard-block in autonomous mode (ask the human).
- **Feedback is batched.** Read a submission only when parked at the gate and a new `submissions/<n>.json`
  (or an `approve-plan` signal) has appeared. Apply every comment/answer, then write a short reply per
  item into `replies.json`.
- **Headless/eval & autonomous modes.** With `--eval`/`AIPF_EVAL=1` skip the human gate (auto-confirm the
  leading hypothesis / apply pre-seeded submissions). When draining a queue stamped `autonomous:true` (or
  invoked `--auto`), do not park at the ROOT-CAUSE GATE — self-confirm the best-evidenced hypothesis with
  a recorded rationale and auto-approve, but KEEP VERIFY (the red→green regression gate stays) and keep
  the hard-block on destructive/irreversible fixes.
- **Output language — the human's request language wins.** Resolve at INTAKE: auto-detect the language of
  the human's request and record it in `state.json.lang` (fallback: the global plugin setting, default
  English). Pass `lang` to every sub-agent; it governs all human-facing output (terminal, dashboard,
  hypotheses/plan/questions, chat/replies). **Always English regardless:** `docs/knowledge/**`, git
  commit messages, and code/test identifiers (match the existing source). These skill/agent instructions
  stay English.
- **Prefer reuse.** Sub-agents read `docs/knowledge/INDEX.md` first and match existing patterns — the fix
  fits the codebase's conventions and the regression test fits the `tests/` house style.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` — a span per session
and per sub-agent (parallel hypothesis-hunters are siblings: the branching view), keyed so a task is one
trace (trace id = slug). You don't manage this; just keep `state.json.phase` current and `active.json`
fresh (step 1). The companion server forwards to Langfuse when keys are set, local-only otherwise.

When in doubt about a stage's mechanics, open the matching reference file above and follow it.
