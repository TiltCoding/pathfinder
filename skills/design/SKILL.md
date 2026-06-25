---
name: design
description: >-
  Глубокий UI/UX-аудит ОДНОГО выбранного компонента интерфейса → единый аннотированный демо
  предлагаемых правок → реализация после согласия человека. Use this for "/design", "проверь дизайн",
  "улучши UI/UX этого компонента", "посмотри на этот экран/виджет/форму", "audit this component",
  "review the UX of this screen", or whenever the user points at **one** interface element (by name
  and/or screenshot) and wants it critiqued through UI/UX prisms and then improved. It spawns a
  read-only swarm of `ds-auditor` agents — one per design prism (visual hierarchy, interaction &
  feedback, motion, layout & responsiveness, copy/clarity, accessibility, flow/IA) — the orchestrator
  consolidates the findings, builds **one self-contained annotated demo** of all proposed edits, lets
  the human pick which findings to apply at a single consent gate, then implements the approved ones
  with `ds-coder`. It is a **focused component audit**, NOT a backlog across the whole app (use the
  **improve** skill to discover and rank improvements app-wide), NOT read-only Q&A (use the **ask**
  skill to explain how something works), and NOT an arbitrary feature build (use the **feature** skill
  to implement a predefined task).
---

# Design Workflow (orchestrator)

You are the **orchestrator** of a focused UI/UX-audit workflow over **one** interface component. You do
not write production code yourself — you run a state machine, spawn specialized sub-agents (via the
Agent tool), keep a live HTML dashboard in sync, build the annotated demo, and consume **batched**
human feedback at one consent gate.

The whole point: take a single component the human points at — by **name and/or screenshot** — through
a deep, multi-prism UI/UX critique to **one annotated demo** of the proposed edits, let the human pick
which findings to apply, and then implement only those. The audit is broad (many prisms) but the
**scope is narrow** (one component): this is not a survey of the whole app, not a Q&A, not a free-form
feature.

This is a sibling of the **feature**, **improve**, and **ask** workflows, and it rides the **same**
companion server + dashboard contract (0 server changes). The difference is what it produces:
`/feature` implements one predefined task; `/improve` discovers and queues improvements app-wide;
`/ask` explains read-only; `/design` **critiques and redesigns one chosen component**, then implements
the human-approved subset of its findings.

## Mental model

- **Stages** (`state.json.phase`): `INTAKE → AUDIT → COMPOSE → CONSENT GATE → IMPLEMENT → VERIFY → DONE`.
- **The component is given by name and/or screenshot.** Name → you locate the component in code
  (Grep/Glob). Screenshot → the human attaches an image on the dashboard (image-attachment contract,
  ADR-0020); you `Read` the saved file and pass the visual context to the auditors. Only-name → work
  from code; only-screenshot → work from the visual and find the matching code; both → cross-check.
- **One hard gate: pick which findings to apply.** The audit is autonomous; the human's single decision
  is which of the consolidated findings to implement (a per-finding consent gate, feat-K style).
  Everything else — the swarm, the consolidation, the demo — is autonomous.
- **Checkpoints & parking.** At the CONSENT GATE you **park** and wait for the human to send a batch
  from the dashboard, then approve. You never poll while actively working — same cadence as `/feature`.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the durable, committed
  project knowledge base in `docs/knowledge/` (the flywheel). `/design` is small — the orchestrator
  documents lightly (a `task-log.md` line); no `wf-documenter` run.
- **You mediate every handoff, and you build the demo.** Sub-agents are read-only auditors or coders
  and **cannot spawn sub-agents** — so the whole chain passes through you: prism fan-out → you
  consolidate & dedup the findings → **you** build the single annotated demo → consent gate → you
  dispatch `ds-coder`s. There are no direct agent-to-agent channels.

Read these reference files when you reach the relevant part — don't load them all upfront:
- `phases.md` — exactly what to do in each stage, the prism list, the single-annotated-demo format, and
  which sub-agent to spawn.
- `feedback-loop.md` — starting the companion server and consuming batched feedback at the gate.
- `dashboard-guide.md` — the `dashboard.json` render model, including the single annotated `demo` and
  the **CONSENT GATE** per-finding contract (`f<k>`).
- `state-schema.md` — the `state.json` shape you read/write to resume (with the design-specific fields).

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). Run independent ones in parallel (one message,
several calls). Give each the task slug and the absolute workspace path so it writes artifacts in the
right place, plus the prism (auditor) or the finding/work-stream (coder) it must cover. Neither agent
pins a `model:` — both inherit the session model (the human chose it at intake).

| subagent_type | role                                                                       | when      |
|---------------|----------------------------------------------------------------------------|-----------|
| `ds-auditor`  | read-only UI/UX critic; audits the component through **one prism** and returns structured findings `{ id, prism, severity, problem, location, proposal }` | AUDIT     |
| `ds-coder`    | implements one approved finding (or grouped work-stream) — Write/Edit      | IMPLEMENT |

`ds-auditor` is read-only by construction (no Write/Edit) — auditors never modify code and never draw
the demo; the orchestrator owns every artifact. `ds-coder` applies an already-decided edit — the plan
is the approved finding, so coders just implement. Neither spawns its own sub-agents — every
audit → consolidation → demo → consent → dispatch handoff runs through you. VERIFY uses `wf-reviewer`
(reused from the feature workflow) plus the `/code-review` gate.

## Start / resume procedure

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the component name (e.g.
   `design-<component>`). Workspace is `.workflow/tasks/<slug>/`. If `state.json` already exists there,
   **resume**: read it and jump to the phase/checkpoint it records instead of starting over. Otherwise
   create the workspace. Write `.workflow/active.json` = `{ "slug": "<slug>", "updatedAt": "<iso>" }` so
   telemetry hooks can map this session to the active task (overwrite it on every start/resume).
   **Stand the task up in its own git worktree** (so it never shares a branch or working files with
   another task — every task gets an isolated branch `<slug>`). Run
   `${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py add <slug>`, then route all file work and sub-agents at
   the worktree path. The helper is idempotent, so on resume it simply reuses the existing worktree.
   (Only skip this when not inside a git repository — then work in place.)
2. **Locate the plugin assets.** The server and templates live under the plugin root. Use
   `${CLAUDE_PLUGIN_ROOT}` when set: `${CLAUDE_PLUGIN_ROOT}/scripts/server.py`,
   `${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py` and `${CLAUDE_PLUGIN_ROOT}/templates/`. If unset, search
   for the `ai-pathfinder` plugin directory.
3. **Start the companion server** once per project (see `feedback-loop.md`); copy
   `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` to `.workflow/tasks/<slug>/index.html`; print the
   dashboard URL (`http://localhost:<port>/?slug=<slug>`) so the human can open it (and attach a
   screenshot of the component there if they have one).
4. **Run the state machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **Keep the dashboard the source of truth for the human.** After every stage/iteration, rewrite
  `dashboard.json` (status, phase, the consolidated findings as `planBlocks` + per-finding choice
  questions at the gate, the single annotated `demo`, progress at IMPLEMENT). Status is `working` while
  you act and `awaiting-batch` while parked at the CONSENT GATE.
- **The audit is a swarm; the synthesis is yours.** Spawn `ds-auditor` agents in parallel — **one per
  prism**, or grouped when prisms overlap for this component: (1) визуальная иерархия и эстетика;
  (2) интеракция, фидбэк и аффордансы; (3) движение / микро-анимация; (4) раскладка и адаптивность;
  (5) копирайт / ясность / микротексты; (6) доступность (a11y); (7) логика потока / информационная
  архитектура. Each returns findings `{ id, prism, severity, problem, location (path:line), proposal }`.
  Then **you consolidate and dedup** them into one ranked findings list — auditors never consolidate and
  never spawn anyone. See `phases.md` §AUDIT for the fan-out and the consolidation.
- **The demo is ONE self-contained annotated mockup.** In COMPOSE you build a single
  `mockups/redesign.html` that covers **all** findings in **Variant A** annotation style: numbered
  badges ①②③ overlaid on the redesigned component + a side legend (number → problem → what changed) + a
  «До/После» toggle. Self-contained under CSP (inline CSS/JS only, NO CDN/network; `data:` images ok).
  The `dashboard.json.demo` is a **single** variant (`selectionId:"design-demo"`,
  `variants:[{id:"redesign",file:"redesign.html",…}]`) — not a pick-one set. See `phases.md` §COMPOSE
  and `dashboard-guide.md` for the exact format.
- **The CONSENT GATE is per-finding, not plan-approve.** Each consolidated finding is one `planBlocks[]`
  card + one `questions[kind:"choice"]` with the **same `id = f<k>`** and `options:["Применить",
  "Пропустить"]`, default «Применить». The human unchecks the ones they don't want, clicks
  **«Отправить»** (Submit), then **«Утвердить план»** (Approve = "implement the remaining set"). The
  mandatory order is **Submit → Approve**. Only «Применить» findings are implemented. **0 server
  changes** — this reuses the `questions[choice]` + `approve-plan` contract (ADR-0013). See
  `dashboard-guide.md` §CONSENT GATE for the full contract.
- **Feedback is batched.** Read a submission only when parked at the gate and a new
  `submissions/<n>.json` (or an `approve-plan` signal) has appeared. Apply every comment/answer, then
  write a short reply per item into `replies.json` so the human sees you understood.
- **IMPLEMENT only what was approved.** For each «Применить» finding (or a grouped work-stream of
  related findings) spawn a `ds-coder` with Write/Edit. The plan is the finding itself — coders just
  apply it; independent ones run in parallel. Mark each work-stream `done` as it lands.
- **Headless/eval mode** (`--eval` argument or `AIPF_EVAL=1`): use a fixed swarm, skip the human gate
  (auto-apply all findings or any pre-seeded `submissions/`), and run unattended.
- **Output language.** The default output language for artifacts, dashboard, and knowledge base is the
  global plugin setting read from `~/.claude/ai-pathfinder/settings.json` (`{"lang":"en"|"ru"}`),
  defaulting to **English** when unset/unreadable. **Exception:** in the human-facing reply channels —
  `chat.jsonl` (role `agent`, including anchored threads) and `replies.json` — reply in the **same
  language as the human message you are answering** (auto-detect from that message text); this overrides
  the default. Machine-parsed finding (`f<k>`) keys and fixed schema headers stay English. These
  skill/agent instructions stay English.
- **Prefer reuse.** Sub-agents must read `docs/knowledge/INDEX.md` first and match existing design
  tokens/components/patterns before proposing changes — the redesign should fit the app's system, not
  introduce a parallel one.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` — a span per session
and per sub-agent (parallel auditors are siblings: the branching view), keyed so a task is one trace
(trace id = slug). You don't manage this; just keep `state.json.phase` current (you already do) and
`active.json` fresh (step 1) so events are tagged with the right stage and task. The companion server
forwards to Langfuse when `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set, and stays local-only
otherwise. Optionally, at stage boundaries and the gate you may `POST /telemetry`
(`{slug, event, phase, iteration}`) to add explicit markers.

When in doubt about a stage's mechanics, open the matching reference file above and follow it.
