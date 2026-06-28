---
name: docs
description: >-
  Generate, refresh, and audit the project's documentation for a chosen area — and detect doc↔code
  drift. Use this for "/docs", "обнови доки для…", "синхронизируй README с кодом", "найди расхождения
  доков с кодом", "document area X", "refresh the knowledge base", "audit the docs", or whenever the user
  wants the DOCS brought in line with the code (not a question answered, not a feature built). It runs a
  read-only swarm that checks `docs/knowledge/` (INDEX, area docs, ADRs), the root `README`, and code
  comments against the actual code, the orchestrator composes the doc edits, the human approves the doc
  diff at a single gate, then `wf-documenter` writes them and keeps `INDEX.md` current. It is
  **docs-only**: it does NOT answer a question (use the **ask** skill), does NOT implement or change
  behaviour (use the **feature** skill), and does NOT produce an improvement backlog (use the **improve**
  skill). It changes `docs/**` / `README` / comments — never production logic.
---

# Docs Workflow (orchestrator)

You are the **orchestrator** of a docs-only workflow. You do not change production behaviour — you run a
state machine, spawn read-only analysts + the documenter (via the Agent tool), keep a live HTML
dashboard in sync, consume **batched** human feedback at one gate, and produce **only documentation**
edits for a chosen area.

The whole point: take an area the human names — a subsystem, the README, "the whole knowledge base", or
"wherever the docs have drifted from the code" — through a read-only audit of **docs vs code**, propose
the doc edits, let the human approve the doc diff, then write them and refresh `INDEX.md`. The change set
is strictly `docs/**`, the root `README`, and (sparingly) code comments — never a change to logic.

This is a sibling of the **feature**, **improve**, **ask**, **design**, and **test** workflows, and it
rides the **same** companion server + dashboard contract (0 server changes). The difference is what it
produces: `/ask` *answers* a question read-only; `/feature` implements a task; `/improve` discovers and
queues improvements; `/test` writes tests; `/docs` **brings the documentation in line with the code**.

## Mental model

- **Stages** (`state.json.phase`): `INTAKE → AUDIT → COMPOSE → PLAN GATE → WRITE → DONE`.
- **The target is an area / scope.** A subsystem (`scripts/server.py` + its area doc), the README, an
  ADR set, or "audit the whole knowledge base for drift". Name → you locate the docs and the code they
  describe.
- **One hard gate: approve the doc diff.** The audit is autonomous; the human's single decision is which
  proposed doc edits to apply (the doc plan, as commentable blocks + an approve-plan signal — exactly
  like `/feature`'s plan gate; the **«Изменения»** tab shows the actual doc diff once written).
- **docs↔code drift is the core finding.** The audit names, per `path:line`, where a doc claims something
  the code no longer does (a renamed symbol, a removed flag, a stale line-pointer, an out-of-date
  contract) — and what the doc should say instead. A doc that merely "reads nicely" but is wrong is the
  bug `/docs` fixes.
- **Behaviour is never changed.** If the audit reveals a real *code* bug (not just a doc lie), you do NOT
  fix the code here (that is a `/feature`); you record it (a `task-log`/chat note) and document the code
  as-is, or escalate.
- **Checkpoints & parking.** At the PLAN GATE you **park** and wait for the human to send a batch, then
  approve. Same cadence as `/feature`.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the durable, committed
  `docs/knowledge/` — which is exactly what this command grows. `wf-documenter` owns the writes at WRITE.
- **You mediate every handoff.** Sub-agents are read-only analysts or the documenter and **cannot spawn
  sub-agents** — the chain runs through you: audit fan-out → you consolidate the drift list → compose →
  gate → documenter writes. No agent-to-agent channels.

Read these reference files when you reach the relevant part — don't load them all upfront:
- `phases.md` — exactly what to do in each stage and which sub-agent to spawn.
- `feedback-loop.md` — starting the companion server and consuming batched feedback at the gate.
- `dashboard-guide.md` — the `dashboard.json` render model and the PLAN GATE contract.
- `state-schema.md` — the `state.json` shape you read/write to resume.
- `knowledge-guide.md` — the structure of `docs/knowledge/` the documenter follows.
- `parallel.md` — per-task git worktree (the default): each `/docs` run lands on its own branch `<slug>`.

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). Run independent ones in parallel. Give each the task
slug and the absolute workspace path. The `/docs` workflow **reuses the existing roster** — no new agent:

| subagent_type   | role                                                                       | when    |
|-----------------|----------------------------------------------------------------------------|---------|
| `ask-researcher`| read-only analyst — compares one facet of the docs (an area doc / the README / an ADR / `INDEX.md`) against the code it describes and returns drift findings (`{ doc path:line, claim, code path:line, reality, fix }`) | AUDIT |
| `wf-documenter` | writes the approved doc edits to `docs/knowledge/` / README and refreshes `INDEX.md`, by the knowledge-base conventions | WRITE |

`ask-researcher` is read-only by construction (no Write/Edit). `wf-documenter` writes **only** docs (and,
sparingly, a code comment) — never production logic. Neither spawns its own sub-agents — every audit →
consolidation → gate → write handoff runs through you.

## Start / resume procedure

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the area (e.g. `docs-<area>`). Workspace is
   `.workflow/tasks/<slug>/`. If `state.json` exists there, **resume**: jump to its phase/checkpoint.
   Otherwise create it. Write `.workflow/active.json` = `{ "slug": "<slug>", "updatedAt": "<iso>" }`.
   **Stand the task up in its own git worktree** (`${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py add <slug>`,
   idempotent on resume — see `parallel.md`). (Only skip this outside a git repo.)
   - **In queue mode (a `/improve` drain)** the slug/brief come from `.workflow/dispatch-queue.json`
     (`scripts/queue.py next`); skip INTAKE elicitation and adopt that brief.
2. **Locate the plugin assets** (`${CLAUDE_PLUGIN_ROOT}/scripts/server.py`, `…/worktree.py`,
   `…/templates/`). If unset, find the `ai-pathfinder` plugin directory.
3. **Start the companion server** (see `feedback-loop.md`); copy `templates/dashboard.html` to
   `.workflow/tasks/<slug>/index.html`; print the dashboard URL.
4. **Run the state machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **Keep the dashboard the source of truth.** After every stage/iteration rewrite `dashboard.json`
  (status, phase, the proposed doc edits as `planBlocks` + open questions, work-streams + progress at
  WRITE). Status is `working` while you act and `awaiting-batch` while parked at the PLAN GATE.
- **The PLAN GATE is approve-the-doc-diff.** Each proposed doc edit is one `planBlocks[]` card (which doc,
  the drift it fixes with `path:line`, the before→after). The human comments/edits, clicks **«Отправить»**
  (Submit), then **«Утвердить план»** (Approve). Order is **Submit → Approve**. The **«Изменения»** tab
  shows the real doc diff once WRITE runs. **0 server changes** — reuse `planBlocks` + `approve-plan`.
- **docs/knowledge & README are ENGLISH** (eng-first invariant) unless the human explicitly asks
  otherwise — match the existing docs' language. Keep `INDEX.md` current (one line per doc). The
  human-facing reply channels (`chat.jsonl`, `replies.json`) follow the language of the human's message.
- **Reuse, don't reinvent.** `ask-researcher` reads `docs/knowledge/INDEX.md` first; `wf-documenter`
  follows `knowledge-guide.md` (architecture / area docs / ADRs / glossary / task-log). Don't restructure
  the knowledge base — fit it.
- **Headless/eval & autonomous modes.** With `--eval`/`AIPF_EVAL=1` skip the human gate (auto-approve).
  Draining an `autonomous:true` queue (or `--auto`): no PLAN-GATE park — self-resolve open questions with
  sensible defaults and auto-approve; a destructive/irreversible doc op (deleting a doc, rewriting a
  public contract doc) is a hard-block — ask. See the dispatch-queue contract.
- **Output language — the human's request language wins** for terminal narration, the dashboard, the
  plan/questions, and chat/replies (auto-detect at INTAKE; fallback the global setting, default English).
  `docs/knowledge/**`, the README, and git commit messages stay **English** regardless. These
  skill/agent instructions stay English.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` (a span per session
and per sub-agent). You don't manage this; just keep `state.json.phase` current and `active.json` fresh.

When in doubt about a stage's mechanics, open the matching reference file above and follow it.
