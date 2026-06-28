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

- **Right-size the ceremony (fast lane).** Not every task needs the full machine. At intake you
  **triage**: a *primitive* task (confined to one module, no new functionality, trivial verification,
  no design decision, low risk) runs the **Fast Lane** — make the change directly, verify it, report —
  skipping the dashboard, the sub-agent swarm, and the plan gate. A task is *complex* (→ full lane) when
  it spans several modules, adds new functionality, needs nontrivial verification, or carries a design
  decision/risk — **not** by file count. When unsure, take the full lane; the moment a "primitive" task
  reveals hidden complexity, **escalate**. Criteria + mechanics: `phases.md` §0.
- **Phases (full lane):** INTAKE → EXPLORE → ELABORATE → PLAN GATE → IMPLEMENT → VERIFY → DONE.
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
- `parallel.md` — **every task runs in its own git worktree (the default)**: how to stand one up, where
  the session works, and how artifacts still land in the one shared store (the hub at `/hub` aggregates
  all runs). Read it before step 1 — it is no longer opt-in.

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
   `/improve` queue. **Pick the next item with `scripts/queue.py next`** (it atomically takes the
   lowest-`n` `pending` item → `in-progress` (+`startedAt`) and prints its `slug`/`briefPath`/`baseCommit`/
   `autonomous`; **and it first self-heals a crashed drain** — any stale `in-progress` whose session died
   mid-flight is returned to `pending` and re-picked, never lost — feat-14). Adopt its `slug` as the
   active task and its `briefPath` as the **given** brief — so you **skip INTAKE elicitation** and go
   straight to EXPLORE. The worktree you stand up in step 1 forks the queue's `baseCommit` (pass
   `--base <baseCommit>`), so each drained item lands on its own branch off that base and stays
   independently reviewable. At DONE you mark the item `done` (`scripts/queue.py done <slug>`) and tell
   the human to `/clear` + `/feature` for the next. **If the feature can't reach DONE** (VERIFY won't go
   green and isn't fix-or-justifiable, a hard-block with no human, worktree failure), mark it
   `scripts/queue.py fail <slug> --reason "…"` and — in an autonomous drain — escalate (anchored
   `chat.jsonl` `needsAnswer:true` + `state.json.questions[]`) and continue with the next item, never
   abandoning it silently. Full contract incl. recovery/failure: **`../improve/dispatch-queue.md`**
   §"Recovery & failure". (If there is no queue or the human named a task, skip this and use step 1.)
   - **Autonomous flag.** When entering queue mode, read the queue's top-level **`autonomous`** field:
     if `true`, drain this queue **autonomously** (no PLAN GATE park — self-resolve open questions and
     auto-approve — but VERIFY and the review gates are kept; see PLAN GATE / VERIFY in `phases.md`).
     The queue field is the canonical surface. **Per-invocation override:** an explicit
     **`--auto` / `--autonomous`** argument (or an equivalent natural-language request to run unattended)
     turns the **same** mode on just for this invocation — so a human can autonomously drain a manual
     (non-`autonomous`) queue, or run a single named task unattended. The CLI arg overrides the queue
     field for that one run. Canonical spec: **`../improve/dispatch-queue.md`** §"Autonomous drain (opt-in)".

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the task title (in queue mode, the slug is
   the queue item's). Workspace is `.workflow/tasks/<slug>/`. If `state.json` already exists there,
   **resume**: read it and jump to the phase/checkpoint it records instead of starting over. Otherwise
   create the workspace. Write `.workflow/active.json` = `{ "slug": "<slug>", "updatedAt": "<iso>" }` so
   telemetry hooks can map this session to the active task (overwrite it on every start/resume).
   **Always stand the task up in its own git worktree** (so it never shares a branch or working files
   with another task — every task gets an isolated branch `<slug>`). Run
   `${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py add <slug>` (in queue mode pass `--base <baseCommit>` so
   the branch forks the queue's base), then route all file work and sub-agents at the worktree path and
   write a per-session pointer — see `parallel.md`. The helper is idempotent, so on resume it simply
   reuses the existing worktree. (Only skip this when not inside a git repository — then work in place.)
   - **Triage now (fast lane vs full).** Before standing up the server and dashboard, judge whether the
     task is *primitive* (criteria + mechanics in `phases.md` §0 TRIAGE). If it is, run the **Fast Lane**:
     keep the worktree above, **skip steps 2–3 (server + dashboard) and the sub-agent swarm**, make the
     change directly, verify, and report in chat. Otherwise continue with steps 2–4 for the full
     workflow. A resumed task keeps whichever lane it recorded in `state.json.lane`.
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
  - **Autonomous mode is a SEPARATE predicate from `AIPF_EVAL` — do not merge the two.** Autonomous
    skips the PLAN GATE park like eval, but **keeps** VERIFY + the review gates and chat steering;
    eval skips the review gates. See `../improve/dispatch-queue.md` §"Autonomous drain (opt-in)".
- **Output language — the human's request language wins.** Resolve the run language at INTAKE: **use the
  language of the human's request** (auto-detect from their message) and record it in `state.json.lang`;
  fall back to the global plugin setting (`~/.claude/ai-pathfinder/settings.json`, `{"lang":"en"|"ru"}`,
  default **English**) **only** when there is no human request to detect from (autonomous/eval runs).
  Pass `lang` to every sub-agent. **The resolved language is mandatory for everything the human reads:**
  your own terminal narration, the dashboard content, brief/exploration/plan/questions/summary, gate
  texts and choice options, and the reply channels `chat.jsonl` (role `agent`, including anchored
  threads) and `replies.json`. **Always English regardless of the run language** (unless the human
  explicitly asks otherwise): `docs/knowledge/**`, the README, and git commit messages — plus
  machine-parsed digest/candidate headers and fixed schema keys, which are never translated. These
  skill/agent instruction files stay English.
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
