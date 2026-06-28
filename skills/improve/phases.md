# Phases — what to do in each stage

Each stage ends by updating `state.json` (phase, iteration, checkpoint, improve-specific fields) and
`dashboard.json`. Spawn sub-agents with the Agent tool; pass them the slug and the absolute workspace
path. Keep the human's dashboard truthful at all times. The deep mechanics of CONSENSUS and DISPATCH
live in `consensus.md` — this file is the stage map that calls into it.

## 1. INTAKE

Goal: capture what we are auditing and stand up the workspace.

- Write `brief.md` from `templates/artifacts/brief.md`: the audit goal (what part of the app, what we
  want out of it), scope/non-scope, constraints (e.g. "only the dashboard", "no server changes"), and
  what the user already specified. Ask the user only for blockers you truly cannot infer — keep it
  light; this is an audit, not a single task, so the brief is a frame, not a spec.
- Create `state.json` (see `state-schema.md`) with `phase: "INTAKE"`, `iteration: 0`. In a git repo,
  record `baseCommit` = `git rev-parse HEAD`. Seed `prisms[]` with the default prism list (below) — or
  the subset the brief constrains you to.
- **Resolve the run language** — **the human's request language wins.** Auto-detect the language of the
  human's request and record it in `state.json` as `lang`; fall back to the global setting
  `~/.claude/ai-pathfinder/settings.json` (`{"lang":"en"|"ru"}`; graceful → `"en"`) **only** when there
  is no human request (autonomous/eval runs). Pass `lang` to every sub-agent in its spawn prompt — it is
  the output language for all human-facing output (terminal narration, candidate texts, dashboard,
  chat/replies). `docs/knowledge/**` and git commit messages stay English regardless (unless the human
  explicitly asks otherwise).
- Start the companion server and copy the dashboard (see `feedback-loop.md`). Write the first
  `dashboard.json` (summary from the brief, status `working`) and give the user the URL.
- Advance to SCOUT.

## 2. SCOUT (autonomous)

Goal: survey the app from every prism and gather raw improvement candidates.

- Spawn **7 `wf-improver` agents in scout mode in parallel** — **one per prism**. The default prisms:
  1. **UX/product** — usability, flows, missing affordances, friction.
  2. **Performance** — latency, big-O traps, wasteful work, payload size.
  3. **Reliability/resilience** — error handling, edge cases, failure modes, recovery.
  4. **Code quality/tech-debt** — duplication, coupling, dead code, refactor opportunities.
  5. **DX** — developer experience: build/test/run ergonomics, docs, scripts.
  6. **Functionality gaps** — missing features users would reasonably expect.
  7. **Accessibility + security** — a11y gaps and security exposure (one combined prism).
  (Narrow the set if the brief constrains scope; keep them disjoint so scouts don't overlap.)
- Each scout **reads `docs/knowledge/INDEX.md` first** (reuse-first), then surveys the code from its
  prism, and returns a set of candidates in the structured per-candidate schema from `agents/wf-improver.md`
  (title / prism / problem with `path:line` / change / areas / size / risk / impact / rationale).
- You write each scout's raw output to `scout/<prism>.md` (one file per prism). Don't dedup yet — that
  is CONSENSUS.
- **Make the swarm visible (feat-4).** Before the fan-out, seed `dashboard.json.workstreams[]` with one
  entry per prism — `{ "title": "<prism>", "status": "in_progress" }` — and set `now` to e.g. "рой
  сканирует призмы (0/7)". As each scout returns, flip its entry to `"status": "done"` and bump the
  `now` count. The dashboard renders these as the **swarm grid** (one card per prism, running→done) plus
  the sidebar segment track + count chip — no extra fields, the existing `workstreams[]` render does it.
- Update `dashboard.json` (the swarm grid above is the "swarm in progress" view), set
  `state.json.phase = "SCOUT"`, advance to CONSENSUS.

## 3. CONSENSUS (autonomous)

Goal: turn the raw scout output into a ranked, deduplicated, top-K shortlist.

- The full mechanics are in `consensus.md`: consolidate + **dedup** the scout candidates into stable
  `cand-1…cand-N` (`candidates.md`); spawn **3 `wf-improver` voters in parallel** (each sees the whole
  list, scores `impact/effort/risk/confidence` + keep/drop); then **you aggregate the scores
  deterministically** (the formula in `consensus.md` §aggregation) and sort to **top-K = 6–8**.
- Record `candidates[]`, `votes[]` (the aggregate per candidate), and the chosen top-K in `state.json`.
- **Make the vote panel visible (feat-4).** When you fan out the voters, rewrite
  `dashboard.json.workstreams[]` as the 3 voters — `{ "title": "voter 1", "status": "in_progress" }`,
  flipping each to `"done"` as its ballot lands — and set `now` to e.g. "панель из 3 голосующих
  оценивает N кандидатов" (the candidate count makes the consolidation legible). The dashboard shows
  the vote panel as the swarm grid + the candidate count in the «Сейчас» line.
- Advance to PROPOSE/SELECT GATE.

## 4. PROPOSE / SELECT GATE (the one human gate)

Goal: present the top-K and let the human pick which features to dispatch. This is a batched-feedback
loop, exactly like `/feature`'s plan gate — but the gate is **feature-pick**, not plan-approve.

- Render the top-K into `dashboard.json` using the **feat-K contract** (see `dashboard-guide.md`
  §SELECT GATE): for each candidate `K`, write one `planBlocks[]` card and one
  `questions[kind:"choice"]`, **both with the same `id = feat-K`** and a two-option choice in the active
  dashboard language — `options:["Do","Skip"]` (en) or `options:["Делаем","Пропускаем"]` (ru).
  The card `body` (markdown) carries: prism / problem / proposed change / size·risk·impact / affected
  files (clickable paths), **plus an obligatory one-line ranking from `state.json.votes[]`** — the
  compact form `score X.XX · agreement N% · impact·effort·risk a·b·c` (numbers only, no vote-note;
  translate the label to the active language) — so
  the human sees how the panel scored each feature and the gate is not a black box. Set status
  `awaiting-batch`.
- Also write **one** extra `questions[kind:"choice"]` with the stable `id = "drain-mode"` (not tied to
  any `feat-K`): text "Drain the picked features autonomously (no per-feature plan gate; the agent
  self-resolves open questions)?", `options:["Автономно","Вручную (как сейчас)"]`. The default is
  **«Вручную»** (no answer ⇒ manual) — the safe, back-compatible behavior. This single choice flips the
  whole queue to autonomous at DISPATCH; the drainer's contract is in `dispatch-queue.md`
  §"Autonomous drain (opt-in)".
- In the `summary`, tell the human the contract in the **run language `state.json.lang`** (the human's
  request language): pick the **Do / Skip** choice per feature
  (or type a free-form note like "do it, but without X"), then **Submit** to record the choice, then
  **Approve plan** to dispatch the picked ones. State the defaults explicitly: **no answer = Skip**, and
  the order **Submit → Approve** is required (the draft is not readable before submit). Also explain the
  **`drain-mode`** choice: «Автономно» drains the whole queue without parking at each feature's plan gate
  (the agent self-resolves open questions, recorded with rationale), while «Вручную» (the default) keeps
  today's behavior — every item parks at its PLAN GATE. The choice option
  labels and gate texts you write into `dashboard.json` must match the dashboard's active language
  (these are UI/content in the global default, not a reply to the human).
- Park at the checkpoint and wait (see `feedback-loop.md`). On a new `submissions/<n>.json`: read every
  comment/answer, refine the cards if the human pushed back (bump `iteration`, re-park), and write a
  short `replies.json` entry per item keyed by `feat-K`. A free-form `answer.text` outside the options
  is valid (ADR-0008) — read it as a refinement to that feature's brief.
- When the human clicks **«Утвердить план»** (an `approve-plan` signal): take the **latest** submission,
  collect every `feat-K` whose answer is «Делаем» (or a free-form "делаем…"). Treat any `feat-K` with no
  answer as **Пропускаем**. Record the picked ids in `state.json.selected[]`, and advance to DISPATCH.
- In headless/eval mode: skip waiting; auto-pick the top-K (or apply any pre-seeded submissions);
  auto-approve.

## 5. DISPATCH (autonomous)

Goal: queue each picked feature for a sequential `/feature` drain, then hand the drain to the human.
The picks are drained one at a time, each in a fresh context — but each drained `/feature` stands up its
**own worktree off `baseCommit`** (per `parallel.md`), so the features land on independent branches, not
stacked on one. You (the `/improve` writer) just don't create those worktrees yourself.

- The exact writer-side sequence is in `consensus.md` §DISPATCH and the full contract in
  `dispatch-queue.md` (per feature: fresh slug → write `brief.md` → append a `pending` item to
  `.workflow/dispatch-queue.json`). You create **no** worktree here (the drainer does) and seed **no**
  per-feature `state.json`/`dashboard.json`/`index.html` — the `/feature` drainer makes its own workspace.
- For each queued feature, append a `dispatched[]` entry to this task's `state.json`
  (`{slug, featId, candId, briefPath, status:"queued"}`).
- If the human answered the `drain-mode` choice with **«Автономно»**, stamp top-level `autonomous:true`
  onto `.workflow/dispatch-queue.json` (alongside `mode`/`source`). Absent or «Вручную» ⇒ leave it off
  (manual drain). The drainer reads this back per the contract in `dispatch-queue.md`
  §"Autonomous drain (opt-in)" — don't restate the drain behavior here.
- **Do not run `/feature` yourself** (it would pollute this context). Advance to DONE.

## 6. DONE

- Write a final summary into `dashboard.json`: a card per queued feature (slug / title / prism) in
  ranked order, plus a link to the hub (`/hub`), plus the **drain instructions**: run **`/feature`** to
  start the first item; when it finishes, **`/clear`** then **`/feature`** for the next (or **`/loop
  /feature`** to auto-continue). Tell the human the same in chat: the queue is written, each `/feature`
  run does one feature with a clean context and marks the queue item done. See `dispatch-queue.md`.
- Spawn `wf-documenter` to grow `docs/knowledge/` (see `knowledge-guide.md`): the task-log entry, any
  ADR for a notable decision, and the index refresh. It is a peer step, not an afterthought.
- Set `phase: "DONE"` in `state.json` and set the dashboard status appropriately. Point the human at the
  dashboard, the hub, and the updated knowledge base.
