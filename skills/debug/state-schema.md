# `state.json` — resumable workflow state

One file per task at `.workflow/tasks/<slug>/state.json`. You read it at the start of every `/feature`
invocation to **resume** exactly where you left off, and you rewrite it whenever something meaningful
changes (phase transition, new iteration, work-stream status, a consumed submission).

```json
{
  "slug": "add-csv-export",
  "title": "Добавить экспорт отчётов в CSV",
  "phase": "IMPLEMENT",
  "iteration": 3,
  "checkpoint": "working",
  "createdAt": "2026-06-09T22:00:00",
  "updatedAt": "2026-06-09T22:40:00",
  "questions": [
    { "id": "q1", "text": "Разделитель?", "answer": "запятая" }
  ],
  "answers": [],
  "workstreams": [
    { "id": "ws1", "title": "Сервис экспорта", "status": "done" },
    { "id": "ws2", "title": "Эндпоинт + тест", "status": "in_progress" }
  ],
  "subagents": [
    { "type": "wf-coder", "workstream": "ws2", "bg": true, "startedAt": "..." }
  ],
  "baseCommit": "8e53e98eeeae88a5e6b4c85e857340e83264ea2c",
  "lastSubmission": 2,
  "lastSignalCount": 1,
  "lastChatTs": "2026-06-09T22:35:00",
  "lang": "en",
  "serverPort": 8473
}
```

Field notes:

- **`phase`** / **`checkpoint`**: `checkpoint` is `working` while you act and `awaiting-batch` while
  parked waiting for a human batch. Together with `phase` they tell a resumed session what to do next.
- **`lane`**: `"fast"` | `"full"` — which lane the TRIAGE gate (`phases.md` §0) chose. `"fast"` means
  the primitive-task path (no server/dashboard, no sub-agent swarm, no plan gate); a resumed session
  stays on it. Promoted to `"full"` if the fast lane escalates. Absent on older tasks = treat as
  `"full"`.
- **`workstreams[].status`**: `todo` | `in_progress` | `done`. The source of truth for IMPLEMENT
  progress; mirror it into `dashboard.json.progress` and `workstreams`. Also reflect the **current
  activity** into `dashboard.json.now`/`nowAt` (a human one-liner like «пишу сервис экспорта» + ISO
  timestamp) whenever it changes, so the «Сейчас: …» header stays live — see `dashboard-guide.md`.
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
  output**: terminal narration, artifacts, dashboard, gate cards, choice labels, chat (`chat.jsonl`) and
  `replies.json`. `docs/knowledge/**` and git commit messages stay English regardless (unless the human
  explicitly asks otherwise). Pass it to sub-agents in their spawn prompt.
- **`worktreePath`**: the absolute path of the task's own git working tree. Set for **every** task now
  that each task runs in its own worktree (see `parallel.md`). The server diffs the **Changes** tab
  against this tree instead of the project root. Written by `scripts/worktree.py` (append-only); read by
  the server. Absent only outside a git repo (or for old tasks) — the server then falls back to its
  `--root` working tree.
- **`branch`**: the git branch the task's worktree is checked out on (`<slug>` by default). Set
  alongside `worktreePath` by `scripts/worktree.py`. Absent only outside a git repo or for old tasks,
  which stay fully compatible.
- **`lastChatTs`**: timestamp of the last `chat.jsonl` message you have already read/answered. On each
  checkpoint wake-up you reply to messages newer than this, then advance it (see `feedback-loop.md`).
- **`subagents`**: lightweight record of what you spawned (especially background coders) so a resumed
  session knows what is in flight.
- **`questions`**: keep ids stable and store the resolved `answer` once known — this is your record of
  decisions (also feed notable ones to the knowledge base as ADRs).

On resume: load this file; if `checkpoint === "awaiting-batch"`, re-check `submit.flag`/`signals.json`
before doing anything else; otherwise continue the current `phase` from where `workstreams` indicate.

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
