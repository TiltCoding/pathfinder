# `state.json` — resumable workflow state

One file per task at `.workflow/tasks/<slug>/state.json`. You read it at the start of every `/improve`
invocation to **resume** exactly where you left off, and you rewrite it whenever something meaningful
changes (phase transition, new iteration, a consumed submission, a consensus result, a dispatched run).
The base shape below is shared with `/feature`; the improve-specific fields are additive (see the last
section) — a single-task `/feature` run never has to carry them.

```json
{
  "slug": "improve-dashboard",
  "title": "Аудит и улучшение дашборда",
  "phase": "PROPOSE",
  "iteration": 1,
  "checkpoint": "awaiting-batch",
  "createdAt": "2026-06-13T10:00:00",
  "updatedAt": "2026-06-13T10:40:00",
  "questions": [
    { "id": "feat-1", "text": "Кэшировать /changes?", "answer": "Делаем" }
  ],
  "answers": [],
  "subagents": [
    { "type": "wf-improver", "mode": "vote", "bg": false, "startedAt": "..." }
  ],
  "baseCommit": "8e53e98eeeae88a5e6b4c85e857340e83264ea2c",
  "lastSubmission": 1,
  "lastSignalCount": 0,
  "lastChatTs": "2026-06-13T10:35:00",
  "lang": "en",
  "serverPort": 8473
}
```
The improve-specific fields (`prisms`, `candidates`, `votes`, `selected`, `dispatched`) extend this —
see the section below.

Field notes:

- **`phase`** / **`checkpoint`**: `checkpoint` is `working` while you act and `awaiting-batch` while
  parked waiting for a human batch. Together with `phase` they tell a resumed session what to do next.
- **`workstreams[]`**: unused by `/improve` (it produces feature runs, not work-streams). Track the
  improve progress with the improve-specific fields below instead.
- **`lastSubmission`**: the highest `submissions/<n>` you have already consumed. Compare against
  `submit.flag.latest` to detect a new batch.
- **`lastSignalCount`**: how many `signals.json` entries you have already accounted for. Serves as the
  `/wait` long-poll baseline (`sinceSignal`) and keeps you from re-processing old signals.
- **`baseCommit`**: the git `HEAD` captured at INTAKE. The companion server diffs the working tree
  against it to populate the **Changes** tab (`/changes`). Absent in non-git projects — the server
  then falls back to `HEAD`.
- **`lang`**: the resolved run language (`"en"` | `"ru"`). **The human's request language wins** —
  auto-detect it at INTAKE; fall back to `~/.claude/ai-pathfinder/settings.json` (graceful → `"en"`)
  only when there is no human request (autonomous/eval runs). It is the language for **all human-facing
  output**: terminal narration, candidate texts, dashboard, gate cards, choice labels, chat
  (`chat.jsonl`) and `replies.json`. `docs/knowledge/**` and git commit messages stay English regardless
  (unless the human explicitly asks otherwise). Pass it to sub-agents in their spawn prompt.
- **`worktreePath`** (optional): the absolute path of a task's own git working tree. The `/improve`
  audit task is read-only and does **not** get a worktree, so this is absent on the audit's own
  `state.json`; it is instead set on each drained **`/feature`** task's `state.json` (every feature runs
  in its own worktree — see `parallel.md`), where the server diffs that feature's **Changes** tab
  against the worktree. Written by `scripts/worktree.py` (append-only); read by the server.
- **`branch`** (optional): the git branch a worktree is checked out on (`<slug>`). Set alongside
  `worktreePath` by `scripts/worktree.py` on the drained feature tasks; absent on the read-only audit
  task. Old tasks without either field stay fully compatible.
- **`lastChatTs`**: timestamp of the last `chat.jsonl` message you have already read/answered. On each
  checkpoint wake-up you reply to messages newer than this, then advance it (see `feedback-loop.md`).
- **`subagents`**: lightweight record of what you spawned (the scout fan-out and the voter panel, some
  possibly backgrounded) so a resumed session knows what is in flight.
- **`questions`**: at the SELECT GATE the `feat-K` choices land here with the resolved `answer` once
  picked — this is your record of which features the human chose (mirror notable decisions to the
  knowledge base as ADRs).

On resume: load this file; if `checkpoint === "awaiting-batch"`, re-check `submit.flag`/`signals.json`
before doing anything else; otherwise continue the current `phase` from where the improve-specific
fields indicate (which stage produced the latest `candidates`/`votes`/`selected`/`dispatched`).

## improve-specific fields (additive)

`/improve` carries a few extra fields on the same `state.json`. They are all **append/extend-only**
(invariant "add, don't rewrite" — `conventions.md`); a `/feature` task simply never has them, so the
two scenarios stay compatible. Write each one as the matching stage produces it.

```json
{
  "phase": "PROPOSE",
  "prisms": ["UX/product", "performance", "reliability", "tech-debt", "DX", "functionality gaps", "accessibility+security"],
  "candidates": [
    { "id": "cand-1", "title": "Cache /changes", "prism": "performance",
      "problem": "Full git diff on every request (server.py:312).",
      "change": "Cache by baseCommit+mtime, TTL 2s.",
      "areas": ["scripts/server.py"], "size": "S", "risk": "low" }
  ],
  "votes": [
    { "candId": "cand-1", "impact": 2.3, "effort": 1.0, "risk": 0.7, "confidence": 2.7, "keep": 1.0, "score": 1.62 }
  ],
  "selected": ["feat-1", "feat-3", "feat-4"],
  "dispatched": [
    { "slug": "cache-changes-endpoint", "featId": "feat-1", "candId": "cand-1",
      "briefPath": ".workflow/tasks/cache-changes-endpoint/brief.md", "status": "queued" }
  ]
}
```

- **`prisms`** — the scout prisms used this run (seeded at INTAKE; one scout per prism in SCOUT). Lets a
  resumed session know which prisms were already surveyed.
- **`candidates`** — the consolidated, deduplicated list after CONSENSUS consolidation
  (`{id, title, prism, problem, change, areas[], size, risk}`), with stable `cand-K` ids. The voters and
  the gate cards key off these ids — keep them constant.
- **`votes`** — the **aggregate** per candidate after the deterministic aggregation
  (`{candId, impact, effort, risk, confidence, keep, score}` — means over the 3 voters, plus the
  computed `score`; see `consensus.md` §aggregation). The raw per-voter output stays in scratch files;
  state holds the aggregate that drove the ranking.
- **`selected`** — the feature ids the human picked at the SELECT GATE (`feat-K`). Filled when
  `approve-plan` arrives; the top-K candidates map `cand-K → feat-K` in ranked order.
- **`dispatched`** — one entry per **queued** feature
  (`{slug, featId, candId, briefPath, status}`), appended as DISPATCH writes each item into
  `.workflow/dispatch-queue.json` (the project-level queue is the source of truth for the drain; this
  mirror is for the DONE summary). No `worktreePath` in this mirror — the drained `/feature` stands up
  its own worktree off `baseCommit` and records it in that feature's own `state.json`, not here. See
  `dispatch-queue.md`.

## Related files (not part of `state.json`)

- **`.workflow/active.json`** — `{ slug, updatedAt }`, rewritten on every start/resume. Lets the
  telemetry hooks map a Claude Code session to the active task for session-level events.
- **`.workflow/active/<session_id>.json`** — `{ slug, sessionId, updatedAt }`, a per-session pointer
  the orchestrator additionally writes when running in parallel (see `parallel.md`). With a shared
  store the single `active.json` is overwritten by concurrent sessions, so session-level events would
  be attributed to the wrong task; the per-session file keys attribution by `session_id`. The hook's
  `active_slug` prefers it when present and falls back to `active.json` (then the newest `state.json`)
  otherwise, so single-task runs are unaffected.
- **`.workflow/tasks/<slug>/telemetry.jsonl`** — append-only event log written by the hooks (and any
  `POST /telemetry` markers): one line per `session.start|session.end|turn.stop|subagent.start|
  subagent.end|file.touch`. The **trace id is the slug**, so a task is one Langfuse trace across
  sessions. `telemetry.cursor` records how many lines the server has already forwarded, and
  `telemetry.enriched.json` tracks which sub-agent spans have had their token usage back-filled to
  Langfuse from transcripts. All are gitignored under `.workflow/`; you don't edit them by hand.
