# Stages — what to do in each one

The greenfield stage machine: `INTAKE → DISCOVER → PRD → PRD-GATE → PHASE-PLAN → PLAN-GATE → BUILD →
SHIP → DONE`. Each stage ends by updating `state.json` (phase, iteration, gates, phases/work-streams)
and `dashboard.json`. Spawn sub-agents with the Agent tool; pass them the slug, the absolute workspace
path, and **only** the curated inputs they need (see SKILL.md → Operating rules). The model is pinned
in each agent's frontmatter — you pick the `subagent_type`, not the model. Keep the human's dashboard
truthful at all times.

At every **stage boundary and gate**, emit `POST /telemetry {slug, event: "phase"|"gate", phase,
iteration}` (gate policy here is **V1**: two human gates, autonomous build loop).

## 1. INTAKE

Goal: capture the pitch and stand up the workspace on a blank slate.

- Capture the user's product pitch (the one-paragraph "what & why"). Ask the user only for blockers you
  truly cannot infer — deeper elicitation is DISCOVER's job.
- **Greenfield-git.** Decide where the product will live, then prepare the repo:
  - **Scaffold location:** if the current repo root is effectively empty, build at the
    **root**; otherwise build in a subdirectory `./<slug>/`. If it's genuinely ambiguous, ask the user
    here. Record the answer in `state.json.projectRoot`.
  - If there is no git repository, `git init`. Then capture the baseline: `baseCommit = git rev-parse
    HEAD`, **but** if that fails because there are zero commits, set `baseCommit =
    4b825dc642cb6eb9a060e54bf8d69288fbee4904` (the empty-tree hash) so the **Changes** tab diffs
    from commit zero. (See `state-schema.md` for the full rule.)
- **Read the global language setting** from `~/.claude/ai-pathfinder/settings.json`
  (`{"lang":"en"|"ru"}`; graceful → `"en"` on any error/missing/unknown value). Record the resolved
  language in `state.json` as `lang`, and pass it to every sub-agent in its spawn prompt — it is the
  **default** output language for the PRD/plan/dashboard/knowledge/commits/README (chat/reply channels
  still follow the human's message language).
- Create `state.json` (see `state-schema.md`) with `phase: "INTAKE"`, `iteration: 0`, `projectRoot`,
  `lang`, and `baseCommit`.
- Start the companion server and copy the dashboard (see `feedback-loop.md`). Write the first
  `dashboard.json` (summary from the pitch, status `working`) and give the user the URL.
- Advance to DISCOVER.

## 2. DISCOVER (autonomous, parallel)

Goal: turn a vague pitch into answered questions + a compressed outside-view digest.

- **Spawn two agents in parallel, mediated by you** (they cannot talk to each other):
  - `np-thinker` (fable) — **elicitation**: produce **≤3 questions per round** of the highest-leverage
    unknowns plus a first cut of **Assumptions**. You render its questions into `questions[]` on the
    dashboard.
  - `np-researcher` (opus) — **outside view** on goal / format / boundaries (domain, analogues, likely
    stack, constraints). It returns a **digest** built on `templates/artifacts/research-digest.md`
    (TL;DR decisions on top, facts as `[source — one fact]`, pre-decided vs open). You save it to
    `<task>/research/digest-1.md` — never pass the raw research to the thinker, only this digest.
- Park at the checkpoint and wait for the human's answers (see `feedback-loop.md`). **At most 2 rounds**
  of questions; if more research is needed between rounds, re-spawn `np-researcher` and append
  `digest-2.md`. Any question the human leaves unanswered becomes an explicit **Assumption** (the
  thinker folds it in) rather than a blocker.
- When elicitation has converged, advance to PRD.

## 3. PRD (`np-thinker`)

Goal: a machine-readable product requirements document.

- Spawn `np-thinker` (fable) with the curated inputs only: the pitch, the human's answers, and the
  research digest(s). It writes `prd.md` from `templates/artifacts/prd.md`.
- **Lean-adaptive depth:** the **FR table** (`FR-ID | statement | Given-When-Then | priority |
  phase | test`) and **Assumptions** are mandatory always; the thinker expands the other sections
  (NFR, success metrics, risks, scope tiers) in proportion to the product's complexity. **Non-goals**
  are explicit.
- Render the PRD's sections into `planBlocks[]` (one block per section; ids `prd-*` / `fr-*`) so the
  human can comment block-by-block. Set status `awaiting-batch` and move to PRD-GATE.

## 4. PRD-GATE (human gate #1)

Goal: converge on a PRD the human approves. An iteration loop driven by batched feedback.

- Park and wait (see `feedback-loop.md`): the human comments on PRD blocks and answers any open
  questions, then clicks **«Отправить агенту на доработку»**.
- On a new `submissions/<n>.json`: read every item, have `np-thinker` revise `prd.md`, write a short
  `replies.json` entry per item (keyed to the block/question id), bump `iteration`, refresh
  `dashboard.json`, park again.
- Repeat until the human clicks **«Утвердить план»**. Here `approve-plan` means **"the PRD is
  approved"** (the signal is interpreted by current stage — see `feedback-loop.md`). Freeze `prd.md`,
  record `prd.approved = true` and `prd.frIds[]` in `state.json`, emit the `gate` telemetry marker, and
  advance to PHASE-PLAN.
- In headless/eval mode: skip waiting, auto-apply pre-seeded submissions, auto-approve.

## 5. PHASE-PLAN (`np-thinker`)

Goal: decompose the approved PRD into buildable vertical slices (phases) with their own exit tests.

- Spawn `np-thinker` (fable) with the frozen PRD. It writes `phase-plan.md` from
  `templates/artifacts/phase-plan.md`:
  - **Ф0 = walking skeleton** (the thinnest end-to-end slice that runs), then vertical feature slices
    ordered by **dependency + risk** (riskiest-useful first). Aim for ~3–6 phases for an MVP.
  - **Per phase:** goal, the **FR-IDs** it satisfies, an **exit checklist** (tests green ✓ / judge ≥
    threshold ✓ / FRs traceable to tests ✓ / slice demoable ✓ / no blocking issues ✓), a **test spec**
    (each GWT → concrete cases), a **judge rubric** (3 dimensions × weights × scale 0–3, names chosen
    per phase, `PASS_THRESHOLD = 80/100`), and an **iteration budget** (default ≤5).
- Render the phases into `planBlocks[]` (ids `phase-*`). Set status `awaiting-batch` and move to
  PLAN-GATE.

## 6. PLAN-GATE (human gate #2)

Goal: converge on a phase plan the human approves.

- Same loop as PRD-GATE: batched comments → `np-thinker` revises `phase-plan.md` → per-item
  `replies.json` → park. Here `approve-plan` means **"the phase plan is approved"**.
- On approval: freeze `phase-plan.md`, write `build.phases[]` into `state.json` (each phase id/title/
  status `todo`, with its FR-IDs, exit criteria, test spec, rubric, budget — see `state-schema.md`),
  write `workstreams[]` (= the product phases) and `progress` (phases done/total) into `dashboard.json`,
  emit the `gate` marker, and advance to BUILD.
- In headless/eval mode: auto-approve as above.

## 7. BUILD (autonomous)

Goal: build the product phase by phase with the evolutionary loop.

- Run the loop in **`loop.md`** for each phase **strictly in order** (Ф0 first). For each phase: a
  tests-first pre-loop (freeze the tests), then iterations of *implement → freeze-check → run tests →
  judge → `decision()`*, until PASS / STOP_* / ESCALATE.
- **Gate policy V1: transitions between phases are autonomous** — when a phase PASSes you commit it,
  mark it `done`, bump `progress`, and move straight to the next phase **without a human gate**. The
  loop only parks for a human when a stop-condition (budget / plateau / oscillation) fires, as a
  choice-question (see `loop.md` §STOP / `feedback-loop.md`).
- Keep `state.build.currentPhase` / `iteration` and the dashboard (`workstreams`, `progress`,
  `iteration` badge, `summary` score-trend) current so a resumed session re-enters on the right
  iteration boundary. Tag BUILD telemetry with `workstream=<phase id>` and `iteration=<number>`; emit a
  `phase` marker at each phase boundary.
- When the last phase has PASSed, advance to SHIP.

## 8. SHIP (autonomous, with final human acceptance)

Goal: confirm the whole product satisfies the PRD, document it, and hand off.

- **Full test run:** run the entire accumulated test suite (all phases) and capture the result.
- **Holistic judgement:** spawn `np-judge` (opus) once over the whole product **vs the PRD** —
  end-to-end FR tracing (every `FR-ID` → a satisfying test/behavior), not a per-dimension phase score.
  Record it in `reviews.json` (`kind: "judge"`).
- **Optional review gates:** the human may click **`run-code-review`** / **`run-security-review`**;
  honor them via `wf-reviewer` and append entries to `reviews.json` (see `feedback-loop.md`). (Skipped
  in headless/eval mode.)
- **Product README:** write a README for the product (what it does, how to run, the FR coverage) in the
  **global default language** (`~/.claude/ai-pathfinder/settings.json`, default English).
- **Knowledge base:** spawn `wf-documenter` (reused) to grow the **product's own**
  `docs/knowledge/` — area docs, ADRs for the notable build decisions, `INDEX.md`, the root `CLAUDE.md`
  pointer (see `knowledge-guide.md`).
- **Final acceptance (human):** park for the human to accept the shipped product (an `approve-plan`
  signal at this stage = "product accepted"). In headless/eval mode, auto-accept.
- Advance to DONE.

## 9. DONE

- Write a final summary into `dashboard.json` (what was built, where it lives, how it was verified,
  follow-ups) and set status appropriately. Set `phase: "DONE"` in `state.json` and emit the final
  `phase` telemetry marker.
- Tell the user what landed and point at the dashboard, the product README, and its knowledge base.
