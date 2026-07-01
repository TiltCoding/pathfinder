# Phases — what to do in each one

Each phase ends by updating `state.json` (phase, iteration, checkpoint, work-streams) and
`dashboard.json`. Spawn sub-agents with the Agent tool; pass them the slug and the absolute
workspace path. Keep the human's dashboard truthful at all times.

## 0. TRIAGE (fast-lane gate — runs before INTAKE)

Goal: spend ceremony in proportion to the task. This skill's machinery — server, dashboard,
sub-agent swarm, plan gate, review gates — exists for *substantial* work. A primitive task should
not pay for it, and a human who asked for a one-line fix should not wait through it.

After you've resolved the workspace and stood up the worktree (SKILL step 1), judge the task against
the **primitive** bar. A task is **primitive** only when **all** hold:

- **Single area** — confined to one module/subsystem; it doesn't ripple across several or touch shared
  architecture. *Judge by scope, not file count:* a tidy edit spanning a few files inside one area is
  still primitive, while a one-file change that forces matching changes in callers across modules is
  **not**.
- **No new functionality** — a fix, tweak, or adjustment to existing behavior; you are **not** adding a
  new feature, capability, endpoint, or surface area.
- **Trivial verification** — "done" is obvious and cheap to confirm (an existing test, a quick run); it
  does **not** need a new or multi-step verification strategy (new test scaffolding, manual UI driving,
  cross-component checks).
- **No decision for the human** — there are no open questions; you are not choosing between designs or
  trade-offs that the human would want a say in.
- **Low risk** — not destructive/irreversible; not touching security, auth, money, data migrations,
  or a public API/contract.
- **You can do it directly** — you're confident you can implement and verify it without exploration.

If **any** bar fails, or you are unsure → **full workflow** (INTAKE onward). When in doubt, take the
full lane: an unneeded plan gate costs minutes, but powering a real feature through the fast lane
costs a botched change.

### Fast Lane

Record `lane: "fast"` in `state.json` (so a resume stays on it), `phase: "IMPLEMENT"`. Then:

- **Keep only the worktree** from SKILL step 1 — the cheap, idempotent isolation that protects the
  shared tree. **Skip** the companion server + dashboard (SKILL steps 2–3), EXPLORE, ELABORATE, the
  PLAN GATE, the parallel coder/documenter swarm, and the heavy `/code-review` + `/security-review`
  gates.
- **Do the work directly.** The "you don't write production code yourself" rule is relaxed on this
  lane: the orchestrator edits the files itself — no `wf-coder` round-trip. Still **read
  `docs/knowledge/INDEX.md` first** and match existing patterns, as always.
- **Verify before claiming done.** Run the tests/build/lint for the touched slice and confirm green
  (evidence, not assertion). A quick self-review of the diff replaces the review-gate skills.
- **Capture lightly.** Append a one-line `docs/knowledge/task-log.md` entry only if the change is
  notable; otherwise skip the documenter entirely. No ADR for a primitive change.
- **Report in chat** (there is no dashboard): what changed, the files as clickable `path:line`, and
  how you verified. Set `phase: "DONE"`, `lane: "fast"` in `state.json`.

### Escalation valve (one-way: fast → full)

The fast lane is a bet that the task is small. The moment the bet breaks — the change spreads across
modules, it turns out to add real new functionality, it needs nontrivial verification, a real design
choice surfaces, you hit ambiguity, or you discover risk — **stop and promote to the full workflow**:
stand up the server + dashboard (SKILL steps 2–3), set `lane: "full"`, and run EXPLORE → ELABORATE →
PLAN GATE before going further. Tell the human you escalated and why. Never silently grind a now-complex
task through the fast lane.

> Queue mode and autonomous/eval runs still triage — a primitive queued item may take the fast lane —
> and the DONE bookkeeping in §7 (mark the queue item `done`, etc.) still applies to whichever lane runs.

## 1. INTAKE

Goal: capture the task and stand up the workspace.

- **Queue mode (a `/improve` drain — see SKILL step 0):** if you popped an item from
  `.workflow/dispatch-queue.json`, the **brief already exists** at the item's `briefPath` — read it,
  do **not** re-elicit, and use the queue's `baseCommit`. Then go straight to EXPLORE. The rest of this
  step is for a normal, human-initiated task.
- Write `brief.md` from `templates/artifacts/brief.md`: title, goal, scope/non-scope, constraints,
  acceptance criteria, anything the user already specified. Ask the user only for blockers you truly
  cannot infer — keep it light; deeper questions come out of EXPLORE.
- Create `state.json` (see `state-schema.md`) with `phase: "INTAKE"`, `iteration: 0`. In a git repo,
  record `baseCommit` = `git rev-parse HEAD` so the dashboard's **Changes** tab can diff the task's
  work against the starting point.
- **Resolve the run language** — **the human's request language wins.** Auto-detect the language of the
  human's request and record it in `state.json` as `lang`; fall back to the global setting
  `~/.claude/ai-pathfinder/settings.json` (`{"lang":"en"|"ru"}`; graceful → `"en"`) **only** when there
  is no human request (autonomous/eval runs). Pass `lang` to every sub-agent in its spawn prompt — it is
  the output language for all human-facing output (terminal narration, artifacts, dashboard,
  chat/replies). `docs/knowledge/**` and git commit messages stay English regardless (unless the human
  explicitly asks otherwise).
- Start the companion server and copy the dashboard (see `feedback-loop.md`). Write the first
  `dashboard.json` (summary from the brief, status `working`) and give the user the URL.
- **Stand the task up in its own git worktree at this point** (always — see `parallel.md`):
  `worktree.py add <slug>` records `worktreePath`/`branch` in `state.json`, symlinks the shared store,
  and gives the task an isolated branch so it never collides with another task's files or branch. In
  queue mode pass `--base <baseCommit>`. (Skip only outside a git repo.)
- Advance to EXPLORE.

## 2. EXPLORE (autonomous)

Goal: understand the relevant code before planning.

- Spawn one to three `wf-explorer` agents in parallel. Split by area when the scope is broad
  (e.g. one for the data layer, one for the API, one for tests/build). For a narrow task, one is enough.
- Each explorer **reads `docs/knowledge/INDEX.md` first** (if present) to reuse prior knowledge, then
  fills the gaps by searching the code, and writes/extends `exploration.md` (RU): relevant files with
  clickable paths, entry points, existing patterns to reuse, constraints, risks, and **open questions**.
- Merge their findings into `exploration.md`, update `dashboard.json` (Карта кодовой базы), advance.

## 3. ELABORATE

Goal: turn understanding into a concrete, reviewable plan plus the questions you need answered.

- Spawn `wf-planner` with the brief + exploration. It produces a draft `plan.md` broken into
  **blocks** (each with a stable `id` like `b1`, `b2`) and a list of **open questions** in
  `questions.md` (each with a stable `id` like `q1`; mark choice-questions with options).
- When it helps the human decide, the planner also produces a **visual demo** of the solution: 2–3
  self-contained HTML/SVG variants in `.workflow/tasks/<slug>/mockups/` (UI mockups for UI tasks, a
  diagram/infographic for backend/CLI) plus a `demo` render model. It's optional — skip for trivial tasks.
- Render everything into `dashboard.json`: `planBlocks[]`, `questions[]`, and `demo` (if any). Set
  status `awaiting-batch`.
- Move to the PLAN GATE.

## 4. PLAN GATE (the one human gate)

Goal: converge on a plan the human approves. This is an iteration loop driven by batched feedback.

- Park at a checkpoint and wait (see `feedback-loop.md`): the human comments on plan blocks and
  answers questions in the dashboard, then either clicks **«Отправить агенту на доработку»** (revise
  and come back) or **«Утвердить план»** to approve.
- When a new `submissions/<n>.json` appears: read every item, revise `plan.md` / questions, and write
  a short `replies.json` entry per item (reference the block/question id) so the human sees the change.
  Bump `iteration`, refresh `dashboard.json`, park again.
- A **chosen demo variant** arrives as an answer keyed to the demo's `selectionId`. Record it in
  `state.json.questions`, fold the picked design into `plan.md` (and drop the alternatives from `demo`,
  or keep `selected` set to it), and reply under the variant so the human sees it's locked in.
- **Approve absorbs the pending answers.** «Утвердить план» auto-submits any unsent answers, so an
  `approve-plan` signal often arrives **together with** a fresh `submissions/<n>.json` in the same
  `/wait` return. When both are present: **first apply that submission** (record answers, fold the
  picked variants into `plan.md`, reply as above), **then advance straight to IMPLEMENT** — do **not**
  re-park at `awaiting-batch`. Answers are inputs to the build, not a separate revision round.
- Repeat until the human clicks **«Утвердить план»** (an `approve-plan` signal). Then freeze
  `plan.md`, finalize `workstreams[]` in `state.json` (independent, parallelizable units, each with an
  id/title/status `todo`), and advance to IMPLEMENT.
- In headless/eval mode: skip waiting, auto-apply any pre-seeded submissions, auto-approve.
- In **autonomous** mode (a separate predicate from eval — see SKILL step 0): do **not** park by
  default. For each open question apply the auto-resolve policy (sensible default + the **two-tier
  escalation valve** — canonical in `../improve/dispatch-queue.md` §"Autonomous drain (opt-in)"),
  recording `answer` + `rationale` + `mode:"auto"|"escalated"|"blocked"` in `state.json.questions[]`
  and mirroring to `replies.json`; then auto-approve the plan and advance to IMPLEMENT.
  **Exception — hard block:** if a decision is irreversible / destructive / risks data loss, do **not**
  auto-approve that slice — raise the question to the human (an entry in `state.json.questions[]` plus
  an anchored `chat.jsonl` agent line with `needsAnswer:true`), park on `/wait` and wait for explicit
  human approval (the same parking machinery as the normal PLAN GATE above, enabled only conditionally),
  and continue only after the human answers.

## 5. IMPLEMENT (autonomous, with optional steering)

Goal: build the approved plan and document it as you go.

- For each work-stream spawn a `wf-coder` agent. Independent streams run in parallel; long ones use
  `run_in_background`. Give each coder its work-stream, the plan, exploration, and the convention that
  it must read `docs/knowledge/` and match existing style.
- **In parallel, spawn `wf-documenter`** to grow `docs/knowledge/` as work-streams land (see
  `knowledge-guide.md`). It is a peer to the coders, not an afterthought.
- As streams complete, mark them `done` in `state.json` and `dashboard.json` and update progress.
- Between work-streams you hit checkpoints: if the human has submitted a steering batch **or sent chat
  messages** (`chat.jsonl`, see `feedback-loop.md`), consume them (answer in chat, adjust remaining
  streams) before continuing. Otherwise proceed autonomously. Chat never interrupts a running coder —
  it is handled at the next checkpoint.

## 6. VERIFY (autonomous)

Goal: confirm the change actually works.

- Spawn `wf-reviewer`: run the project's tests/linters/build, review the diff for correctness, and
  report findings. Fix or spawn a coder to fix real issues; re-run until green or until you have a
  clear blocker to surface.
- **Review gates (auto):** after `wf-reviewer` is green, run the `/code-review` and `/security-review`
  skills over the diff as gates. Capture each run into `reviews.json` (see `feedback-loop.md` for the
  shape): set `status: "running"` **with a `startedAt`** before you invoke the skill and rewrite it to
  `done`/`failed` with a short `summary` and the ranked `findings` (severity, `file:line`, text) when it
  returns. The dashboard's **«Изменения»** tab renders these and surfaces the change diff next to them.
  Treat high-severity findings as fix-or-justify before DONE. (Only headless/eval mode skips these gates;
  in **autonomous** mode `/code-review` + `/security-review` STILL run, exactly like a normal run.)
  - **Write `reviews.json` atomically** (feat-16). Under an autonomous `/loop /feature` drain this is a
    read-modify-write into the **shared store** across sessions, so use the project's atomic writer
    (`scripts/_aipf.py` `write_json` → `atomic_write`, ADR-0021), never a raw truncate-write — a
    half-written `reviews.json` would corrupt the only quality record of the gate.
  - **Stale-`running` is a resume-invariant of VERIFY** (feat-16). The gate's pass/fail rests on
    `reviews.json`, so a `running` entry whose session died (it has a `startedAt`, no terminal status,
    and the run isn't actually in flight) must NOT be trusted as "still running" forever. On resume,
    treat a stale `running` (older than a short threshold) as **`failed`** and **re-run that review
    gate** before DONE — never reach DONE with a `running`/unresolved high-severity gate. This mirrors
    the dispatch-queue's stale-`in-progress` recovery (feat-14).
- A human can also request a re-run from the dashboard: the **`run-code-review`** / **`run-security-review`**
  signals arrive on your `/wait` baseline like any other signal — when you see one, re-run that skill
  and append a fresh entry to `reviews.json`.
- Have `wf-documenter` finalize: append the `task-log.md` entry, add an ADR for any notable decision,
  refresh `INDEX.md` and the root `CLAUDE.md` pointer.
- Update `dashboard.json` with verification status.

## 7. DONE

- Write a final summary into `dashboard.json` (what changed, where, how it was verified, follow-ups)
  and set status appropriately. Set `phase: "DONE"` in `state.json`.
- Tell the user what landed and point at the dashboard and the updated knowledge base.
- **Queue mode:** if this run drained a `.workflow/dispatch-queue.json` item, mark that item `done`
  (+`doneAt`) and bump the queue's `updatedAt`. Then tell the human how many items remain and the next
  step: **`/clear` then `/feature`** to start the next pending item in a fresh context (or **`/loop
  /feature`** to auto-continue). **Do not** start the next item in this same session — a clean context
  per feature is the point. If it was the last item, say the queue is drained and point at `/hub`.
- **Autonomous queue-mode run:** the DONE summary **lists every auto-resolved question with its
  rationale** (from `state.json.questions[]`), calling out any `blocked` / escalated decisions in a
  separate section so they are easy to spot. Since the human stepped away, **recommend `/loop /feature`**
  for the next item — while still preserving the invariant above: **do not** start the next item in this
  same session.
