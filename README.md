<img width="2172" height="724" alt="4b367714-ca19-4e9a-b733-c4a57fc52d4a" src="https://github.com/user-attachments/assets/25da8d33-51ac-4684-ad3b-84752f0cbbc6" />
# ai-pathfinder

A long-running, multi-agent coding workflow for large/existing projects, packaged as a Claude Code
plugin. One command, `/feature`, drives a task from a rough idea to reviewed, implemented, and
documented code — while keeping a human comfortably in the loop through a **live per-task HTML
dashboard** and a single explicit approval gate (the plan).

## What it does

- **Right-sized ceremony (fast lane).** Before any of the machinery spins up, `/feature` **triages** the
  task. A *primitive* one (confined to a single module, adds no new functionality, trivially verified, no
  design decision, low risk) takes the **Fast Lane** — the orchestrator makes the change directly and
  verifies it, skipping the dashboard, the sub-agent swarm, and the plan gate. A task counts as *complex*
  when it spans several modules, adds new functionality, or needs nontrivial verification — **not** by how
  many files it touches. Anything complex runs the full flow below, and a fast-lane task that turns out
  complex **escalates** to it. So a small fix doesn't cost a full workflow.
- **Autonomous exploration.** Spawns read-only `wf-explorer` sub-agents that map the relevant code
  (reading the project knowledge base first), so planning is grounded.
- **Human-friendly elaboration.** A per-task dashboard fills in as work proceeds. The human comments on
  individual plan blocks (Google-Docs style), answers open questions, queues up edits **in batches**,
  and clicks **«Send to agent for revision»** — the agent picks the batch up at its next checkpoint
  and replies right in the page. One hard gate: **«Approve plan»**.
- **Visual demos.** When a task warrants it, the planner generates 2–3 self-contained visual variants of
  the solution — interface **mockups** for UI work, an architecture **diagram/infographic** for
  backend/CLI — rendered inline in the dashboard (sandboxed) so the human can preview and **pick one**
  before any code is written.
- **Parallel implementation.** Spawns `wf-coder` sub-agents per work-stream, plus a `wf-documenter` that
  grows a durable, agent-readable knowledge base in `docs/knowledge/` as code lands.
- **Verification.** A `wf-reviewer` runs tests/linters/build and reviews the diff — and, for web UIs,
  drives a real browser via the bundled **Playwright** MCP. In VERIFY the orchestrator also runs the
  `/code-review` and `/security-review` skills as gates (re-runnable from the dashboard).
- **A control-panel dashboard.** Beyond the plan, the dashboard shows a **«Changes»** tab (the task's
  git diff per file + the review findings), a **«Documentation»** tab (the `docs/knowledge/` tree beside a
  live force-directed graph of how docs and code files link together, with this task's files highlighted),
  and a **chat panel** for free-form steering the agent picks up at checkpoints.
- **A flywheel.** Each task enriches `docs/knowledge/`, so the next task's exploration is faster.
- **Observability.** Bundled hooks trace each task as a span tree (sessions, phases, parallel
  sub-agents) and optionally forward it to **Langfuse**.

## Phases

A **TRIAGE** gate runs first: primitive tasks take the **Fast Lane** (direct change → verify → done, no
dashboard/swarm/gate); everything else runs the full flow:

`INTAKE → EXPLORE → ELABORATE → PLAN GATE → IMPLEMENT → VERIFY → DONE` — resumable across sessions via
`.workflow/tasks/<slug>/state.json` (the chosen lane is recorded in `state.lane`).

## `/new-product` — greenfield, from scratch

A second command, `/new-product`, drives a **brand-new product from a rough pitch** rather than a
change to an existing codebase. Where `/feature` starts by exploring code that already exists,
`/new-product` starts from nothing: it elicits requirements, writes a **PRD**, plans the build in
vertical-slice phases, then materializes the product through an **evolutionary build-loop** — sharing
the same companion server, dashboard, and telemetry as `/feature`.

Its stages are:

`INTAKE → DISCOVER → PRD → PRD-GATE → PHASE-PLAN → PLAN-GATE → BUILD → SHIP → DONE`

— resumable via the same `.workflow/tasks/<slug>/state.json`. (A *stage* is a workflow step; a *phase*
is a build slice inside BUILD.) On INTAKE the orchestrator bootstraps git for an empty repo (`git init`,
empty-tree base commit) so the **«Changes»** tab works from the very first commit.

**How it differs from `/feature`:** `/feature` is for adding to or refactoring an **existing**
codebase and gates once on the plan; `/new-product` is **greenfield** — it produces a PRD first, gates
twice (PRD then phase-plan), and then runs an autonomous generate-and-judge loop to grow the product
slice by slice.

**Roles & models.** `/new-product` runs its own `np-*` sub-agent roster (model pinned per agent in
frontmatter) and reuses the workflow's reviewer/documenter:

| sub-agent       | model  | role                                                                |
|-----------------|--------|---------------------------------------------------------------------|
| `np-thinker`    | fable  | ideation, PRD, phase goals, judge rubrics & test specs — works **only** from curated digests |
| `np-researcher` | opus   | gathers and **compresses** domain/stack facts into a research digest |
| `np-coder`      | opus   | tests-first then implementation; commits only via the orchestrator   |
| `np-judge`      | opus   | scores a slice against the PRD — one isolated call per rubric dimension |
| `wf-reviewer`   | —      | reused: code-review / security-review on SHIP                        |
| `wf-documenter` | —      | reused: grows the product's own `docs/knowledge/`                    |

The thinker is deliberately kept on **fable** and fed only curated digests (never raw sources): all
hand-offs go through the orchestrator, and sub-agents never spawn sub-agents.

## `/improve` — discover improvements, then drain them sequentially

A third command, `/improve`, is the sibling that **doesn't write code at all** — it is a *producer* of
`/feature` runs. Point it at an existing app and it surveys the codebase with a **swarm** of read-only
analysts (each from its own prism: UX/product, performance, reliability, tech-debt, DX, functionality
gaps, accessibility & security), reaches **consensus** with a voting panel, lets the human **pick which
improvements to do** on the dashboard, and then **queues the chosen features and drains them one at a
time through `/feature`** — each feature in a fresh context — reusing the same companion server,
dashboard, telemetry, and the runs hub.

Its stages are:

`INTAKE → SCOUT → CONSENSUS → PROPOSE/SELECT GATE → DISPATCH → DONE`

— resumable via the same `.workflow/tasks/<slug>/state.json`.

**How consensus works.** SCOUT spawns **7 `wf-improver` scouts in parallel** (one per prism); each reads
the knowledge base first and proposes improvement candidates with `path:line` evidence. The orchestrator
**consolidates and dedups** them into a stable list, then CONSENSUS spawns **3 `wf-improver` voters in
parallel** — each independently scores the whole list (impact/effort/risk/confidence on a 0–3 scale,
keep/drop). The orchestrator then **aggregates the votes deterministically** (not via an LLM:
`score = (mean(impact) − w·mean(effort) − w·mean(risk)) · mean(confidence)/3`, agreement = share of keeps),
ranks, and takes the **top 6–8** into the gate. This is the same "panel of independent scorers + a
deterministic merge by the orchestrator" pattern as `/new-product`'s judge loop — sub-agents never spawn
sub-agents, so the consensus is manufactured by the orchestrator, not by one agent.

**The one gate: pick which features to do.** Unlike `/feature` (gate = *approve the plan*) and
`/new-product` (two gates: PRD then phase-plan), `/improve` has a single gate where the human **picks
which candidate features to dispatch**. It reuses the dashboard's existing `choice` questions + the
«Approve plan» signal with **zero edits to the server or HTML**: each top-K candidate is one card + one
«Do / Skip» choice; the human submits, then approves; no answer means "skip".

**Dispatch is queue-and-drain.** Rather than fanning out parallel worktrees you'd have to launch by
hand, DISPATCH writes each picked feature's brief and appends it to a project-level queue
(`.workflow/dispatch-queue.json`). The human then drains the queue through `/feature`: running `/feature`
with no task pops the next pending item, runs the **full** workflow (explore → plan gate → implement →
review gates → done) on it, marks it done, and prompts for the next. Between features you **`/clear` then
`/feature`** (a genuinely fresh context per feature, the queue file carrying state across the clear) — or
**`/loop /feature`** to walk away. Every run shows up in the **hub** (`/hub`) as it executes. Parallel
git-worktree fan-out (`scripts/worktree.py`) is still there as an opt-in if you explicitly want it.

**Autonomous drain (opt-in).** By default each drained feature still parks at its **«Approve plan»**
gate. To run the whole queue hands-off, turn on **autonomous drain**: pick **«Автономно»** at the
`/improve` SELECT GATE (which stamps `autonomous:true` on the queue), or invoke **`/feature --auto`** to
override for one drain. In autonomous mode `/feature` runs each item to DONE **without the plan gate** —
instead of asking the human, it **self-resolves the plan's open questions** (picks the sensible default
and records a rationale, visible afterwards on the dashboard and in chat). A **two-tier escalation valve**
keeps it safe: ordinary, reversible ambiguity is decided by the agent and noted (*soft escalation*), but
anything **irreversible / destructive / risking data loss** (deleting or overwriting data, a destructive
migration, `DROP`, rewriting git history, a breaking change to a served contract — anything not revertible
via git) **hard-blocks**: the agent parks and waits for explicit human approval, even during a fully
hands-off `/loop /feature`. Autonomous is a **separate mode from `--eval`** — it still **keeps** VERIFY
with the `/code-review` and `/security-review` gates, the fix-or-justify rule on high-severity findings,
chat steering, and decision transparency. Pair it with **`/loop /feature`** for a genuinely hands-off run.

**How it differs from `/feature` and `/new-product`:** `/feature` implements one already-defined task;
`/new-product` builds a greenfield product from a PRD; `/improve` **discovers what's worth doing** across
the whole app and **queues the winners** for a sequential `/feature` drain — it never edits code itself.
It runs a single two-mode `wf-improver` sub-agent (scout + vote) and reuses the workflow's `wf-documenter`.

## `/ask` — read-only Q&A with a visual answer and a chat

A fourth command, `/ask`, is a lightweight **read-only question-and-answer** mode: ask it how something
works, why a decision was made, or where a behaviour lives, and it answers **without touching project
code at all**. The orchestrator splits the question into facets, spawns a small swarm of read-only
`ask-researcher` sub-agents (each reads `docs/knowledge/INDEX.md` first, then the code, and returns a
structured digest), consolidates their findings, and then **synthesizes the answer itself**: a text
write-up, an **infographic** of the key facts/numbers, and a **process diagram** of how it reached the
answer — all rendered inline in the dashboard (sandboxed). It then keeps a **chat** open so you can ask
follow-up questions, and the run shows up in the **hub** (`/hub`) as an *ask* task (badge included).

Its stages are:

`INTAKE → RESEARCH → SYNTHESIZE → ANSWER → DONE`

— resumable via the same `.workflow/tasks/<slug>/state.json`. There is **no plan gate** and **no
IMPLEMENT/VERIFY** — it is pure read-only Q&A.

- **INTAKE** records the question (as a question, not a spec) in `brief.md`, seeds `state.json`
  (`phase:"INTAKE"`, `kind:"ask"`, `baseCommit`), starts the companion server, and opens a dashboard.
- **RESEARCH** is a **mini-swarm**: the orchestrator decomposes the question into facets (knowledge
  base/docs, server code, dashboard/front-end, tests) and spawns several `ask-researcher` sub-agents in
  parallel (usually 2–4; 1–2 for a narrow question), then **consolidates** their digests into one
  summary. Unlike `/improve`, there is **no voting and no queue** — just gather and merge.
- **SYNTHESIZE** has the orchestrator **itself** (it has Write) produce the text answer (`summary` +
  optional `planBlocks`), the `infographic.html`, and the `process.svg`, and wire them into the
  dashboard's `demo` block as two self-contained mockups.
- **ANSWER** is a **non-terminal** chat loop: the orchestrator parks on `/wait`, listens for the `chat`
  signal, and answers follow-ups (a simple clarification inline, a substantive new question via a fresh
  mini-swarm). Keeping a non-terminal phase keeps the task **active** in the hub during the chat.
- **DONE** happens **automatically after ~24 h of chat silence** (matching the hub's active window) or on
  an explicit human request — moving the task into the hub's **History**.

**How it differs from the others:** `/feature`/`/new-product`/`/improve` all change the repo (write code,
or seed/queue feature runs); `/ask` **only reads** — it answers a question with a visual write-up and a
chat, and edits nothing. Use `/feature` to actually make a change, `/improve` for a prioritized backlog
of improvements, `/new-product` for greenfield. `/ask` runs its own read-only `ask-researcher` sub-agent
(no pinned model, like the `wf-*` roster) and reuses the workflow's `wf-documenter` on DONE.

## `/design` — deep UI/UX audit of one component → one annotated demo → implement

A fifth command, `/design <component>`, runs a **focused UI/UX audit of a single interface component**
and then implements the edits you approve. Point it at **one** element — by **name and/or a screenshot**
— and it critiques that component broadly (many design prisms) while keeping the **scope narrow** (just
that component). Unlike `/improve` (a backlog across the whole app) and `/ask` (read-only Q&A), `/design`
both critiques **and redesigns** the chosen component, reusing the same companion server, dashboard, and
telemetry, with **zero server changes**.

Its stages are:

`INTAKE → AUDIT → COMPOSE → CONSENT GATE → IMPLEMENT → VERIFY → DONE`

— resumable via the same `.workflow/tasks/<slug>/state.json`.

- **INTAKE** takes the component by **name and/or screenshot**: a name locates it in code (Grep/Glob); a
  screenshot is attached on the dashboard (the image-attachment contract) and the orchestrator `Read`s the
  saved file to pass visual context to the auditors.
- **AUDIT** is a **swarm**: the orchestrator spawns read-only `ds-auditor` sub-agents in parallel, one per
  **UI/UX prism** — visual hierarchy & aesthetics, interaction/feedback/affordances, motion, layout &
  responsiveness, copy/clarity, accessibility, and flow/IA. Each reads the knowledge base first and returns
  structured findings (`{ id, prism, severity, problem, location, proposal }`); the orchestrator then
  **consolidates and dedups** them into one ranked list.
- **COMPOSE** has the orchestrator build **one** self-contained annotated mockup (`mockups/redesign.html`):
  numbered badges ①②③ on the redesigned component + a side legend (number → problem → what changed) + a
  **«Before/After»** toggle. It is a **single** demo of all proposed edits — not a pick-one set of competing
  designs — rendered inline in the dashboard (sandboxed, no CDN).
- **CONSENT GATE** is **per-finding**, not plan-approve. Each finding is one card + one «Apply / Skip»
  choice with the **default «Apply»** (opt-out: no answer means *apply*) — the deliberate inverse of
  `/improve`'s opt-in feature pick, since you already chose the component. You uncheck what you don't want,
  **submit**, then **approve** ("implement the remaining set"). It reuses the dashboard's existing `choice`
  questions + the «Approve plan» signal with **zero edits to the server or HTML**.
- **IMPLEMENT** spawns a `ds-coder` per approved finding (the plan *is* the finding); independent ones run
  in parallel.
- **VERIFY** runs `wf-reviewer` plus the **`/code-review`** gate (no `/security-review` — focused UI/UX
  edits of one component rarely add a security surface; run it by hand if needed).

**How it differs from the others:** `/feature` implements one predefined task, `/improve` discovers and
queues improvements app-wide, `/ask` only explains read-only; `/design` **critiques and redesigns one
chosen component**, then implements the human-approved subset of its findings. It runs its own `ds-*`
roster — `ds-auditor` (read-only critic) and `ds-coder` (implementer) — two files because their tool-sets
differ (read-only vs. Write/Edit), neither with a pinned model (they inherit the session model). The
single-annotated-demo design and the opt-out consent gate are recorded in **ADR-0023**.

**The evolutionary build-loop (in brief).** Each BUILD phase runs a loop: `np-coder` first materializes
**executable tests** from the thinker's spec (without seeing the implementation plan), and those tests
are **frozen** (paths + hashes in state). Then each iteration: implement → run the frozen tests →
if green, spawn three parallel `np-judge` calls (one per rubric dimension) → merge their verdicts. The
gate is **hybrid** — the tests are a wall (red tests never close a phase, and the judge isn't even
called), the judge is the steering wheel (3 dimensions × a 0–3 scale, `PASS_THRESHOLD = 80/100`, no
dimension at 0). A `decision()` step the orchestrator computes **deterministically** (not an LLM) ends
each iteration in one of: PASS, REFINE, or one of three **stop conditions** — budget exhausted (≤5
iterations/phase), a score plateau, or oscillation — which park for a human choice.

**Gate policy (V1).** Two human gates — **PRD-GATE** and **PLAN-GATE** — and then the build-loop is
**autonomous**: phases advance without a per-phase gate, escalating to the human only when a stop
condition fires. Both gates reuse the dashboard's existing **«Approve plan»** signal; the
orchestrator interprets it by the current stage (PRD approved vs. phase-plan approved).

## Layout

```
.claude-plugin/   plugin.json, marketplace.json
.mcp.json         bundled MCP servers (playwright, context7)
hooks/hooks.json  telemetry hooks wiring
skills/feature/   the /feature orchestrator skill + reference files
skills/new-product/  the /new-product orchestrator skill + reference files
skills/improve/   the /improve orchestrator skill + reference files (swarm → consensus → feature fan-out)
skills/ask/       the /ask orchestrator skill + reference files (read-only Q&A → visual answer → chat)
skills/design/    the /design orchestrator skill + reference files (one-component UI/UX audit → annotated demo → implement)
agents/           wf-explorer, wf-planner, wf-coder, wf-reviewer, wf-documenter, wf-improver, ask-researcher, ds-* (auditor, coder), np-* (thinker, researcher, coder, judge)
scripts/          server.py (feedback server) + telemetry_hook.py + _aipf.py (shared, stdlib)
templates/        dashboard.html + artifact & knowledge-base templates
evals/            fixtures, scenarios, rubrics for measuring the workflow
```

## Install

From GitHub (anyone):

```
/plugin marketplace add TiltCoding/pathfinder
/plugin install ai-pathfinder@tiltcoding
```

Or from a local checkout during development:

```
/plugin marketplace add /path/to/pathfinder
/plugin install ai-pathfinder@tiltcoding
```

Then run `/feature <describe your task>` in any project. The plugin starts the companion server, opens a
dashboard for the task, and walks the phases. Per-task scratch lives in `.workflow/` (gitignored); the
knowledge base in `docs/knowledge/` is meant to be committed.

## Development

**Tests.** The suite is offline, stdlib-only `unittest` (no network, no disk outside a tempfile). Run the
whole set from the repo root:

```
python3 -m unittest discover -s tests
```

A single file runs the same way as a module, e.g. `python3 -m unittest tests.test_hub`. There is also a
thin `Makefile`: `make test` runs the full suite.

**Cross-platform shortcut.** `make` and `python3` aren't always available (notably on Windows). A
stdlib `dev.py` runner works everywhere by using the current interpreter (`sys.executable`):

```
python dev.py test                 # whole suite (discover -s tests)
python dev.py test tests.test_hub  # specific target(s)
python dev.py serve [--port N] [--open SLUG] [--no-browser] [--no-forward]
python dev.py lint                 # stdlib/no-CDN invariant gate (scripts/check_stdlib.py); also a CI `lint` job
```

**Run the companion server locally.** The server is stdlib-only Python — no build step, no dependencies:

```
python3 scripts/server.py --root "$(pwd)"
```

or `make serve`. It binds an available port and writes its URL/port to `.workflow/server.json` (read the
`url` / `port` fields), and `GET /health` self-reports `{ok, ts, pid, port}` so you can tell which process
owns the server. Useful flags: `--port N` (prefer a specific port), `--no-browser` (don't open a tab), and
`--no-forward` (disable Langfuse forwarding).

**Hooks & telemetry.** Telemetry hooks are wired in `hooks/hooks.json` and append a span tree to
`.workflow/tasks/<slug>/telemetry.jsonl` — they only touch local disk. Without `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` set, telemetry stays **local-only** (nothing is forwarded); see
[Telemetry & MCP](#telemetry--mcp) for the full env-var table and forwarding details.

## Conventions

- **Language.** The agent answers in the **language of the human's request** — auto-detected and applied
  to everything the human reads: the terminal narration, the dashboard content, the working artifacts
  (brief/plan/questions/summary/PRD), gate texts, and the chat/inline replies. The global plugin setting
  in `~/.claude/ai-pathfinder/settings.json` (default **English**, changed from the runs hub via the
  EN/RU toggle) is the **fallback** when there is no human request (autonomous/eval runs) and the
  language of the dashboard/hub **UI chrome**. The **knowledge base** (`docs/knowledge/**`), the README,
  and **git commit messages** stay **English** regardless (unless you explicitly ask otherwise). The
  skill/agent instructions themselves stay **English** (more reliable triggering).
- `${CLAUDE_PLUGIN_ROOT}` locates the server and templates at runtime.

## Telemetry & MCP

**Bundled MCP servers** (`.mcp.json`, launched via `npx` on demand):
- **playwright** (`@playwright/mcp`) — the reviewer drives a real browser in VERIFY for web changes.
  The browser launches lazily, only on the first browser call.
- **context7** (`@upstash/context7-mcp`) — the explorer/planner fetch up-to-date library docs.
  Works without a key (rate-limited); set `CONTEXT7_API_KEY` for higher limits.

**Telemetry hooks** (`hooks/hooks.json` → `scripts/telemetry_hook.py`) append a span tree to
`.workflow/tasks/<slug>/telemetry.jsonl`: a span per `/feature` session and per sub-agent (parallel
sub-agents are siblings — the branching view), tagged with phase/iteration. The **trace id is the task
slug**, so a task is one trace across sessions. Hooks only touch local disk (fast, never block tools).

The dashboard's **«Tracing»** tab visualizes this per task: a session summary (sub-agents, output
tokens, total time, peak context, ≈cost), a parallelism timeline (one lane per concurrent sub-agent),
and a card per sub-agent with model, duration, tokens, context-window fill %, cache-hit %, and ≈cost.
It is served by `GET /trace?slug=`, computed by joining the telemetry spans with per-sub-agent
**transcript** usage on disk (`~/.claude/projects/.../subagents/agent-*.jsonl`) — only numbers are
read, never prose. The same token/cost data also enriches the Langfuse generations.

The companion server forwards new events to **Langfuse** in the background (cursor-based, at-least-once)
when these env vars are set — otherwise it stays **local-only**:

| Env var | Purpose |
|---|---|
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (forwarding off if unset) |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (forwarding off if unset) |
| `LANGFUSE_HOST` | Langfuse base URL (default `https://cloud.langfuse.com`) |
| `CONTEXT7_API_KEY` | Optional Context7 key for higher doc-fetch limits |

Disable forwarding explicitly with `server.py --no-forward`. **Note:** hook payloads carry no token
data, so structure/timing/outcomes come from the hooks while per-sub-agent token & context numbers are
recovered separately from the on-disk transcripts (see the «Tracing» tab above).

## Dashboard tabs

The per-task page is a control panel, served by the stdlib companion server (no build step, no CDN):

- **Workflow** — the plan, questions, visual demos and work-streams; the human comments and
  approves here.
- **Changes** — the task's changed files (working tree diffed against the `baseCommit` captured at
  INTAKE) with `+/−` counts and per-file unified diffs (`GET /changes`), plus the **code-review /
  security-review** runs (`reviews.json`) with ranked findings and buttons to re-run either skill.
- **Documentation** — the `docs/knowledge/` file tree beside a vanilla force-directed SVG graph
  (`GET /knowledge`) of how docs link to each other and to code files; nodes this task touched are
  highlighted, and clicking one opens the doc inline.
- **Tracing** — the observability view (see below).
- **💬 Chat** — a slide-in panel for free-form steering. Messages wake the agent via a `chat` signal and
  are answered at the next checkpoint (it never interrupts a running coder).

## Evaluations

Workflow quality is measured by reusing the **skill-creator** eval toolchain against fixture projects
(with-workflow vs vanilla baseline), so you can compare component variants with variance analysis. See
`evals/run-eval.md`.
