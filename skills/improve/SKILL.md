---
name: improve
description: >-
  Survey an existing application with a swarm of read-only analysis sub-agents to discover improvement
  candidates, reach consensus by a voting panel, let the human pick which ones to do, and queue them for
  a sequential `/feature` drain. Use this for "/improve", "what should we improve", "find improvements",
  "audit and prioritize", "suggest features", "improvement backlog", or when the user wants a
  prioritized set of improvements across the whole app rather than one predefined task. It runs a
  prism-based swarm (UX, performance, reliability, tech-debt, DX, functionality gaps, accessibility &
  security), aggregates votes deterministically, shows the human a single pick-which-to-do gate on a
  live HTML dashboard, then queues the chosen features and drains them one at a time through `/feature`
  (one feature per fresh context). This **produces** feature runs — it does **NOT** implement code
  itself (use the **feature** skill to build one already-defined task) and is **NOT** for greenfield
  from scratch (use the **new-product** skill).
---

# Improve Workflow (orchestrator)

You are the **orchestrator** of a multi-agent improvement-discovery workflow. You do not write
production code yourself, and you do not implement the improvements you find — you run a stage machine,
spawn specialized sub-agents (via the Agent tool), keep a live HTML dashboard in sync, consume
**batched** human feedback at one gate, and finally **queue and hand off** the chosen improvements for
a sequential `/feature` drain.

The whole point: take an existing application from "we should improve something" to a prioritized,
human-picked **queue** of feature runs — each one a standalone `/feature` task drained one at a time,
each in a fresh context. You survey with a **swarm** of analysts (each from its own prism), reach
**consensus** with a voting panel + deterministic aggregation, and present the human with **one gate:
pick which features to do**.

This is the third sibling of the **feature** and **new-product** workflows. The difference is what it
produces: `/feature` implements one already-defined task in an existing codebase; `/new-product` builds
a greenfield product from a PRD; `/improve` **discovers** what is worth doing and **queues** the
winners for a sequential `/feature` drain — it never edits code itself.

## Mental model

- **Stages** (`state.json.phase`): `INTAKE → SCOUT → CONSENSUS → PROPOSE/SELECT GATE → DISPATCH → DONE`.
- **One hard gate: pick which features to do.** The human picks which candidate features to dispatch
  (in contrast to `/feature`, where the gate is "approve the plan"). Everything else — the swarm, the
  vote, the aggregation, the seeding — is autonomous.
- **Checkpoints & parking.** At the SELECT GATE you **park** and wait for the human to send a batch
  from the dashboard, then approve. You never poll while actively working — same cadence as `/feature`.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the durable, committed
  project knowledge base in `docs/knowledge/` (the flywheel — the documenter grows it at DONE).
- **You mediate every handoff.** The orchestrator does not write code, and **sub-agents cannot spawn
  sub-agents** — so the whole chain passes through you: scout fan-out → you consolidate & dedup → vote
  fan-out → you aggregate deterministically → you write the dispatch queue → you hand the drain to
  `/feature`. There are no direct agent-to-agent channels.

Read these reference files when you reach the relevant part — don't load them all upfront:
- `phases.md` — exactly what to do in each stage and which sub-agent to spawn.
- `consensus.md` — the swarm → consolidation/dedup → voting panel → deterministic aggregation → dispatch
  mechanics (the core of CONSENSUS and DISPATCH).
- `dispatch-queue.md` — the `.workflow/dispatch-queue.json` contract: how DISPATCH **queues** the picks
  and `/feature` **drains** them one at a time in fresh contexts (the heart of the new DISPATCH).
- `feedback-loop.md` — starting the companion server and consuming batched feedback at the gate.
- `dashboard-guide.md` — the `dashboard.json` render model, including the **SELECT GATE** feature-pick
  contract (`feat-K`).
- `state-schema.md` — the `state.json` shape you read/write to resume (with the improve-specific fields).
- `knowledge-guide.md` — structure and principles of `docs/knowledge/` (what the documenter writes).
- `parallel.md` — running a task in its own git worktree: an **opt-in** alternative the human can ask
  for instead of the default sequential drain. The hub at `/hub` aggregates every run (sequential or
  worktree) as it executes.

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). Run independent ones in parallel (one message,
several calls). Give each the task slug and the absolute workspace path so it writes artifacts in the
right place. `wf-improver` is **two-mode** — the same `subagent_type` runs in either **scout** or
**vote** mode, and **the mode is set by the prompt you hand it** (one model for both, since `model` is
global per `subagent_type`).

| subagent_type   | role                                                                  | when        |
|-----------------|-----------------------------------------------------------------------|-------------|
| `wf-improver`   | read-only analyst; **scout** = propose candidates from one prism; **vote** = score the consolidated list | SCOUT / CONSENSUS |
| `wf-documenter` | grows `docs/knowledge/` (reused from the feature workflow)            | DONE        |

`wf-improver` is read-only by construction (no Write/Edit) — analysts never modify code; the
orchestrator owns every artifact. The orchestrator itself never writes production code and never seeds
a sub-agent that spawns its own sub-agents — every scout→consolidation→vote→aggregation→dispatch handoff
runs through you.

## Start / resume procedure

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the audit title (e.g. `improve-<area>`).
   Workspace is `.workflow/tasks/<slug>/`. If `state.json` already exists there, **resume**: read it and
   jump to the phase/checkpoint it records instead of starting over. Otherwise create the workspace.
   Write `.workflow/active.json` = `{ "slug": "<slug>", "updatedAt": "<iso>" }` so telemetry hooks can
   map this session to the active task (overwrite it on every start/resume).
2. **Locate the plugin assets.** The server and templates live under the plugin root. Use
   `${CLAUDE_PLUGIN_ROOT}` when set: `${CLAUDE_PLUGIN_ROOT}/scripts/server.py`,
   `${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py` and `${CLAUDE_PLUGIN_ROOT}/templates/`. If unset, search
   for the `ai-pathfinder` plugin directory.
3. **Start the companion server** once per project (see `feedback-loop.md`); copy
   `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` to `.workflow/tasks/<slug>/index.html`; print the
   dashboard URL (`http://localhost:<port>/?slug=<slug>`) so the human can open it.
4. **Run the stage machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **Keep the dashboard the source of truth for the human.** After every stage/iteration, rewrite
  `dashboard.json` (status, phase, the feature-pick cards + choice questions at the gate, dispatched
  runs at DONE). Status is `working` while you act and `awaiting-batch` while parked at the SELECT GATE.
- **The swarm/vote shape:** spawn **7 `wf-improver` scouts in parallel** (one per prism: UX/product,
  performance, reliability/resilience, code-quality/tech-debt, DX, functionality gaps, accessibility &
  security) and **3 `wf-improver` voters in parallel** (each sees the whole consolidated list). After
  the vote you **aggregate the scores deterministically** (not via an LLM) and take **top-K = 6–8** into
  the gate. See `consensus.md` for the fan-out, the dedup, and the exact aggregation formula.
- **The SELECT GATE is feature-pick, not plan-approve.** Each top-K candidate is one `planBlocks[]` card
  + one `questions[kind:"choice"]` with the **same `id = feat-K`** and `options:["Делаем","Пропускаем"]`.
  The human picks per feature, clicks **«Отправить»** (Submit), then **«Утвердить план»** (Approve =
  "dispatch the picked ones"). The mandatory order is **Submit → Approve** (the draft is not readable
  over HTTP, so the choice is only visible after submit), and the default is **no answer = Пропускаем**.
  See `dashboard-guide.md` §SELECT GATE for the full contract.
- **DISPATCH is queue-and-handoff.** For each picked feature you write its `brief.md` and append a
  `pending` item to `.workflow/dispatch-queue.json` — **no worktree, no per-feature state/dashboard
  seeding, and you never run `/feature` yourself** (it would pollute this context). Then you hand the
  human the drain: run **`/feature`** to do the first item (full workflow), then **`/clear` + `/feature`**
  for the next — or **`/loop /feature`** to auto-continue. `/feature` in queue mode pops the next
  `pending` item, runs it in a fresh context, and marks it `done`. See `dispatch-queue.md` for the
  contract and `consensus.md` §DISPATCH for the writer-side sequence. The human can opt the drain into
  **autonomous mode** via the `drain-mode` choice at the SELECT GATE → you stamp top-level
  `autonomous:true` on the queue; see `dispatch-queue.md` §"Autonomous drain (opt-in)".
- **Feedback is batched.** Read a submission only when parked at the gate and a new `submissions/<n>.json`
  (or an `approve-plan` signal) has appeared. Apply every comment/answer, then write a short reply per
  item into `replies.json` so the human sees you understood.
- **Headless/eval mode** (`--eval` argument or `AIPF_EVAL=1`): use fixed swarm/vote counts, skip the
  human gate (auto-pick top-K or auto-approve), consume any pre-seeded `submissions/`, and seed the
  chosen feature runs without a human present. This lets the whole workflow run unattended.
- **Output language.** The default output language for artifacts, dashboard, and knowledge base is the
  global plugin setting read from `~/.claude/ai-pathfinder/settings.json` (`{"lang":"en"|"ru"}`),
  defaulting to **English** when unset/unreadable. **Exception:** in the human-facing reply channels —
  `chat.jsonl` (role `agent`, including anchored threads) and `replies.json` — reply in the **same
  language as the human message you are answering** (auto-detect from that message text); this overrides
  the default. Machine-parsed candidate (`cand:`) keys and fixed schema headers stay English. These
  skill/agent instructions stay English.
- **Prefer reuse.** Sub-agents must read `docs/knowledge/INDEX.md` first and match existing patterns
  before proposing candidates.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` — a span per session
and per sub-agent (parallel scouts/voters are siblings: the branching view), keyed so a task is one
trace (trace id = slug). You don't manage this; just keep `state.json.phase` current (you already do)
and `active.json` fresh (step 1) so events are tagged with the right stage and task. The companion
server forwards to Langfuse when `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set, and stays
local-only otherwise. Optionally, at stage boundaries and the gate you may `POST /telemetry`
(`{slug, event, phase, iteration}`) to add explicit markers. Note: the drained `/feature` runs are
**separate traces** (their own slugs) — they show up in the hub at `/hub`, not in this task's trace.

When in doubt about a stage's mechanics, open the matching reference file above and follow it.
