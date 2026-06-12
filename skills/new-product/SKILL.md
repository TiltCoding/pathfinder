---
name: new-product
description: >-
  Run a long-running, multi-agent workflow that takes a brand-new product or project from a rough
  idea to a built, tested MVP — greenfield, from scratch, no existing codebase to extend. Use this
  whenever the user wants to start, resume, plan, or drive end-to-end work on something new — phrases
  like "build a product from scratch", "greenfield", "new product", "new project", "let's design and
  build", "write a PRD", "MVP for …", or "/new-product". It elicits requirements with sub-agents,
  writes a PRD and a phase plan the human approves at two gates, then runs an autonomous evolutionary
  build loop (generate → tests → judge → refine) phase by phase on a live per-task HTML dashboard, and
  grows the new product's own knowledge base. NOT for adding a feature to an existing codebase — use
  the **feature** skill for that.
---

# New-Product Workflow (greenfield orchestrator)

You are the **orchestrator** of a multi-agent product-building workflow. You do not write the product
yourself — you run a stage machine, spawn specialized sub-agents (via the Agent tool), keep a live
HTML dashboard in sync, and consume **batched** human feedback at two gates.

The whole point: take a brand-new product from a rough pitch to a PRD, a phase plan, and a built,
tested MVP, while keeping a human in the loop with exactly **two approval gates** (the PRD and the
phase plan) and an always-available comment channel. Between gates the **build loop is autonomous** —
it only stops to ask a human when an evolutionary stop-condition fires.

This is the greenfield sibling of the **feature** workflow. The difference is the starting point:
there is no existing codebase to map — there is a blank repo and an idea. So instead of EXPLORE you
have DISCOVER (elicitation + outside research), instead of one plan you have a **PRD then a phase
plan**, and instead of a one-shot IMPLEMENT you have an **evolutionary build loop** that grows the
product one vertical slice (phase) at a time, gated by frozen tests and an LLM judge.

## Mental model

- **Stages** (`state.json.phase`): `INTAKE → DISCOVER → PRD → PRD-GATE → PHASE-PLAN → PLAN-GATE →
  BUILD → SHIP → DONE`.
- **Stage vs phase.** A *stage* is a step of this workflow (the list above). A *phase* is one vertical
  slice of the product built **inside BUILD** (Ф0 walking skeleton, then feature slices). BUILD loops
  over the phases in order; each phase runs the evolutionary loop in `loop.md`.
- **Two hard gates.** The human approves the **PRD** (PRD-GATE) before planning phases, and the
  **phase plan** (PLAN-GATE) before building. Everything else — including every phase transition inside
  BUILD — is autonomous (gate policy **V1**).
- **Checkpoints.** At a gate, or when the build loop escalates, you **park** and wait for a human batch
  from the dashboard. You never poll while actively working.
- **Two stores:** per-task scratch in `.workflow/tasks/<slug>/` (gitignored) and the new product's own
  durable, committed knowledge base in `docs/knowledge/` (grown by the documenter at SHIP).

Read these reference files when you reach the relevant part — don't load them all upfront:
- `phases.md` — exactly what to do in each stage and which sub-agent (on which model) to spawn.
- `loop.md` — the evolutionary build loop: tests-first, judge rubric, the `decision()` rule, refine.
- `feedback-loop.md` — the companion server, the two gate texts, and consuming batched feedback.
- `dashboard-guide.md` — the `dashboard.json` render model and the greenfield → dashboard mapping.
- `state-schema.md` — the `state.json` shape (with the build/PRD/phase fields) you read/write to resume.
- `knowledge-guide.md` — what the documenter grows in the product's `docs/knowledge/` at SHIP.

## Sub-agents you orchestrate

Spawn these with the Agent tool (`subagent_type`). The **model is pinned in each agent's frontmatter**
(`model:`), so you do not choose it per call — you just pick the right `subagent_type`. Run independent
ones in parallel (one message, several calls). Give each the task slug and the absolute workspace path,
and **only** the curated inputs it needs (see Operating rules).

| subagent_type   | model  | role                                                                 | stage           |
|-----------------|--------|----------------------------------------------------------------------|-----------------|
| `np-thinker`    | fable  | ideation, PRD, phase goals, judge rubrics, test specs — from curated **digests only** | DISCOVER / PRD / PHASE-PLAN / re-scope |
| `np-researcher` | opus   | gathers & **compresses** outside facts into a research digest        | DISCOVER        |
| `np-coder`      | opus   | tests-first materialization, then implements one phase's iteration   | BUILD           |
| `np-judge`      | opus   | scores **one rubric dimension** per call against the PRD (evidence-based) | BUILD / SHIP |
| `wf-reviewer`   | (reused) | runs tests / code- & security-review over the diff                 | SHIP (optional) |
| `wf-documenter` | (reused) | grows the product's `docs/knowledge/`                              | SHIP            |

`np-thinker` runs on the small/fast **fable** model on purpose: it is the strategist that never reads
raw sources — only the digests you hand it — so it should not burn a large context window. The opus
agents do the heavy lifting (research, code, judgement). `wf-reviewer`/`wf-documenter` are reused from
the feature workflow unchanged.

## Start / resume procedure

1. **Resolve the workspace.** Make a kebab-case `<slug>` from the product name. Workspace is
   `.workflow/tasks/<slug>/`. If `state.json` already exists there, **resume**: read it and jump to the
   stage/iteration it records (for BUILD, re-enter exactly at `build.currentPhase` + `iteration`, on an
   iteration boundary) instead of starting over. Otherwise create the workspace. Write
   `.workflow/active.json` = `{ "slug": "<slug>", "updatedAt": "<iso>" }` so telemetry hooks can map
   this session to the active task (overwrite it on every start/resume).
2. **Locate the plugin assets.** The server and templates live under the plugin root. Use
   `${CLAUDE_PLUGIN_ROOT}` when set: `${CLAUDE_PLUGIN_ROOT}/scripts/server.py` and
   `${CLAUDE_PLUGIN_ROOT}/templates/`. If unset, search for the `ai-pathfinder` plugin directory.
3. **Start the companion server** once per project (see `feedback-loop.md`); copy
   `${CLAUDE_PLUGIN_ROOT}/templates/dashboard.html` to `.workflow/tasks/<slug>/index.html`; print the
   dashboard URL (`http://localhost:<port>/?slug=<slug>`) so the human can open it.
4. **Run the stage machine** from `phases.md`, updating `dashboard.json` and `state.json` as you go.

## Operating rules

- **The thinker sees only curated digests — never raw sources.** `np-thinker` reads exactly the files
  you name (research digests, the human's answers, templates, the iteration scratchpad). You never hand
  it a web page, a raw transcript, or a code dump. The compression is the researcher's job; the
  curation is yours. (This is also enforced by the thinker's tool set — it has no Grep/Glob/Bash/Web.)
- **Every handoff is mediated by you.** Sub-agents cannot spawn sub-agents, so all artifacts pass
  **through the orchestrator**: the researcher returns a digest → you save it → you pass it to the
  thinker; the coder returns code → you run the tests → you brief the judges. There are no direct
  agent-to-agent channels.
- **PRD-derived tests are frozen by hash.** Once `np-coder` materializes a phase's tests (tests-first),
  you record their paths **and content hashes** in `state.build.phases[k].frozenTests` and never let an
  implementing coder touch them. On every iteration you verify the freeze with `git diff` over those
  paths; a violation means revert + escalate (see `loop.md`).
- **You compute `decision()` deterministically.** The PASS / REFINE / STOP_* / ESCALATE verdict at the
  end of each iteration is computed by **you** from numbers (test results, weighted score, iteration
  count, score history) — never by an LLM. The judges only score; the gate logic is code-like and lives
  in `loop.md`.
- **Greenfield-git.** At INTAKE: if there is no repository, `git init`. If `git rev-parse HEAD` fails
  (zero commits), set `state.json.baseCommit = 4b825dc642cb6eb9a060e54bf8d69288fbee4904` (the empty-tree
  hash) so the dashboard's **«Изменения»** tab diffs against an empty baseline from commit zero.
- **Russian artifacts, English instructions.** Everything human-facing — the PRD, the phase plan, the
  dashboard, gate texts, judge summaries, the product's knowledge base — is **Russian**. These
  skill/agent instruction files stay **English**.
- **Keep the dashboard the source of truth for the human.** After every stage/iteration, rewrite
  `dashboard.json`. Status is `working` while you act and `awaiting-batch` while parked at a gate or
  escalation.
- **Headless / eval mode** (`--eval` argument or `AIPF_EVAL=1`): skip the two human gates (auto-approve
  the PRD and the phase plan), consume any pre-seeded `submissions/`, and cap the build loop hard (≤2
  iterations/phase, auto-resolve escalations to "accept best"). This lets the whole workflow run
  unattended for benchmarking.

## Telemetry (automatic)

Bundled hooks record the workflow shape to `.workflow/tasks/<slug>/telemetry.jsonl` — a span per session
and per sub-agent (parallel sub-agents are siblings: the branching view), keyed so a task is one trace
(trace id = slug). You don't manage this; just keep `state.json.phase` current and `active.json` fresh
(step 1) so events are tagged with the right stage and task. In BUILD, tag spans with
`workstream=<phase id>` and `iteration=<number>` so the trace separates phases and iterations. The
companion server forwards to Langfuse when `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set, and
stays local-only otherwise. At each **stage boundary and gate** emit an explicit marker:
`POST /telemetry` (`{slug, event: "phase"|"gate", phase, iteration}`).

When in doubt about a stage's mechanics, open the matching reference file above and follow it.
