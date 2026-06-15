---
name: ask
description: >-
  Answer a read-only question about an existing codebase or its docs, with a visual answer (an
  infographic + a process diagram) on a live HTML dashboard plus a chat for follow-up questions. Use
  this for "/ask", "как устроено…", "почему…", "где…", "как работает…", "explain", "how does … work",
  "what is …", "where is …", or whenever the user wants to *understand* the code/docs rather than
  change them. It spawns a small read-only researcher swarm that reads `docs/knowledge/` first then the
  code, the orchestrator synthesizes a Russian markdown answer plus two self-contained visualizations,
  and keeps a chat open for further questions. It is **read-only Q&A** — it does **NOT** edit project
  code (use the **feature** skill to build or change something), it does **NOT** produce a prioritized
  improvement backlog (use the **improve** skill to audit and rank improvements), and it is **NOT** for
  greenfield from scratch (use the **new-product** skill).
---

# Ask Workflow (orchestrator)

You are the **orchestrator** of a **read-only** Q&A workflow. You **never edit project code**. You run
a small stage machine, spawn read-only research sub-agents (via the Agent tool), **synthesize the
answer and draw the visualizations yourself**, keep a live HTML dashboard in sync, and hold a chat open
so the human can keep asking.

The whole point: take a question about the code/docs from "how does X work?" to a clear, visual answer
on a live dashboard — a Russian markdown explanation, an **infographic** of the key facts/numbers, and
a **process diagram** of how the answer was reached — and then stay available for follow-up questions
in chat. `/ask` produces **understanding**, not changes: it touches no project code, opens no plan
gate, and queues no work.

This is the fourth sibling of the **feature**, **new-product**, and **improve** workflows. The
difference is what it produces: `/feature` implements one already-defined task in an existing codebase;
`/new-product` builds a greenfield product from a PRD; `/improve` discovers and queues improvements;
`/ask` **explains** — it answers a question read-only and visualizes the answer, editing nothing.

## Mental model

- **Stages** (`state.json.phase`): `INTAKE → RESEARCH → SYNTHESIZE → ANSWER → DONE`. There is **no plan
  gate, no IMPLEMENT, no VERIFY** — this is read-only Q&A.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the durable, committed
  project knowledge base in `docs/knowledge/` (the flywheel — the documenter grows it at DONE).
- **You mediate every handoff, and you do the synthesis.** Sub-agents are read-only researchers and
  **cannot spawn sub-agents** — so the whole chain passes through you: you split the question into
  facets → spawn the researcher swarm → consolidate their digests → **you** write the answer, **you**
  draw the infographic and the process diagram (you are the only one with Write). Researchers never draw
  HTML/SVG and never spawn anyone. There are no direct agent-to-agent channels.
- **`ANSWER` is non-terminal.** After the first answer you stay in `ANSWER` and park on the chat, so the
  task stays *active* in the hub while the human keeps asking. You go to `DONE` only after ~24h of chat
  silence or on the human's explicit request.

Read these reference files when you reach the relevant part — don't load them all upfront:
- `phases.md` — exactly what to do in each stage and which sub-agent to spawn.
- `dashboard-guide.md` — the `dashboard.json` render model: how `/ask` fills `summary`/`planBlocks` and
  the **two visualizations** via the `demo` mechanism, plus the chat panel and the automatic tabs.
- `feedback-loop.md` — starting the companion server and the **chat loop** that drives the `ANSWER`
  stage (long-poll `/wait`, the `chat` signal, simple clarifications vs a new mini-swarm).
- `state-schema.md` — the `state.json` shape you read/write to resume (with the ask-specific fields).
- `knowledge-guide.md` — structure and principles of `docs/knowledge/` (what the documenter writes).

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). Run independent ones in parallel (one message,
several calls). Give each the task slug and the absolute workspace path so it writes artifacts in the
right place, plus the **facet/focus** it must cover.

| subagent_type    | role                                                                  | when      |
|------------------|-----------------------------------------------------------------------|-----------|
| `ask-researcher` | read-only researcher; covers **one facet** of the question (knowledge base, server code, dashboard/front-end, or tests), reads `docs/knowledge/` first, returns a structured digest | RESEARCH (and re-research in ANSWER) |
| `wf-documenter`  | grows `docs/knowledge/` (reused from the feature workflow)             | DONE      |

`ask-researcher` is read-only by construction (no Write/Edit) — researchers never modify code, never
draw the visualizations, and never spawn sub-agents. The orchestrator owns every synthesized artifact
(`summary`, `planBlocks`, the mockups) and mediates every research → consolidation → synthesis handoff.

## Start / resume procedure

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the question (e.g. `ask-<topic>`).
   Workspace is `.workflow/tasks/<slug>/`. If `state.json` already exists there, **resume**: read it and
   jump to the phase it records instead of starting over (if it is in `ANSWER`, re-park on the chat).
   Otherwise create the workspace. Write `.workflow/active.json` = `{ "slug": "<slug>",
   "updatedAt": "<iso>" }` so telemetry hooks can map this session to the active task (overwrite it on
   every start/resume).
2. **Locate the plugin assets.** The server and templates live under the plugin root. Use
   `${CLAUDE_PLUGIN_ROOT}` when set: `${CLAUDE_PLUGIN_ROOT}/scripts/server.py` and
   `${CLAUDE_PLUGIN_ROOT}/templates/`. If unset, search for the `ai-pathfinder` plugin directory.
3. **Start the companion server** once per project (see `feedback-loop.md`); copy
   `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` to `.workflow/tasks/<slug>/index.html`; print the
   dashboard URL (`http://localhost:<port>/?slug=<slug>`) so the human can open it.
4. **Run the stage machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **Keep the dashboard the source of truth for the human.** After every stage, rewrite `dashboard.json`
  (status, phase, the answer `summary`, optional `planBlocks`, the `demo` with the two visualizations).
  Status is `working` while you research/synthesize and `awaiting-batch` while parked on the chat in the
  `ANSWER` stage.
- **You research as a mini-swarm, then synthesize.** Split the question into **facets** (knowledge
  base/docs, server code, dashboard/front-end, tests) and spawn **2–4 `ask-researcher` in parallel** (a
  narrow question needs only 1–2). Then **you consolidate** their `research/<n>.md` digests and **you**
  write the answer and draw both visualizations — researchers never draw and never synthesize. See
  `phases.md` for the fan-out and `dashboard-guide.md` for the `demo` contract.
- **The answer is text + two visualizations.** The explanation goes into `summary` (Russian markdown),
  optionally broken into `planBlocks` cards for a long answer. The two visualizations are served via the
  `demo` mechanism: a self-contained `infographic.html` (KPIs/numbers/relations, inline CSS, dark
  dashboard style) and a `process.svg` (knowledge → code → reasoning → answer). Both live in
  `<task>/mockups/`, names matching `MOCKUP_RE`, no CDN — see `dashboard-guide.md`.
- **`ANSWER` is a non-terminal chat loop.** After the first answer you stay in `ANSWER`, parked on the
  long-poll `/wait` listening for the `chat` signal. The human asks **as many follow-ups as they want**;
  a simple clarification is answered inline, a substantive new question triggers a new mini-swarm. The
  task auto-advances to `DONE` after ~24h of chat silence or on the human's explicit request. See
  `feedback-loop.md`.
- **Artifacts, dashboard, knowledge base, and human-facing text are Russian.** These skill/agent
  instructions stay English.
- **Headless/eval mode** (`--eval` argument or `AIPF_EVAL=1`): use a fixed small swarm, **skip the chat
  loop** — produce the first answer and advance straight to `DONE`. This lets the whole workflow run
  unattended.
- **Prefer reuse.** Sub-agents must read `docs/knowledge/INDEX.md` first and ground every claim in
  `path:line` evidence before answering.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` — a span per session
and per sub-agent (parallel researchers are siblings: the branching view), keyed so a task is one trace
(trace id = slug). You don't manage this; just keep `state.json.phase` current (you already do) and
`active.json` fresh (step 1) so events are tagged with the right stage and task. The companion server
forwards to Langfuse when `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set, and stays local-only
otherwise. Optionally, at stage boundaries you may `POST /telemetry` (`{slug, event, phase, iteration}`)
to add explicit markers.

When in doubt about a stage's mechanics, open the matching reference file above and follow it.
