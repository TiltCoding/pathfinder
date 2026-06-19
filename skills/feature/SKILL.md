---
name: feature
description: >-
  Run a long-running, multi-agent coding workflow for a non-trivial feature or task in a
  large/existing codebase. Use this whenever the user wants to start, resume, plan, or drive
  end-to-end work on a substantial coding task — phrases like "let's work on", "implement",
  "add a feature", "refactor", "build out", "/feature", or when a task is big enough to need
  exploration, a reviewed plan, and several coordinated changes. It autonomously explores the
  codebase with sub-agents, opens a live per-task HTML dashboard where the human comments on the
  plan, implements the approved plan with parallel coding sub-agents, and grows a durable project
  knowledge base. Prefer this over ad-hoc coding for anything beyond a quick one-file edit. This is
  for work on an EXISTING codebase; to create a brand-new product from scratch (greenfield, starting
  from a PRD) use the new-product skill instead.
---

# Feature Workflow (orchestrator)

You are the **orchestrator** of a multi-agent coding workflow. You do not write production code
yourself — you run a state machine, spawn specialized sub-agents (via the Agent tool), keep a live
HTML dashboard in sync, and consume **batched** human feedback at checkpoints.

The whole point: take a substantial task in a large/existing project from a rough idea to reviewed,
implemented, and documented code, while keeping a human comfortably in the loop with a single
explicit approval gate (the plan) and an always-available comment channel.

## Mental model

- **Phases:** INTAKE → EXPLORE → ELABORATE → PLAN GATE → IMPLEMENT → VERIFY → DONE.
- **Checkpoints:** at the end of each iteration you **park** and wait for the human to send a batch
  of feedback from the dashboard (or to approve). You never poll while actively working.
- **One hard gate:** the human must approve the plan before IMPLEMENT. Everything else is autonomous.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the durable, committed
  project knowledge base in `docs/knowledge/` (the flywheel — each task makes the next faster).

Read these reference files when you reach the relevant part — don't load them all upfront:
- `phases.md` — exactly what to do in each phase and which sub-agent to spawn.
- `feedback-loop.md` — starting the companion server and consuming batched feedback at checkpoints.
- `dashboard-guide.md` — the `dashboard.json` render model and how to keep the page current.
- `knowledge-guide.md` — structure and principles of `docs/knowledge/` (what the documenter writes).
- `state-schema.md` — the `state.json` shape you read/write to resume.
- `parallel.md` — running a task in its own git worktree, in parallel with another in-flight task
  (read only when the human asks for that; the hub at `/hub` aggregates all runs).

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). Run independent ones in parallel (one message,
several calls). Give each the task slug and workspace path so it writes artifacts in the right place.

| subagent_type   | role                                                        | when           |
|-----------------|-------------------------------------------------------------|----------------|
| `wf-explorer`   | read-only codebase cartographer → `exploration.md`          | EXPLORE        |
| `wf-planner`    | designs the implementation + open questions → plan/questions| ELABORATE      |
| `wf-coder`      | implements one work-stream                                  | IMPLEMENT      |
| `wf-reviewer`   | runs tests / review / verification                          | VERIFY         |
| `wf-documenter` | grows `docs/knowledge/` in parallel with coders             | IMPLEMENT+     |

## Start / resume procedure

0. **Queue mode (a `/improve` drain).** If you were invoked with **no explicit task** and a
   `.workflow/dispatch-queue.json` exists with at least one `pending` item, you are draining an
   `/improve` queue. Pick the lowest-`n` `pending` item, set it `in-progress` (+`startedAt`), and adopt
   its `slug` as the active task and its `briefPath` as the **given** brief — so you **skip INTAKE
   elicitation** and go straight to EXPLORE. On the default branch, branch from the queue's
   `baseCommit`. At DONE you mark the item `done` and tell the human to `/clear` + `/feature` for the
   next. Full contract: **`../improve/dispatch-queue.md`**. (If there is no queue or the human named a
   task, skip this and use step 1.)

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the task title (in queue mode, the slug is
   the queue item's). Workspace is `.workflow/tasks/<slug>/`. If `state.json` already exists there,
   **resume**: read it and jump to the phase/checkpoint it records instead of starting over. Otherwise
   create the workspace. Write `.workflow/active.json` = `{ "slug": "<slug>", "updatedAt": "<iso>" }` so
   telemetry hooks can map this session to the active task (overwrite it on every start/resume). If the
   human asked to run this task **in parallel** with another in-flight one, stand it up in its own git
   worktree first and also write a per-session pointer — see `parallel.md`.
2. **Locate the plugin assets.** The server and templates live under the plugin root. Use
   `${CLAUDE_PLUGIN_ROOT}` when set: `${CLAUDE_PLUGIN_ROOT}/scripts/server.py` and
   `${CLAUDE_PLUGIN_ROOT}/templates/`. If unset, search for the `ai-pathfinder` plugin directory.
3. **Start the companion server** once per project (see `feedback-loop.md`); copy
   `templates/dashboard.html` to `.workflow/tasks/<slug>/index.html`; print the dashboard URL
   (`http://localhost:<port>/?slug=<slug>`) so the human can open it.
4. **Run the state machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **Keep the dashboard the source of truth for the human.** After every phase/iteration, rewrite
  `dashboard.json` (status, phase, plan blocks, questions, work-streams, progress) — that is how the
  page fills in. Status is `working` while you act and `awaiting-batch` while parked at a checkpoint.
- **Feedback is batched.** Read a submission only when you are parked at a checkpoint and a new
  `submissions/<n>.json` (or an `approve-plan` signal) has appeared. Apply every comment and answer,
  then write a short reply per item into `replies.json` so the human sees you understood.
- **Headless/eval mode** (`--eval` argument or `AIPF_EVAL=1`): skip the human gate (auto-approve the
  plan) and consume any pre-seeded `submissions/`. This lets the whole workflow run unattended.
- **Output language.** The default output language for artifacts, dashboard, and knowledge base is the
  global plugin setting read from `~/.claude/ai-pathfinder/settings.json` (`{"lang":"en"|"ru"}`),
  defaulting to **English** when unset/unreadable. **Exception:** in the human-facing reply channels —
  `chat.jsonl` (role `agent`, including anchored threads) and `replies.json` — reply in the **same
  language as the human message you are answering** (auto-detect from that message text); this overrides
  the default. Machine-parsed digest/candidate headers and fixed schema keys stay English. These
  skill/agent instructions stay English.
- **Prefer reuse.** Sub-agents must read `docs/knowledge/INDEX.md` first and match existing patterns
  before proposing new code.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` — a span per
session and per sub-agent (parallel sub-agents are siblings: the branching view), keyed so a task is
one trace (trace id = slug). You don't manage this; just keep `state.json.phase` current (you already
do) and `active.json` fresh (step 1) so events are tagged with the right phase and task. The companion
server forwards to Langfuse when `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set, and stays
local-only otherwise. Optionally, at phase boundaries and gate events you may `POST /telemetry`
(`{slug, event, phase, iteration}`) to add explicit markers.

When in doubt about a phase's mechanics, open the matching reference file above and follow it.
